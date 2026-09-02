"""Tests for P2-c/P2-d: Fyers-sourced backtest history + top-20 watchlist
with midday refresh."""

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from core.scheduler import MarketLifecycleScheduler

IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────
# P2-d: watchlist top-N + midday refresh
# ─────────────────────────────────────────────


def _scheduler_with_config(watchlist_cfg: dict):
    engine = MagicMock()
    engine.feed = None
    engine.current_regime = "Sideways"
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()
    cfg = MagicMock()
    cfg.get_watchlist_config.return_value = watchlist_cfg
    engine.config = cfg

    repo = MagicMock()

    async def get_repo():
        return repo

    return MarketLifecycleScheduler(engine=engine, repository_getter=get_repo), engine, repo


def test_watchlist_top_n_reads_config():
    sched, _, _ = _scheduler_with_config({"final_top_n": 20})
    assert sched._watchlist_top_n() == 20


def test_watchlist_top_n_default_20_without_config():
    sched, engine, _ = _scheduler_with_config({})
    engine.config = None
    assert sched._watchlist_top_n() == 20


def test_watchlist_top_n_clamped_to_sane_bounds():
    sched, _, _ = _scheduler_with_config({"final_top_n": 500})
    assert sched._watchlist_top_n() == 50
    sched2, _, _ = _scheduler_with_config({"final_top_n": 1})
    assert sched2._watchlist_top_n() == 5
    sched3, _, _ = _scheduler_with_config({"final_top_n": "garbage"})
    assert sched3._watchlist_top_n() == 20


@pytest.mark.asyncio
async def test_midday_refresh_skips_non_trading_day():
    sched, engine, _ = _scheduler_with_config({"final_top_n": 20})
    mock_build = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sched, "_is_trading_day", lambda: False)
        mp.setattr(sched, "_build_and_persist_watchlist", mock_build)
        await sched.on_midday_watchlist_refresh()
    mock_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_midday_refresh_rebuilds_on_trading_day():
    sched, engine, _ = _scheduler_with_config({"final_top_n": 20})
    built = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}]
    mock_build = AsyncMock(return_value=built)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sched, "_is_trading_day", lambda: True)
        mp.setattr(sched, "_build_and_persist_watchlist", mock_build)
        await sched.on_midday_watchlist_refresh()
    mock_build.assert_awaited_once_with(source_label="12:30 PM IST")


@pytest.mark.asyncio
async def test_six_lifecycle_jobs_registered():
    sched, _, _ = _scheduler_with_config({})
    sched.start()
    try:
        job_ids = {j.id for j in sched.scheduler.get_jobs()}
    finally:
        sched.stop()
    # v0.4.8 P2 added the 15:35 EOD PDF job (eod_pdf_report) — 7 jobs now.
    assert job_ids == {
        "pre_market_init",
        "market_open",
        "midday_watchlist_refresh",
        "squareoff_warning",
        "auto_squareoff",
        "market_close",
        "eod_pdf_report",
    }


# ─────────────────────────────────────────────
# P2-c: Fyers history for backtests
# ─────────────────────────────────────────────


def _fyers_repo_with_valid_token():
    from utils.encryption import encrypt_credentials

    repo = MagicMock()
    cred = SimpleNamespace(
        broker_name="fyers",
        encrypted_credentials=encrypt_credentials(
            {"app_id": "APP", "access_token": "TOK"}
        ),
        extra='{"token_expires_at": %s}' % (time.time() + 3600),
        last_connected_at=None,
        last_error=None,
    )
    repo.get_broker_credentials = AsyncMock(return_value=cred)
    return repo


def _repo_getter(repo):
    async def getter():
        return repo
    return getter


@pytest.mark.asyncio
async def test_fetch_fyers_history_no_token_returns_empty():
    """Without valid Fyers credentials the backtest silently uses Yahoo."""
    from feeds.fyers_candles import fetch_fyers_history_candles

    repo = MagicMock()
    repo.get_broker_credentials = AsyncMock(return_value=None)
    out = await fetch_fyers_history_candles(
        _repo_getter(repo), "RELIANCE", "5m", "2026-08-01", "2026-08-15"
    )
    assert out == []


@pytest.mark.asyncio
async def test_fetch_fyers_history_aggregates_to_5m():
    """With a valid token + mock broker, 1m bars over the range come back
    aggregated as IST-ISO 5m candles."""
    from feeds.fyers_candles import fetch_fyers_history_candles

    # 10 minutes of 1m bars starting 09:15 IST on a Monday
    base = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
    bars = [
        {
            "timestamp": int((base + timedelta(minutes=i)).timestamp()),
            "open": 100 + i, "high": 101 + i, "low": 99 + i,
            "close": 100.5 + i, "volume": 100 + i,
        }
        for i in range(10)
    ]

    repo = _fyers_repo_with_valid_token()

    import feeds.fyers_candles as fc

    captured = {}

    class _MockBroker:
        async def get_candles(self, symbol, resolution="1", range_from="", range_to="", **kw):
            captured["symbol"] = symbol
            captured["resolution"] = resolution
            captured["range"] = (range_from, range_to)
            return bars

    orig_build = fc.build_fyers_candle_feed

    async def fake_build(rg):
        feed = fc.FyersCandleFeed(broker_factory=lambda: _MockBroker(), cache_ttl_seconds=0)
        return feed

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fc, "build_fyers_candle_feed", fake_build)
        out = await fetch_fyers_history_candles(
            _repo_getter(repo), "RELIANCE", "5m", "2026-08-01", "2026-08-15"
        )

    # 10 one-minute bars → 2 five-minute buckets
    assert len(out) == 2
    assert out[0]["timestamp"] == "2026-08-31T09:15:00+05:30"
    assert out[1]["timestamp"] == "2026-08-31T09:20:00+05:30"
    # Broker received the FYERS symbol + 1m resolution + normalized dates
    assert captured["symbol"] == "NSE:RELIANCE-EQ"
    assert captured["resolution"] == "1"
    assert captured["range"] == ("2026-08-01", "2026-08-15")


@pytest.mark.asyncio
async def test_fetch_fyers_history_1m_passthrough():
    from feeds.fyers_candles import fetch_fyers_history_candles

    base = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
    bars = [
        {"timestamp": int((base + timedelta(minutes=i)).timestamp()),
         "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10}
        for i in range(3)
    ]
    repo = _fyers_repo_with_valid_token()

    import feeds.fyers_candles as fc

    class _MockBroker:
        async def get_candles(self, symbol, resolution="1", range_from="", range_to="", **kw):
            return bars

    async def fake_build(rg):
        return fc.FyersCandleFeed(broker_factory=lambda: _MockBroker(), cache_ttl_seconds=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fc, "build_fyers_candle_feed", fake_build)
        out = await fetch_fyers_history_candles(
            _repo_getter(repo), "RELIANCE", "1m", "2026-08-01", "2026-08-15"
        )
    assert len(out) == 3
    assert all(c["timestamp"].endswith("+05:30") for c in out)


@pytest.mark.asyncio
async def test_fetch_fyers_history_broker_failure_returns_empty():
    from feeds.fyers_candles import fetch_fyers_history_candles

    repo = _fyers_repo_with_valid_token()

    import feeds.fyers_candles as fc

    class _FailingBroker:
        async def get_candles(self, *a, **kw):
            raise RuntimeError("fyers down")

    async def fake_build(rg):
        return fc.FyersCandleFeed(broker_factory=lambda: _FailingBroker(), cache_ttl_seconds=0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fc, "build_fyers_candle_feed", fake_build)
        out = await fetch_fyers_history_candles(
            _repo_getter(repo), "RELIANCE", "5m", "2026-08-01", "2026-08-15"
        )
    assert out == []
