"""Live-market validation correction (2026-08-28): volume_ratio forming-bar bias.

Observed live (09:38-09:41 IST, market open): G15_VolumeLiquidity rejected
SIC signals on ultra-liquid F&O names (BAJFINANCE 0.05x, JSWSTEEL 0.03x)
because the metric compared the FORMING candle's partial volume (~1 minute
into its 5-minute formation) against completed-bar averages. G15's verdict
depended on where in a bar the 180-second scan timer happened to land.

Corrected metric (core/engine.py): volume_ratio =
    max(last COMPLETED bar / avg of prior 19 completed bars,
        forming bar prorated by elapsed bar-time (floor 20%))

These tests pin the corrected behaviour using synthetic candles ONLY as test
fixtures (production paths never see synthetic data — this is a unit test of
the ratio arithmetic, not of market data).
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import Settings
from core.engine import EngineState, UltraBotEngine


def _mk_candle(close=1005.0, volume=10000, ts=None):
    c = {"open": 1000.0, "high": 1010.0, "low": 990.0,
         "close": close, "volume": volume}
    if ts is not None:
        c["timestamp"] = ts
    return c


def _mk_series(formed_bar_vol, completed_vols, forming_age_seconds=60):
    """21 candles: 20 completed + 1 forming `forming_age_seconds` into its 5-min span."""
    now = datetime.now().astimezone()
    candles = []
    # 19 completed bars before the last completed one
    for i, v in enumerate(completed_vols[:-1]):
        ts = (now - timedelta(seconds=300 * (21 - i))).isoformat()
        candles.append(_mk_candle(volume=v, ts=ts))
    # last completed bar (index -2)
    ts = (now - timedelta(seconds=300 * 2)).isoformat()
    candles.append(_mk_candle(volume=completed_vols[-1], ts=ts))
    # forming bar (index -1), started forming_age_seconds ago
    ts = (now - timedelta(seconds=forming_age_seconds)).isoformat()
    candles.append(_mk_candle(volume=formed_bar_vol, ts=ts))
    return candles


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
    engine.active_strategies = ["SIC"]
    engine._broadcast = AsyncMock()
    engine._record_telemetry_event = MagicMock()
    return engine


def _valid_signal():
    return {
        "symbol": "BAJFINANCE",
        "strategy": "SIC",
        "direction": "BUY",
        "entry_price": 1005.0,
        "sl_price": 995.0,
        "target_price": 1025.0,
        "confidence": 0.85,
    }


class TestVolumeRatioCorrection:
    async def _capture_signal(self, candles):
        """Run _scan_symbol and return the signal dict passed to _run_risk_gates."""
        engine = _make_engine()
        engine.feed = MagicMock()
        engine.feed.get_candles = AsyncMock(return_value=candles)
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True

        engine._execute_strategy_scan = AsyncMock(return_value=_valid_signal())
        engine._run_risk_gates = AsyncMock(
            return_value={"passed": False, "block_gate": "TEST", "all_gates": []}
        )

        await engine._scan_symbol("BAJFINANCE", None)
        engine._run_risk_gates.assert_awaited()
        args, _ = engine._run_risk_gates.await_args
        return args[0]

    @pytest.mark.asyncio
    async def test_forming_bar_bias_corrected_young_forming_bar(self):
        """A 1-minute-old forming bar with a STRONG completed bar must not
        be punished for the forming bar's partial volume.

        Old buggy metric: 2,000/10,000 = 0.20x -> G15 reject.
        Corrected: last completed 12,000/10,000 = 1.20x -> passes through.
        """
        candles = _mk_series(
            formed_bar_vol=2_000,          # 60s into formation, partial
            completed_vols=[10_000] * 19 + [12_000],
            forming_age_seconds=60,
        )
        signal = await self._capture_signal(candles)
        vr = signal.get("volume_ratio")
        assert vr is not None
        # completed-bar ratio dominates: 1.20x
        assert 1.0 <= vr <= 1.45

    @pytest.mark.asyncio
    async def test_prorated_forming_bar_surges_qualify(self):
        """A quiet completed bar but a SURGING forming bar qualifies on run-rate.

        Forming bar 3 min in (60% elapsed) already at 1.5x average volume ->
        prorated run-rate = 1.5/0.6 = 2.5x.
        """
        candles = _mk_series(
            formed_bar_vol=15_000,         # 1.5x the 10k average, 60% elapsed
            completed_vols=[10_000] * 19 + [8_000],
            forming_age_seconds=180,
        )
        signal = await self._capture_signal(candles)
        vr = signal.get("volume_ratio")
        assert vr is not None
        assert vr >= 1.5  # prorated run-rate recognized

    @pytest.mark.asyncio
    async def test_quiet_tape_still_rejected(self):
        """Genuine below-average volume on BOTH the completed and forming bars
        must still measure below 1.0x — the correction is not a free pass."""
        candles = _mk_series(
            formed_bar_vol=3_000,          # low, 60% elapsed -> prorated 0.5x
            completed_vols=[10_000] * 19 + [7_600],  # completed 0.76x
            forming_age_seconds=180,
        )
        signal = await self._capture_signal(candles)
        vr = signal.get("volume_ratio")
        assert vr is not None
        assert vr < 1.0

    @pytest.mark.asyncio
    async def test_no_timestamps_falls_back_to_completed_bar_only(self):
        """If the forming bar has no parseable timestamp, the prorated leg is
        skipped (elapsed fraction 1.0) and only the completed ratio counts."""
        candles = [_mk_candle(volume=10_000) for _ in range(20)]
        candles.append(_mk_candle(volume=2_000))  # no timestamp anywhere
        # Rebuild completed history to keep avg 10k; last completed = 11k
        candles[-2]["volume"] = 11_000
        signal = await self._capture_signal(candles)
        vr = signal.get("volume_ratio")
        assert vr is not None
        assert 1.0 <= vr <= 1.2  # 11k/10k completed ratio, forming leg inert

    @pytest.mark.asyncio
    async def test_volume_ratio_not_overwritten_by_strategy(self):
        """setdefault semantics: a strategy-provided volume_ratio is respected."""
        engine = _make_engine()
        engine.feed = MagicMock()
        engine.feed.get_candles = AsyncMock(
            return_value=_mk_series(2_000, [10_000] * 19 + [12_000], 60))
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True

        sig = _valid_signal()
        sig["volume_ratio"] = 2.5  # strategy already measured it
        engine._execute_strategy_scan = AsyncMock(return_value=sig)
        engine._run_risk_gates = AsyncMock(
            return_value={"passed": False, "block_gate": "TEST", "all_gates": []})
        await engine._scan_symbol("BAJFINANCE", None)
        args, _ = engine._run_risk_gates.await_args
        assert args[0].get("volume_ratio") == 2.5
