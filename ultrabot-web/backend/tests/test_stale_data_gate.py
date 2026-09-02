"""Phase 5 tests — runtime data-freshness guards (stale/delisted symbol defense).

Covers three layers added in the instrument-hygiene phase:
1. utils.market_utils.get_last_candle_age_minutes — pure helper.
2. Engine DATA_STALE_CANDLES freshness guard — during open market hours, a scanned symbol
   whose newest candle is older than stale_candle_max_age_minutes is skipped
   (delisted symbols like TATAMOTORS post the Oct-2025 demerger can still
   serve OLD history through the feed; trading on it would generate phantom
   signals at stale prices).
3. WatchlistBuilder freshness guard — stale symbols are dropped from
   candidates and cannot re-enter through the offline fallback.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import Settings
from core.engine import UltraBotEngine, EngineState
from scanner.watchlist_builder import WatchlistBuilder
from utils.market_utils import get_last_candle_age_minutes


def _mk_candles(n=30, age_minutes=None):
    """Candles with newest bar `age_minutes` old (None -> no timestamps)."""
    candles = []
    for i in range(n):
        c = {
            "open": 1000.0, "high": 1010.0, "low": 990.0,
            "close": 1005.0, "volume": 10000,
        }
        if age_minutes is not None:
            ts = datetime.now() - timedelta(minutes=age_minutes + 5 * (n - 1 - i))
            c["timestamp"] = ts.isoformat()
        candles.append(c)
    return candles


class TestLastCandleAgeHelper:
    def test_fresh_candles_small_age(self):
        age = get_last_candle_age_minutes(_mk_candles(age_minutes=5))
        assert age is not None and 4.5 <= age <= 7.0

    def test_stale_candles_large_age(self):
        age = get_last_candle_age_minutes(_mk_candles(age_minutes=60 * 24 * 400))
        assert age is not None and age > 24 * 60 * 399

    def test_empty_list_returns_none(self):
        assert get_last_candle_age_minutes([]) is None

    def test_missing_timestamp_returns_none(self):
        assert get_last_candle_age_minutes(_mk_candles(age_minutes=None)) is None

    def test_unparseable_timestamp_returns_none(self):
        candles = [{"close": 1.0, "timestamp": "not-a-date"}]
        assert get_last_candle_age_minutes(candles) is None

    def test_tz_aware_timestamp_handled(self):
        from zoneinfo import ZoneInfo
        ts = (datetime.now(tz=ZoneInfo("Asia/Kolkata")) - timedelta(minutes=5)).isoformat()
        age = get_last_candle_age_minutes([{"close": 1.0, "timestamp": ts}])
        assert age is not None and 0 <= age < 15


def _make_engine():
    cfg = MagicMock(spec=Settings)
    cfg.get_capital_config.return_value = {
        "virtual_capital": 500000.0,
        "max_capital_usage_pct": 90.0,
        "min_position_size": 5000.0,
        "max_per_position_pct": 25.0,
    }
    cfg.get_risk_config.return_value = {
        "vix_staleness_warning_seconds": 360,
        "vix_staleness_critical_seconds": 540,
        "vix_stale_floor": 22.0,
        "stale_candle_max_age_minutes": 30,
    }
    cfg.get_partial_booking_config.return_value = {}
    cfg.get_strategy_activation.return_value = {"active": [], "paused": []}

    engine = UltraBotEngine(
        config=cfg,
        repository_getter=None,
        error_engine=None,
        risk_engine=None,
        position_sizer=None,
        partial_booker=None,
        daily_risk_manager=None,
        broker_factory=None,
        feed_manager=None,
        session_manager=None,
        market_hours=None,
        ws_manager=None,
    )
    engine.state = EngineState.RUNNING
    engine.active_strategies = []
    engine._broadcast = AsyncMock()
    return engine


def _set_feed(engine, candles):
    feed = MagicMock()
    feed.get_candles = AsyncMock(return_value=candles)
    engine.feed = feed


def _stale_events(engine):
    return [
        e for e in engine._recent_scan_telemetry
        if e.get("gate") == "DATA_STALE_CANDLES"
    ]


class TestEngineStaleDataGate:
    @pytest.mark.asyncio
    async def test_stale_candles_skipped_with_stale_data_telemetry(self):
        """Market open + 400-day-old candles -> symbol skipped, DATA_STALE_CANDLES event recorded."""
        engine = _make_engine()
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True
        _set_feed(engine, _mk_candles(age_minutes=60 * 24 * 400))

        await engine._scan_symbol("TATAMOTORS", None)

        events = _stale_events(engine)
        assert len(events) == 1
        assert events[0]["symbol"] == "TATAMOTORS"
        assert events[0]["status"] == "SKIPPED"
        assert "Stale candles" in events[0]["reason"]
        assert "TATAMOTORS" in engine._stale_data_symbols_warned

    @pytest.mark.asyncio
    async def test_fresh_candles_not_blocked(self):
        """Market open + fresh candles -> no DATA_STALE_CANDLES event (scan proceeds)."""
        engine = _make_engine()
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True
        _set_feed(engine, _mk_candles(age_minutes=5))

        await engine._scan_symbol("RELIANCE", None)

        assert _stale_events(engine) == []

    @pytest.mark.asyncio
    async def test_market_closed_bypasses_freshness_check(self):
        """Outside market hours the check is skipped (stale bars are legitimate)."""
        engine = _make_engine()
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = False
        _set_feed(engine, _mk_candles(age_minutes=60 * 24 * 400))

        await engine._scan_symbol("RELIANCE", None)

        assert _stale_events(engine) == []

    @pytest.mark.asyncio
    async def test_disabled_guard_never_blocks(self):
        """stale_candle_max_age_minutes=0 disables the guard entirely."""
        engine = _make_engine()
        engine.stale_candle_max_age_minutes = 0
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True
        _set_feed(engine, _mk_candles(age_minutes=60 * 24 * 400))

        await engine._scan_symbol("RELIANCE", None)

        assert _stale_events(engine) == []

    @pytest.mark.asyncio
    async def test_missing_timestamps_do_not_block(self):
        """Unknown freshness (no timestamps) must NOT block trading."""
        engine = _make_engine()
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True
        _set_feed(engine, _mk_candles(age_minutes=None))

        await engine._scan_symbol("RELIANCE", None)

        assert _stale_events(engine) == []

    @pytest.mark.asyncio
    async def test_stale_symbol_warned_once(self):
        """The warning log fires once per symbol, not on every scan cycle."""
        engine = _make_engine()
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True
        stale = _mk_candles(age_minutes=60 * 24 * 400)
        _set_feed(engine, stale)

        await engine._scan_symbol("DEADCO", None)
        await engine._scan_symbol("DEADCO", None)

        # Telemetry records every skip, but the warned set stays size 1.
        assert len(engine._stale_data_symbols_warned) == 1
        assert len(_stale_events(engine)) == 2


class _StaleAwareMockFeed:
    """Serves stale candles for DEADCO and fresh candles for LIVECO."""

    async def get_candles(self, symbol: str, timeframe: str = "15m", count: int = 100):
        if symbol == "DEADCO":
            return _mk_candles(n=max(count, 30), age_minutes=60 * 24 * 400)
        return _mk_candles(n=max(count, 30), age_minutes=10)


class TestWatchlistBuilderFreshnessGuard:
    @pytest.mark.asyncio
    async def test_stale_symbol_dropped_from_watchlist(self):
        """A symbol serving 400-day-old candles never reaches the final watchlist."""
        builder = WatchlistBuilder()
        builder.technical_scanner = MagicMock()
        builder.technical_scanner.scan = AsyncMock(return_value=[])
        builder.kronos_scanner = MagicMock()
        builder.kronos_scanner.scan = MagicMock(return_value=[])

        result = await builder.build_daily_watchlist(
            feed=_StaleAwareMockFeed(),
            regime="Sideways",
            candidate_symbols=["DEADCO", "LIVECO"],
            final_top_n=10,
        )

        symbols = [r["symbol"] for r in result]
        assert "DEADCO" not in symbols, (
            "Stale (delisted/suspended) symbol leaked into the final watchlist"
        )
        assert symbols == ["LIVECO"], (
            "Fresh symbol should be promoted via the verified-fallback path"
        )
