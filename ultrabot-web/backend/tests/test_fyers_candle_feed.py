"""Tests for the P1 Fyers 1-minute realtime candle pipeline.

Covers:
* feeds/fyers_candles — symbol mapping, 1m→5m aggregation (bucket alignment,
  OHLCV merge, ordering, tail limiting), caching, self-healing rebuilds,
  LTP, Yahoo-compatible timestamp format
* FeedManager integration — Fyers primary failing → automatic Yahoo backup
* Engine cadence — effective scan interval 60s on realtime feed / 180s Yahoo
* build_fyers_candle_feed — valid/expired/missing credential decisions
* apply_tokens_to_engine — Fyers feed hot-apply (paper-execution hybrid)
"""

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from feeds.fyers_candles import (
    FyersCandleFeed,
    aggregate_1m_to_5m,
    to_fyers_symbol,
)
from feeds.feed_manager import FeedManager

IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────
# Symbol mapping
# ─────────────────────────────────────────────


def test_symbol_mapping_equities_and_indices():
    assert to_fyers_symbol("RELIANCE") == "NSE:RELIANCE-EQ"
    assert to_fyers_symbol("reliance") == "NSE:RELIANCE-EQ"  # upper-cased
    assert to_fyers_symbol("^NSEI") == "NSE:NIFTY50-INDEX"
    assert to_fyers_symbol("NIFTY") == "NSE:NIFTY50-INDEX"
    assert to_fyers_symbol("BANKNIFTY") == "NSE:NIFTYBANK-INDEX"
    assert to_fyers_symbol("^NSEBANK") == "NSE:NIFTYBANK-INDEX"
    assert to_fyers_symbol("FINNIFTY") == "NSE:FINNIFTY-INDEX"
    assert to_fyers_symbol("MIDCPNIFTY") == "NSE:MIDCPNIFTY-INDEX"
    assert to_fyers_symbol("^INDIAVIX") == "NSE:INDIAVIX-INDEX"
    assert to_fyers_symbol("SENSEX") == "BSE:SENSEX-INDEX"
    assert to_fyers_symbol("NSE:INFY-EQ") == "NSE:INFY-EQ"  # passthrough
    assert to_fyers_symbol("") == "NSE:-EQ"


# ─────────────────────────────────────────────
# 1m → 5m aggregation
# ─────────────────────────────────────────────


def _epoch(h, m):
    base = datetime(2026, 8, 31, tzinfo=IST)
    return int(base.replace(hour=h, minute=m, second=0).timestamp())


def test_aggregation_merges_five_bars_correctly():
    one_min = [
        {"timestamp": _epoch(9, 15), "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
        {"timestamp": _epoch(9, 16), "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1500},
        {"timestamp": _epoch(9, 17), "open": 101.5, "high": 101.8, "low": 100.8, "close": 101.0, "volume": 800},
        {"timestamp": _epoch(9, 18), "open": 101.0, "high": 103.5, "low": 101.0, "close": 103.0, "volume": 2000},
        {"timestamp": _epoch(9, 19), "open": 103.0, "high": 103.2, "low": 102.4, "close": 102.8, "volume": 700},
    ]
    bars = aggregate_1m_to_5m(one_min)
    assert len(bars) == 1
    b = bars[0]
    assert b["open"] == 100.0     # first open
    assert b["high"] == 103.5     # max high
    assert b["low"] == 99.5       # min low
    assert b["close"] == 102.8    # last close
    assert b["volume"] == 6000    # summed volume
    # Timestamp = bucket OPEN in IST ISO (Yahoo-compatible)
    assert b["timestamp"] == "2026-08-31T09:15:00+05:30"


def test_aggregation_bucket_boundaries_align_to_5m_grid():
    # 09:14 belongs to the 09:10 bucket; 09:15 starts a new bucket
    one_min = [
        {"timestamp": _epoch(9, 14), "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        {"timestamp": _epoch(9, 15), "open": 10.5, "high": 12, "low": 10, "close": 11, "volume": 200},
    ]
    bars = aggregate_1m_to_5m(one_min)
    assert len(bars) == 2
    assert bars[0]["timestamp"] == "2026-08-31T09:10:00+05:30"
    assert bars[1]["timestamp"] == "2026-08-31T09:15:00+05:30"


def test_aggregation_handles_out_of_order_input():
    one_min = [
        {"timestamp": _epoch(9, 17), "open": 3, "high": 4, "low": 2, "close": 3.5, "volume": 30},
        {"timestamp": _epoch(9, 15), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"timestamp": _epoch(9, 16), "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 20},
    ]
    bars = aggregate_1m_to_5m(one_min)
    assert len(bars) == 1
    b = bars[0]
    assert b["open"] == 1      # earliest bar's open
    assert b["close"] == 3.5   # latest bar's close
    assert b["volume"] == 60


def test_aggregation_partial_bucket_is_a_forming_candle():
    """A bucket with fewer than 5 bars (still forming) is still emitted —
    exactly like Yahoo's last (partial) candle."""
    one_min = [
        {"timestamp": _epoch(10, 0), "open": 50, "high": 51, "low": 49, "close": 50.5, "volume": 100},
        {"timestamp": _epoch(10, 1), "open": 50.5, "high": 52, "low": 50, "close": 51.5, "volume": 90},
    ]
    bars = aggregate_1m_to_5m(one_min)
    assert len(bars) == 1
    assert bars[0]["close"] == 51.5


def test_aggregation_empty_and_invalid_inputs():
    assert aggregate_1m_to_5m([]) == []
    assert aggregate_1m_to_5m([{"timestamp": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]) == []
    assert aggregate_1m_to_5m([{"timestamp": None, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]) == []


# ─────────────────────────────────────────────
# FyersCandleFeed with a mock broker
# ─────────────────────────────────────────────


class _MockFyersBroker:
    """Stands in for FyersBroker.get_candles(symbol, resolution, from, to)."""

    def __init__(self, one_min_bars=None, fail: bool = False):
        self.one_min_bars = one_min_bars or []
        self.fail = fail
        self.calls = 0

    async def get_candles(self, symbol, resolution="1", range_from="", range_to="", **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fyers down")
        return list(self.one_min_bars)


def _make_1m_series(start_h=9, start_m=15, bars=25, base_price=100.0):
    """Synthetic 1m bars: each bar moves +0.5, volume 100.."""
    out = []
    t = datetime(2026, 8, 31, start_h, start_m, tzinfo=IST)
    price = base_price
    for i in range(bars):
        out.append({
            "timestamp": int(t.timestamp()),
            "open": round(price, 2),
            "high": round(price + 0.3, 2),
            "low": round(price - 0.2, 2),
            "close": round(price + 0.5, 2),
            "volume": 100 + i,
        })
        price += 0.5
        t += timedelta(minutes=1)
    return out


@pytest.mark.asyncio
async def test_feed_aggregates_1m_to_5m_end_to_end():
    bars = _make_1m_series(bars=25)  # 25 minutes → 5 full 5m buckets
    broker = _MockFyersBroker(one_min_bars=bars)
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)

    candles = await feed.get_candles("RELIANCE", timeframe="5m", count=100)
    assert len(candles) == 5
    # The broker must receive the FYERS-style symbol
    assert broker.calls >= 1
    # First bucket open/high/low/close/volume from bars 0-4
    first = candles[0]
    assert first["open"] == 100.0
    assert first["volume"] == 100 + 101 + 102 + 103 + 104
    assert first["timestamp"] == "2026-08-31T09:15:00+05:30"
    # Yahoo-compatible key set
    assert set(first.keys()) == {"timestamp", "open", "high", "low", "close", "volume"}


@pytest.mark.asyncio
async def test_feed_1m_timeframe_passthrough():
    bars = _make_1m_series(bars=10)
    broker = _MockFyersBroker(one_min_bars=bars)
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)

    candles = await feed.get_candles("RELIANCE", timeframe="1m", count=100)
    assert len(candles) == 10
    assert candles[0]["timestamp"] == "2026-08-31T09:15:00+05:30"
    assert candles[-1]["close"] == pytest.approx(105.0)  # 100 + 0.5 × 10


@pytest.mark.asyncio
async def test_feed_tail_limits_count():
    bars = _make_1m_series(bars=50)  # 10 buckets
    broker = _MockFyersBroker(one_min_bars=bars)
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)

    candles = await feed.get_candles("RELIANCE", timeframe="5m", count=3)
    assert len(candles) == 3
    # The LAST 3 buckets (09:50, 09:55, 10:00), not the first
    assert candles[-1]["timestamp"] == "2026-08-31T10:00:00+05:30"


@pytest.mark.asyncio
async def test_feed_cache_prevents_repeat_fetch():
    bars = _make_1m_series(bars=25)
    broker = _MockFyersBroker(one_min_bars=bars)
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=60)

    await feed.get_candles("RELIANCE", timeframe="5m")
    calls_after_first = broker.calls
    await feed.get_candles("RELIANCE", timeframe="5m")
    assert broker.calls == calls_after_first  # served from cache
    assert feed.is_connected()


@pytest.mark.asyncio
async def test_feed_empty_result_triggers_rebuild_after_two():
    broker = _MockFyersBroker(one_min_bars=[])
    builds = {"n": 0}

    def factory():
        builds["n"] += 1
        return broker

    feed = FyersCandleFeed(broker_factory=factory, cache_ttl_seconds=0)
    assert builds["n"] == 1  # initial construction

    await feed.get_candles("RELIANCE")  # empty #1
    assert builds["n"] == 1
    await feed.get_candles("RELIANCE")  # empty #2 → rebuild
    assert builds["n"] == 2
    assert await feed.get_candles("RELIANCE") == []


@pytest.mark.asyncio
async def test_feed_exception_returns_empty_never_raises():
    broker = _MockFyersBroker(fail=True)
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)
    assert await feed.get_candles("RELIANCE") == []
    assert await feed.get_ltp("RELIANCE") == 0.0


@pytest.mark.asyncio
async def test_feed_ltp_is_last_1m_close():
    bars = _make_1m_series(bars=5)
    broker = _MockFyersBroker(one_min_bars=bars)
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)
    ltp = await feed.get_ltp("RELIANCE")
    assert ltp == pytest.approx(102.5)  # 100 + 0.5*5


@pytest.mark.asyncio
async def test_feed_apply_new_token_resets_client():
    broker = _MockFyersBroker(one_min_bars=_make_1m_series())
    feed = FyersCandleFeed(broker_factory=lambda: broker)
    feed.apply_new_token("fresh-token-123")
    assert broker.calls == 0  # no fetch needed; token stored for next build


# ─────────────────────────────────────────────
# FeedManager integration — automatic failover
# ─────────────────────────────────────────────


class _MockYahooFeed:
    def get_name(self):
        return "Yahoo"

    async def get_candles(self, symbol, timeframe="5m", count=100):
        return [{"timestamp": "2026-08-31T09:15:00+05:30", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    async def get_ltp(self, symbol):
        return 1.0


@pytest.mark.asyncio
async def test_feed_manager_fails_over_fyers_to_yahoo():
    """3 consecutive Fyers failures → FeedManager switches to Yahoo backup."""
    broker = _MockFyersBroker(one_min_bars=[])
    fyers_feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)
    yahoo = _MockYahooFeed()
    fm = FeedManager(primary=fyers_feed, backup=yahoo)

    await fm.get_candles("RELIANCE")  # fail 1 (feed counts internally)
    await fm.get_candles("RELIANCE")  # fail 2
    res = await fm.get_candles("RELIANCE")  # fail 3 → switch → yahoo backup
    # Yahoo's candle comes back
    assert res and res[0]["close"] == 1
    assert fm._using_backup is True


# ─────────────────────────────────────────────
# Engine cadence — effective scan interval
# ─────────────────────────────────────────────


def _engine_stub_with_feed(feed):
    from core.engine import UltraBotEngine

    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.feed = feed
    engine.config = MagicMock()
    engine.config.get_engine_config.return_value = {
        "scan_interval_seconds": 180,
        "scan_interval_realtime_seconds": 60,
    }
    return engine


def test_effective_interval_realtime_feed_is_60():
    broker = _MockFyersBroker()
    feed = FyersCandleFeed(broker_factory=lambda: broker)
    fm = FeedManager(primary=feed, backup=_MockYahooFeed())
    engine = _engine_stub_with_feed(fm)
    assert engine._effective_scan_interval() == 60
    assert engine._is_realtime_feed_active() is True
    assert engine._active_data_source_name() == "Fyers 1m Realtime"


def test_effective_interval_yahoo_feed_is_180():
    fm = FeedManager(primary=_MockYahooFeed(), backup=None)
    engine = _engine_stub_with_feed(fm)
    assert engine._effective_scan_interval() == 180
    assert engine._is_realtime_feed_active() is False


def test_effective_interval_after_failover_relaxes_to_180():
    """Fyers fails mid-session → failover to Yahoo → cadence relaxes."""
    broker = _MockFyersBroker(one_min_bars=[])
    feed = FyersCandleFeed(broker_factory=lambda: broker, cache_ttl_seconds=0)
    fm = FeedManager(primary=feed, backup=_MockYahooFeed())
    engine = _engine_stub_with_feed(fm)

    assert engine._effective_scan_interval() == 60  # realtime first
    for _ in range(3):
        asyncio.get_event_loop().run_until_complete(fm.get_candles("X"))
    assert engine._effective_scan_interval() == 180  # relaxed after failover


def test_effective_interval_no_feed_defaults():
    engine = _engine_stub_with_feed(None)
    assert engine._effective_scan_interval() == 180
    assert engine._is_realtime_feed_active() is False
    assert engine._active_data_source_name() is None


# ─────────────────────────────────────────────
# build_fyers_candle_feed — credential decisions
# ─────────────────────────────────────────────


def _repo_with_fyers_cred(access_token="tok", expires_at=None, has_creds=True):
    from utils.encryption import encrypt_credentials

    repo = MagicMock()
    if not has_creds:
        repo.get_broker_credentials = AsyncMock(return_value=None)
    else:
        cred = SimpleNamespace(
            broker_name="fyers",
            encrypted_credentials=encrypt_credentials(
                {"app_id": "APPID", "access_token": access_token}
            ),
            extra='{"token_expires_at": %s}' % (json.dumps(expires_at) if expires_at else "null"),
            last_connected_at=None,
            last_error=None,
        )
        repo.get_broker_credentials = AsyncMock(return_value=cred)
    return repo


def _repo_getter(repo):
    async def getter():
        return repo
    return getter


import json  # noqa: E402  (used in _repo_with_fyers_cred)


@pytest.mark.asyncio
async def test_build_feed_none_when_no_credentials():
    from feeds.fyers_candles import build_fyers_candle_feed

    repo = _repo_getter(_repo_with_fyers_cred(has_creds=False))
    assert await build_fyers_candle_feed(repo) is None


@pytest.mark.asyncio
async def test_build_feed_none_when_token_expired():
    from feeds.fyers_candles import build_fyers_candle_feed

    repo = _repo_getter(_repo_with_fyers_cred(expires_at=time.time() - 100))
    assert await build_fyers_candle_feed(repo) is None


@pytest.mark.asyncio
async def test_build_feed_none_when_token_missing():
    from feeds.fyers_candles import build_fyers_candle_feed

    repo = _repo_getter(_repo_with_fyers_cred(access_token=""))
    assert await build_fyers_candle_feed(repo) is None


@pytest.mark.asyncio
async def test_build_feed_active_with_valid_token():
    from feeds.fyers_candles import build_fyers_candle_feed

    repo = _repo_getter(_repo_with_fyers_cred(expires_at=time.time() + 3600))
    feed = await build_fyers_candle_feed(repo)
    assert feed is not None
    assert isinstance(feed, FyersCandleFeed)
    assert feed.get_name() == "Fyers 1m Realtime"
    assert feed.is_realtime is True


@pytest.mark.asyncio
async def test_build_feed_repo_error_returns_none_never_raises():
    from feeds.fyers_candles import build_fyers_candle_feed

    repo = MagicMock()
    repo.get_broker_credentials = AsyncMock(side_effect=RuntimeError("db offline"))
    assert await build_fyers_candle_feed(_repo_getter(repo)) is None


# ─────────────────────────────────────────────
# apply_tokens_to_engine — Fyers feed hot-apply
# ─────────────────────────────────────────────


def test_apply_tokens_fyers_refreshes_feed_even_on_paper_engine():
    from brokers.relogin import apply_tokens_to_engine

    engine = MagicMock()
    engine.broker_name = "paper"          # execution on paper
    engine.broker = MagicMock()           # PaperBroker-ish
    fyers_feed = MagicMock(spec=FyersCandleFeed)
    fyers_feed.apply_new_token = MagicMock()
    engine.feed = MagicMock()
    engine.feed.primary = fyers_feed

    ok = apply_tokens_to_engine(engine, "fyers", {"kind": "fyers", "access_token": "NEW"})
    assert ok is True
    fyers_feed.apply_new_token.assert_called_once_with("NEW")


def test_apply_tokens_fyers_without_fyers_feed_returns_false():
    from brokers.relogin import apply_tokens_to_engine

    engine = MagicMock()
    engine.broker_name = "paper"
    engine.broker = MagicMock()
    engine.feed = MagicMock()
    engine.feed.primary = MagicMock(spec=[])  # no apply_new_token attr

    ok = apply_tokens_to_engine(engine, "fyers", {"kind": "fyers", "access_token": "NEW"})
    assert ok is False


def test_apply_tokens_non_fyers_unchanged_behavior():
    """Regression guard: angel_one/shoonya/dhan paths still work."""
    from brokers.relogin import apply_tokens_to_engine

    engine = MagicMock()
    engine.broker_name = "angel_one"
    broker = MagicMock()
    broker.apply_session = MagicMock()
    engine.broker = broker
    engine.feed = MagicMock()
    engine.feed.primary = MagicMock(spec=[])

    ok = apply_tokens_to_engine(
        engine, "angel_one", {"kind": "angel_one", "jwt_token": "j", "feed_token": "f", "refresh_token": "r"}
    )
    assert ok is True
    broker.apply_session.assert_called_once_with(jwt_token="j", feed_token="f", refresh_token="r")
