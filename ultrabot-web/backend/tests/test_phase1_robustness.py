"""Phase 1 Robustness — regression tests.

Covers the approved Phase 1 hardening work:
  1. ADX indicator (trend-strength measurement for the ORB chop filter)
  2. ORB chop filter: no breakout entries when ADX < 20
  3. VC climax-retest entry: no chasing the climax; entry on confirmed retest
  4. G17 CostPreCheck: cost-dominated geometry rejected, healthy geometry passes
  5. G18 StrategyGuard: per-strategy daily loss cap (MRF override) + consecutive
     loss cooldown with expiry
  6. Real-trades-only performance: compute_strategy_stats from the trades
     ledger (zero-trade strategies report zeros — never fabricated numbers)
  7. Shadow signal stats + regime attribution (separate from trade win rates)
  8. Engine time-stop / fail-fast exit helpers
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrations import Base
from db.repository import Repository
from risk.gates.g17_cost_precheck import G17CostPreCheck
from risk.gates.g18_strategy_guard import G18StrategyGuard
from risk.risk_engine import RiskEngine
from strategies.v2.orb import OpeningRangeBreakout
from strategies.v2.vc import VolumeClimax
from utils.indicators import calculate_adx

IST = timezone(timedelta(hours=5, minutes=30))


def _mk_candles(rows):
    """Build a candles DataFrame from (time, o, h, l, c, v) tuples."""
    idx = [r[0] for r in rows]
    data = {
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
    }
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


# ─────────────────────────────────────────────────────────────────────────────
# 1. ADX indicator
# ─────────────────────────────────────────────────────────────────────────────

def test_adx_trending_vs_flat():
    rng = np.random.default_rng(11)
    n = 200
    trend = pd.Series(np.cumsum(rng.normal(0.5, 0.25, n)) + 100.0)
    # Strictly alternating closes around a fixed level: direction flips every
    # bar, so directional movement nets to ~0 and ADX collapses
    flat = pd.Series(100.0 + 0.02 * ((-1.0) ** np.arange(n)))
    noise = abs(rng.normal(0.15, 0.08, n))

    adx_t = calculate_adx(trend + noise, trend - noise, trend, 14)
    adx_f = calculate_adx(flat + 0.01, flat - 0.01, flat, 14)

    assert float(adx_t.iloc[-1]) > 25.0, "trending series must show high ADX"
    assert float(adx_f.iloc[-1]) < 20.0, "flat series must show low ADX"


def test_adx_short_series_is_nan_not_crash():
    s = pd.Series([100.0, 101.0, 100.5])
    adx = calculate_adx(s + 0.2, s - 0.2, s, 14)
    assert pd.isna(adx.iloc[-1])


# ─────────────────────────────────────────────────────────────────────────────
# 2. ORB chop filter
# ─────────────────────────────────────────────────────────────────────────────

def _orb_day(prior_closes, day0=1000.0):
    """Build one ORB scan day on top of prior closes (list of floats)."""
    rows = []
    t0 = datetime(2024, 3, 4, 9, 15, tzinfo=IST) - timedelta(minutes=5 * len(prior_closes))
    px = prior_closes[0]
    for i, c in enumerate(prior_closes):
        rows.append((t0 + timedelta(minutes=5 * i), px, max(px, c) + 0.3, min(px, c) - 0.3, c, 1000))
        px = c
    # Today: 3 range bars then a breakout bar at 09:35
    day = datetime(2024, 3, 5, 9, 15, tzinfo=IST)
    base = prior_closes[-1]
    for k, c in enumerate([base, base - 1.0, base + 1.0]):
        rows.append((day + timedelta(minutes=5 * k), base, c + 1.5, c - 1.5, c, 1000))
    # Breakout bar: closes well above the 3-bar range on 2.5x volume
    rows.append((day + timedelta(minutes=20), base + 1.0, base + 8.0, base + 0.5, base + 7.5, 2500))
    return _mk_candles(rows)


@pytest.mark.asyncio
async def test_orb_blocks_choppy_market():
    """Identical breakout bar in a directionless tape (low ADX) → no signal."""
    rng = np.random.default_rng(3)
    flat_prior = [1000.0 + rng.normal(0, 1.5) for _ in range(90)]
    df = _orb_day(flat_prior)
    adx = float(calculate_adx(df["high"], df["low"], df["close"], 14).iloc[-1])
    assert adx < 20.0, f"test setup error: expected chop, got ADX {adx}"

    orb = OpeningRangeBreakout()
    sig = await orb.scan("TCS", df, regime="Bull", vix=14.0)
    assert sig is None, "ORB must NOT enter breakouts in a low-ADX chop regime"


@pytest.mark.asyncio
async def test_orb_allows_trending_market():
    """Same breakout bar with a real trend behind it (high ADX) → signal."""
    trend_prior = [900.0 + 1.1 * i for i in range(90)]  # steady uptrend to ~998
    df = _orb_day(trend_prior)
    adx = float(calculate_adx(df["high"], df["low"], df["close"], 14).iloc[-1])
    assert adx > 20.0, f"test setup error: expected trend, got ADX {adx}"

    orb = OpeningRangeBreakout()
    sig = await orb.scan("TCS", df, regime="Bull", vix=14.0)
    assert sig is not None, "ORB should trade the breakout when ADX confirms trend"
    assert sig["direction"] == "BUY"
    assert sig["extra_details"]["adx"] is not None and sig["extra_details"]["adx"] >= 20.0


@pytest.mark.asyncio
async def test_orb_min_adx_param_override():
    """min_adx param is honored (raising it above the current ADX blocks)."""
    trend_prior = [900.0 + 1.1 * i for i in range(90)]
    df = _orb_day(trend_prior)
    adx_now = float(calculate_adx(df["high"], df["low"], df["close"], 14).iloc[-1])

    orb = OpeningRangeBreakout(params={"min_adx": adx_now + 10.0})
    sig = await orb.scan("TCS", df, regime="Bull", vix=14.0)
    assert sig is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. VC climax-retest entry
# ─────────────────────────────────────────────────────────────────────────────

def _vc_frames():
    """Climax frame (detection) and retest frame (entry) for the same symbol."""
    t0 = datetime(2024, 3, 5, 9, 15, tzinfo=IST)
    rows = []
    px = 98.0
    # 22 quiet gently-rising bars (OBV rises, VWAP stays below price) — VC
    # needs >= 22 bars. Keep the drift tiny so the climax close dominates.
    for i in range(22):
        c = px + 0.01
        rows.append((t0 + timedelta(minutes=5 * i), px, c + 0.1, px - 0.1, c, 1000))
        px = c
    # Climax bar: 7x volume, big bullish body, close in top of range
    climax_open, climax_close = 99.5, 104.4
    rows.append((t0 + timedelta(minutes=110), climax_open, 105.0, 99.8, climax_close, 7000))
    climax_frame = _mk_candles(rows)

    # Retest frames: pullback into the midpoint zone then confirmed resumption
    retest_rows = list(rows)
    mid = (105.0 + 99.8) / 2.0  # 102.4 ; zone = mid ± 0.30*5.2 → [100.84, 103.96]
    retest_rows.append((t0 + timedelta(minutes=115), 104.0, 104.2, 101.5, 102.0, 1200))  # pullback
    retest_rows.append((t0 + timedelta(minutes=120), 102.0, 103.9, 101.8, 103.6, 1500))  # resumption
    retest_frame = _mk_candles(retest_rows)
    return climax_frame, retest_frame


@pytest.mark.asyncio
async def test_vc_does_not_chase_climax():
    """Climax bar itself produces NO signal — only a registered retest setup."""
    vc = VolumeClimax()
    climax_frame, _ = _vc_frames()
    sig = await vc.scan("RELIANCE", climax_frame, regime="Bull", vix=14.0)
    assert sig is None, "VC must not enter on the climax candle close"
    assert "RELIANCE" in vc._pending_retests, "climax must register a pending retest setup"
    setup = vc._pending_retests["RELIANCE"]
    assert setup["direction"] == "BUY"
    assert abs(setup["climax_mid"] - 102.4) < 0.01


@pytest.mark.asyncio
async def test_vc_enters_on_confirmed_retest():
    """Pullback to the climax midpoint zone + bullish resumption → entry."""
    vc = VolumeClimax()
    climax_frame, retest_frame = _vc_frames()
    await vc.scan("RELIANCE", climax_frame, regime="Bull", vix=14.0)

    sig = await vc.scan("RELIANCE", retest_frame, regime="Bull", vix=14.0)
    assert sig is not None, "confirmed retest must produce a signal"
    assert sig["direction"] == "BUY"
    assert sig["extra_details"]["entry_mode"] == "climax_retest"
    assert sig["entry_price"] == pytest.approx(103.6, abs=0.01)
    assert sig["sl_price"] < sig["entry_price"] < sig["target_price"]
    assert "RELIANCE" not in vc._pending_retests, "setup consumed after entry"


@pytest.mark.asyncio
async def test_vc_retest_setup_expires():
    """Setup older than retest_max_bars expires without entry."""
    vc = VolumeClimax(params={"retest_max_bars": 3})
    climax_frame, _ = _vc_frames()
    await vc.scan("RELIANCE", climax_frame, regime="Bull", vix=14.0)

    rows = list(climax_frame.index)
    last = climax_frame.iloc[-1]
    extended = climax_frame.copy()
    add = []
    px = float(last["close"])
    t = rows[-1]
    for i in range(6):  # drift sideways (no retest trigger, no invalidation)
        c = px + 0.02
        add.append((t + timedelta(minutes=5 * (i + 1)), px, c + 0.1, px - 0.1, c, 900))
        px = c
    extended = _mk_candles(list(zip(
        [r[0] for r in _rows_of(climax_frame)] + [a[0] for a in add],
        [r[1] for r in _rows_of(climax_frame)] + [a[1] for a in add],
        [r[2] for r in _rows_of(climax_frame)] + [a[2] for a in add],
        [r[3] for r in _rows_of(climax_frame)] + [a[3] for a in add],
        [r[4] for r in _rows_of(climax_frame)] + [a[4] for a in add],
        [r[5] for r in _rows_of(climax_frame)] + [a[5] for a in add],
    )))

    sig = await vc.scan("RELIANCE", extended, regime="Bull", vix=14.0)
    assert sig is None
    assert "RELIANCE" not in vc._pending_retests, "expired setup must be dropped"


def _rows_of(df):
    return [
        (ts, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
        for ts, r in df.iterrows()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 4. G17 CostPreCheck
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_g17_rejects_cost_dominated_trade():
    gate = G17CostPreCheck({"max_fee_pct_of_risk": 30.0, "hard_risk_pct": 1.0, "brokerage_per_order": 20.0})
    signal = {"entry_price": 100.0, "sl_price": 99.5, "target_price": 101.0, "strategy": "ORB"}
    ctx = {"total_capital": 10000.0}
    res = await gate.check(signal, ctx)
    assert not res.passed
    assert "G17" in res.gate_name
    assert res.value > 30.0


@pytest.mark.asyncio
async def test_g17_passes_healthy_geometry():
    gate = G17CostPreCheck({"max_fee_pct_of_risk": 30.0, "hard_risk_pct": 1.0, "brokerage_per_order": 20.0})
    signal = {"entry_price": 100.0, "sl_price": 98.0, "target_price": 104.0, "strategy": "PTC"}
    ctx = {"total_capital": 100000.0}
    res = await gate.check(signal, ctx)
    assert res.passed
    assert res.value < 30.0


@pytest.mark.asyncio
async def test_g17_skips_incomplete_geometry():
    gate = G17CostPreCheck({})
    res = await gate.check({"entry_price": 0, "sl_price": 0, "target_price": 0}, {})
    assert res.passed  # geometry pre-validated elsewhere; never crash


# ─────────────────────────────────────────────────────────────────────────────
# 5. G18 StrategyGuard
# ─────────────────────────────────────────────────────────────────────────────

_G18_CFG = {
    "per_strategy_daily_loss_pct": 1.0,
    "per_strategy_daily_loss_overrides": {"MRF": 0.75},
    "per_strategy_consec_loss_limit": 2,
    "per_strategy_consec_loss_cooldown_minutes": 240,
}


@pytest.mark.asyncio
async def test_g18_mrf_tighter_cap():
    gate = G18StrategyGuard(_G18_CFG)
    now = datetime.now(IST)
    # MRF down ₹800: over the 0.75% (₹750) override on ₹100k
    res = await gate.check(
        {"strategy": "MRF"},
        {"total_capital": 100000.0, "strategy_daily_pnl": -800.0, "current_time": now},
    )
    assert not res.passed
    assert "MRF" in res.message

    # ORB down ₹800: under the default 1.0% (₹1000) cap → passes
    res2 = await gate.check(
        {"strategy": "ORB"},
        {"total_capital": 100000.0, "strategy_daily_pnl": -800.0, "current_time": now},
    )
    assert res2.passed


@pytest.mark.asyncio
async def test_g18_consecutive_loss_cooldown():
    gate = G18StrategyGuard(_G18_CFG)
    now = datetime.now(IST)

    # 2 consecutive losses 30 minutes ago → blocked (240 min cooldown)
    res = await gate.check(
        {"strategy": "MB"},
        {
            "total_capital": 100000.0,
            "strategy_daily_pnl": -100.0,
            "strategy_consecutive_losses": 2,
            "strategy_last_loss_at": (now - timedelta(minutes=30)).isoformat(),
            "current_time": now,
        },
    )
    assert not res.passed
    assert "consecutive" in res.message.lower()

    # Same losses but 300 minutes ago → cooldown expired, passes
    res2 = await gate.check(
        {"strategy": "MB"},
        {
            "total_capital": 100000.0,
            "strategy_daily_pnl": -100.0,
            "strategy_consecutive_losses": 2,
            "strategy_last_loss_at": (now - timedelta(minutes=300)).isoformat(),
            "current_time": now,
        },
    )
    assert res2.passed


@pytest.mark.asyncio
async def test_g18_zero_daily_pnl_passes():
    gate = G18StrategyGuard(_G18_CFG)
    res = await gate.check(
        {"strategy": "SIC"},
        {"total_capital": 100000.0, "strategy_daily_pnl": 0.0, "current_time": datetime.now(IST)},
    )
    assert res.passed


def test_risk_engine_has_18_gates():
    re_ = RiskEngine({})
    assert len(re_.gates) == 19
    names = [g.__class__.__name__ for g in re_.gates]
    assert "G17CostPreCheck" in names and "G18StrategyGuard" in names


# ─────────────────────────────────────────────────────────────────────────────
# 6. Real-trades-only performance (repository)
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_compute_strategy_stats_zero_trades(async_session):
    repo = Repository(async_session)
    stats = await repo.compute_strategy_stats("PTC")
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0.0
    assert stats["profit_factor"] == 0.0
    assert stats["source"] == "trades_ledger"


@pytest.mark.asyncio
async def test_compute_strategy_stats_real_trades(async_session):
    repo = Repository(async_session)
    now = datetime.now(IST)
    # 3 closed trades: +200, -100, +150 → 66.67% WR, PF = 350/100 = 3.5
    for i, pnl in enumerate([200.0, -100.0, 150.0]):
        await repo.create_trade(
            symbol="TCS",
            direction="LONG",
            strategy="PTC",
            entry_price=3500.0,
            exit_price=3500.0 + pnl / 10,
            quantity=10,
            status="CLOSED",
            pnl=pnl,
            net_pnl=pnl,
            exit_time=(now - timedelta(minutes=10 * (3 - i))).isoformat(),
            holding_duration_seconds=600,
            extra={"regime": "Bull"},
        )
    # One OPEN trade that must be EXCLUDED from stats
    await repo.create_trade(
        symbol="TCS",
        direction="LONG",
        strategy="PTC",
        entry_price=3500.0,
        quantity=10,
        status="OPEN",
        pnl=0.0,
        net_pnl=0.0,
        extra={"regime": "Bull"},
    )
    # One closed trade of ANOTHER strategy that must be excluded
    await repo.create_trade(
        symbol="INFY",
        direction="SHORT",
        strategy="MB",
        entry_price=1500.0,
        quantity=5,
        status="CLOSED",
        pnl=-50.0,
        net_pnl=-50.0,
        extra={"regime": "Bear"},
    )

    stats = await repo.compute_strategy_stats("PTC")
    assert stats["total_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(66.67, abs=0.01)
    assert stats["total_pnl"] == pytest.approx(250.0)
    assert stats["profit_factor"] == pytest.approx(3.5)
    assert stats["max_consecutive_losses"] == 1
    assert stats["source"] == "trades_ledger"


@pytest.mark.asyncio
async def test_get_today_closed_trades_by_strategy(async_session):
    repo = Repository(async_session)
    now = datetime.now(IST)
    await repo.create_trade(
        symbol="TCS", direction="LONG", strategy="MRF", entry_price=100.0,
        quantity=1, status="CLOSED", pnl=-40.0, net_pnl=-40.0,
        exit_time=now.isoformat(),
    )
    trades = await repo.get_today_closed_trades_by_strategy("MRF")
    assert len(trades) == 1
    assert float(trades[0].net_pnl) == -40.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. Shadow stats + regime attribution
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_signal_stats(async_session):
    repo = Repository(async_session)
    for status in ["SHADOW", "SHADOW_TARGET", "SHADOW_TARGET", "SHADOW_SL", "SHADOW_EXPIRED"]:
        await repo.create_signal(
            symbol="TCS", direction="LONG", strategy="TRS", confidence=0.75,
            entry_price=100.0, stop_loss=98.0, target=104.0, status=status,
        )
    stats = await repo.compute_shadow_signal_stats()
    assert "TRS" in stats
    s = stats["TRS"]
    assert s["total_signals"] == 5
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert s["expired"] == 1
    assert s["pending"] == 1
    assert s["signal_win_rate"] == pytest.approx(66.67, abs=0.01)


@pytest.mark.asyncio
async def test_regime_attribution(async_session):
    repo = Repository(async_session)
    await repo.create_trade(
        symbol="TCS", direction="LONG", strategy="PTC", entry_price=100.0,
        quantity=1, status="CLOSED", pnl=50.0, net_pnl=50.0, extra={"regime": "Bull"},
    )
    await repo.create_trade(
        symbol="INFY", direction="SHORT", strategy="PTC", entry_price=200.0,
        quantity=1, status="CLOSED", pnl=-20.0, net_pnl=-20.0, extra={"regime": "Bear"},
    )
    rows = await repo.get_regime_attribution()
    by_key = {(r["strategy"], r["regime"]): r for r in rows}
    assert by_key[("PTC", "Bull")]["total_trades"] == 1
    assert by_key[("PTC", "Bull")]["win_rate"] == 100.0
    assert by_key[("PTC", "Bear")]["losses"] == 1


@pytest.mark.asyncio
async def test_regime_attribution_empty(async_session):
    repo = Repository(async_session)
    rows = await repo.get_regime_attribution()
    assert rows == []


# ─────────────────────────────────────────────────────────────────────────────
# 8. Engine time-stop / fail-fast helpers + manage_position exits
# ─────────────────────────────────────────────────────────────────────────────

def _engine_stub():
    from core.engine import UltraBotEngine

    eng = MagicMock(spec=UltraBotEngine)
    eng._time_stop_map = {"PTC": 75, "MB": 45}
    eng._time_stop_default = 90
    eng._fail_fast_atr_mults = {"MB": 0.75}
    eng._fail_fast_window_minutes = 15
    eng._errors_count = 0
    eng.session_id = "phase1-test"
    eng.error_engine = MagicMock()
    eng.error_engine.handle_error = AsyncMock()
    eng._close_position = AsyncMock()
    eng.partial_booker = None
    eng.market_hours = None
    # Bind the REAL Phase 1 helper methods (the spec'd mock would return
    # MagicMock objects instead of executing the real logic). Staticmethods
    # are already plain functions after class-attribute access; the instance
    # method needs explicit binding.
    eng._position_extra_dict = UltraBotEngine._position_extra_dict
    eng._time_stop_for = UltraBotEngine._time_stop_for.__get__(eng, UltraBotEngine)
    eng._position_age_minutes = UltraBotEngine._position_age_minutes

    repo = MagicMock()
    repo.update_position = AsyncMock()
    eng._repo_context = MagicMock()

    class RepoCtx:
        async def __aenter__(self):
            return repo

        async def __aexit__(self, exc_type, exc, tb):
            pass

    eng._repo_context.return_value = RepoCtx()
    return eng


def _position(strategy, entry_price, sl, target, entry_minutes_ago, direction="LONG", extra=None):
    pos = MagicMock()
    pos.id = "pos-1"
    pos.trade_id = "trade-1"
    pos.symbol = "TCS"
    pos.direction = direction
    pos.strategy = strategy
    pos.entry_price = entry_price
    pos.stop_loss = sl
    pos.target = target
    pos.quantity = 10
    pos.current_price = entry_price
    pos.entry_time = (datetime.now(IST) - timedelta(minutes=entry_minutes_ago)).isoformat()
    pos.extra = extra if extra is not None else {}
    return pos


def test_time_stop_lookup():
    eng = _engine_stub()
    from core.engine import UltraBotEngine

    assert UltraBotEngine._time_stop_for(eng, "MB") == 45
    assert UltraBotEngine._time_stop_for(eng, "PTC") == 75
    assert UltraBotEngine._time_stop_for(eng, "UNKNOWN") == 90


@pytest.mark.asyncio
async def test_manage_position_time_stop_exit():
    from core.engine import UltraBotEngine

    eng = _engine_stub()
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=3500.0)
    # MB position held 60 min (budget 45), price mid-range
    pos = _position("MB", 3500.0, 3440.0, 3620.0, entry_minutes_ago=60, extra={"strategy": "MB"})
    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos)
    eng._close_position.assert_awaited_once()
    kwargs = eng._close_position.await_args.kwargs
    assert kwargs["close_reason"] == "time_stop"


@pytest.mark.asyncio
async def test_manage_position_fail_fast_exit():
    from core.engine import UltraBotEngine

    eng = _engine_stub()
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=3490.0)  # ₹10 adverse
    # MB position 10 min old, entry ATR 12 → 0.75×12 = 9 ≤ 10 adverse
    pos = _position(
        "MB", 3500.0, 3382.0, 3620.0, entry_minutes_ago=10,
        extra={"strategy": "MB", "entry_atr": 12.0},
    )
    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos)
    eng._close_position.assert_awaited_once()
    kwargs = eng._close_position.await_args.kwargs
    assert kwargs["close_reason"] == "fail_fast"


@pytest.mark.asyncio
async def test_manage_position_fail_fast_window_expired():
    from core.engine import UltraBotEngine

    eng = _engine_stub()
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=3490.0)  # ₹10 adverse
    # MB position 30 min old (window 15) → fail-fast NOT applicable
    pos = _position(
        "MB", 3500.0, 3382.0, 3620.0, entry_minutes_ago=30,
        extra={"strategy": "MB", "entry_atr": 12.0},
    )
    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos)
    eng._close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_manage_position_no_exits_when_healthy():
    from core.engine import UltraBotEngine

    eng = _engine_stub()
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=3510.0)  # ₹10 favorable
    # PTC position 30 min old (budget 75), price between SL and target
    pos = _position("PTC", 3500.0, 3465.0, 3560.0, entry_minutes_ago=30, extra={"strategy": "PTC"})
    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos)
    eng._close_position.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# 9. Shadow-mode integration: _scan_symbol records SHADOW signals without
#    creating opportunities, and _evaluate_shadow_signals resolves outcomes.
# ─────────────────────────────────────────────────────────────────────────────

def _scan_engine_stub(repo):
    from core.engine import UltraBotEngine

    eng = MagicMock(spec=UltraBotEngine)
    eng.vix_critical_stale = False
    eng.active_strategies = ["TRS"]
    eng.shadow_strategies = {"TRS"}
    eng._shadow_signals = {}
    eng._shadow_max_age_minutes = 90
    # v0.4.11: bind the REAL recorder machinery so the stub exercises the
    # actual registration path (autospec mocks would swallow registrations).
    eng._shadow_recorder_enabled = True
    eng._register_shadow = UltraBotEngine._register_shadow.__get__(eng)
    eng._shadow_realtime = MagicMock(return_value=True)
    eng.current_regime = "Bull"
    eng.vix = 14.0
    eng.session_id = "phase1-shadow-test"
    eng._signals_generated = 0
    eng._signals_passed_count = 0
    eng._signals_rejected_count = 0
    eng._errors_count = 0
    eng._rejections_by_gate = {}
    eng._rejections_by_strategy = {}
    eng._opportunities_lock = __import__("asyncio").Lock()
    eng.pending_opportunities = {}
    eng.invalidated_opportunities = {}
    eng._record_telemetry_event = MagicMock()
    eng._broadcast = AsyncMock()
    eng._execute_strategy_scan = AsyncMock(
        return_value={
            "symbol": "TCS",
            "direction": "BUY",
            "entry_price": 3500.0,
            "sl_price": 3465.0,
            "target_price": 3560.0,
            "confidence": 0.8,
            "strategy": "TRS",
            "risk_reward": 1.7,
            "extra_details": {"half_size": True},
        }
    )
    eng._run_risk_gates = AsyncMock(
        return_value={"passed": True, "all_gates": [{"gate": "G1", "passed": True}], "notes": "ok"}
    )
    eng._calculate_position_size = AsyncMock(return_value={"quantity": 10, "position_size": 35000})
    eng._build_opportunity = MagicMock(side_effect=AssertionError("shadow strategies must NOT create opportunities"))
    eng.market_hours = None
    eng.stale_candle_max_age_minutes = 0  # disable stale-data guard in tests

    class RepoCtx:
        async def __aenter__(self):
            return repo

        async def __aexit__(self, exc_type, exc, tb):
            pass

    eng._repo_context = MagicMock(return_value=RepoCtx())
    return eng


@pytest.mark.asyncio
async def test_scan_symbol_shadow_mode_records_without_opportunity(async_session):
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _scan_engine_stub(repo)

    # 25 valid candles (list-of-dicts, as the feed provides)
    now = datetime.now(IST)
    candles = [
        {"open": 3490 + i, "high": 3495 + i, "low": 3485 + i, "close": 3492 + i,
         "volume": 1000, "time": (now - timedelta(minutes=5 * (25 - i))).isoformat()}
        for i in range(25)
    ]
    eng.feed = MagicMock()
    eng.feed.get_candles = AsyncMock(return_value=candles)
    eng.broker = None

    await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo)

    # Signal recorded with SHADOW status
    signals = await repo.get_signals_by_strategy("TRS", limit=10)
    assert len(signals) == 1
    assert signals[0].status == "SHADOW"
    assert float(signals[0].entry_price) == 3500.0
    assert signals[0].regime_at_signal == "Bull"

    # No opportunity was created; the shadow registry holds the live signal
    assert eng.pending_opportunities == {}
    assert len(eng._shadow_signals) == 1


@pytest.mark.asyncio
async def test_evaluate_shadow_signals_resolves_target(async_session):
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _scan_engine_stub(repo)

    # Create the shadow signal directly
    sig_obj = await repo.create_signal(
        symbol="TCS", direction="BUY", strategy="TRS", confidence=0.8,
        entry_price=3500.0, stop_loss=3465.0, target=3560.0, status="SHADOW",
        signal_data={"strategy": "TRS"},
    )
    eng._shadow_signals[sig_obj.id] = {
        "signal_id": sig_obj.id,
        "symbol": "TCS",
        "direction": "BUY",
        "strategy": "TRS",
        "entry_price": 3500.0,
        "stop_loss": 3465.0,
        "target": 3560.0,
        "created_at": sig_obj.created_at,
        "signal_data": {},
    }

    # Live price above target → SHADOW_TARGET
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=3575.0)
    eng.broker = None

    await UltraBotEngine._evaluate_shadow_signals.__get__(eng, UltraBotEngine)()

    updated = await repo.get_signal(sig_obj.id)
    assert updated.status == "SHADOW_TARGET"
    assert updated.signal_data is not None
    assert "shadow_result" in (updated.signal_data or "{}")
    assert eng._shadow_signals == {}

    stats = await repo.compute_shadow_signal_stats()
    assert stats["TRS"]["wins"] == 1
    assert stats["TRS"]["signal_win_rate"] == 100.0
