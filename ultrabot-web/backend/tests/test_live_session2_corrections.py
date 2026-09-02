"""Live-session run 2 corrections (2026-08-28) — regression tests.

Covers the three defects found while re-validating the live paper
session:

1. TEST-DB POLLUTION (the big one): the suite ran against the real
   ``data/ultrabot.db`` and ``test_feed_outage_degradation`` DELETED the
   live session's watchlist mid-market. Fixed by ``tests/conftest.py``
   redirecting ``DB_PATH`` to a per-run temp database — verified here.
2. OPPOSING_SIGNAL_SUPERSEDED orphaned the superseded signal at status
   'pending' forever (observed live: RELIANCE MRF BUY 11:33 IST).
   Fixed: the supersede path now expires the linked signal in the DB.
3. Engine restarts orphan in-memory pending opportunities' signals at
   'pending' (observed live: HCLTECH 09:44, DABUR/HINDALCO prior day).
   Fixed: ``_expire_orphaned_pending_signals()`` sweeps them at start().
4. BONUS BUG: the TTL-expiry path passed ``notes=`` to update_signal,
   but the Signal model has no ``notes`` column — the expiry reason was
   silently dropped. Now writes ``rejection_reason`` (verified here).
"""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from db.migrations import Base
from db.repository import Repository

IST = ZoneInfo("Asia/Kolkata")

PROD_DB = Path(__file__).resolve().parent.parent / "data" / "ultrabot.db"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
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


def _repo_ctx(repo):
    class RepoCtx:
        async def __aenter__(self):
            return repo

        async def __aexit__(self, exc_type, exc, tb):
            pass

    return RepoCtx()


def _engine_stub(repo):
    """Minimal engine stub for driving _scan_symbol / sweeps directly."""
    from core.engine import UltraBotEngine

    eng = MagicMock(spec=UltraBotEngine)
    eng.vix_critical_stale = False
    eng.active_strategies = ["SIC"]
    eng.shadow_strategies = set()
    eng._shadow_signals = {}
    eng._shadow_max_age_minutes = 90
    eng.current_regime = "Sideways"
    eng.vix = 11.0
    eng.session_id = "live-session2-test"
    eng._signals_generated = 0
    eng._signals_passed_count = 0
    eng._signals_rejected_count = 0
    eng._errors_count = 0
    eng._rejections_by_gate = {}
    eng._rejections_by_strategy = {}
    eng._opportunities_lock = asyncio.Lock()
    eng.pending_opportunities = {}
    eng.invalidated_opportunities = {}
    eng._record_telemetry_event = MagicMock()
    eng._broadcast = AsyncMock()
    eng._run_risk_gates = AsyncMock(
        return_value={"passed": True, "all_gates": [{"gate": "G1", "passed": True}]}
    )
    eng._calculate_position_size = AsyncMock(return_value={"quantity": 10, "position_size": 35000})
    eng._build_opportunity = MagicMock(
        return_value={
            "id": "opp-new-sic-1",
            "symbol": "TCS",
            "direction": "SELL",
            "strategy": "SIC",
            "confidence": 0.85,
            "signal_id": None,
        }
    )
    eng.market_hours = None
    eng.stale_candle_max_age_minutes = 0  # disable stale-data guard in tests
    eng._repo_context = MagicMock(return_value=_repo_ctx(repo))
    return eng


def _candles(n=25, base=3500.0):
    now = datetime.now(IST)
    return [
        {
            "open": base + i, "high": base + i + 5, "low": base + i - 5,
            "close": base + i + 2, "volume": 1000,
            "time": (now - timedelta(minutes=5 * (n - i))).isoformat(),
        }
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Test-suite DB isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_suite_runs_on_isolated_temp_database():
    """The conftest must redirect every test onto a temp DB — never the
    production data/ultrabot.db (live watchlist was wiped by tests once)."""
    import db.database as dbmod

    assert os.environ.get("DB_PATH"), "conftest must set DB_PATH before imports"
    assert "ultrabot_test.db" in dbmod.DB_PATH
    assert Path(dbmod.DB_PATH).resolve() != PROD_DB.resolve()
    # The module-level engine (used by init_db / app fixtures) is bound to
    # the same isolated path.
    assert str(PROD_DB) not in str(dbmod.DATABASE_URL)


@pytest.mark.asyncio
async def test_watchlist_writes_never_reach_production_db():
    """Simulate exactly what the polluting tests do (seed + wholesale
    delete of active watchlist rows) and prove the production DB file is
    untouched afterwards."""
    import db.database as dbmod
    from db.migrations import WatchlistItem  # noqa: F401  (schema import)

    prod_before = PROD_DB.read_bytes() if PROD_DB.exists() else b""

    await dbmod.init_db()
    factory = dbmod.async_session_factory
    async with factory() as session:
        repo = Repository(session)
        # Exactly what the polluting tests do: seed rows, then wholesale
        # DELETE of every active watchlist row.
        await repo.add_watchlist_item(symbol="RELIANCE", name="Reliance Industries", is_active=True)
        await repo.add_watchlist_item(symbol="TCS", name="Tata Consultancy", is_active=True)
        for wl in await repo.get_active_watchlist():
            await repo.delete_watchlist_item(wl.id)
        await session.commit()

    prod_after = PROD_DB.read_bytes() if PROD_DB.exists() else b""
    assert prod_before == prod_after, "production DB mutated by test-suite watchlist writes!"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Opposing-signal supersede resolves the superseded signal
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opposing_supersede_expires_superseded_signal(async_session):
    """Live repro: MRF BUY opp (conf 0.84) superseded 44ms later by SIC SELL
    (conf 0.85) — the MRF signal must be expired in the DB, not orphaned at
    'pending' forever."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _engine_stub(repo)

    # The pre-existing pending MRF BUY opportunity + its DB signal
    mrf_sig = await repo.create_signal(
        symbol="TCS", direction="BUY", strategy="MRF", confidence=0.84,
        entry_price=3500.0, stop_loss=3470.0, target=3560.0, status="pending",
        signal_data={"strategy": "MRF"},
    )
    eng.pending_opportunities["opp-mrf-1"] = {
        "id": "opp-mrf-1",
        "symbol": "TCS",
        "direction": "BUY",
        "strategy": "MRF",
        "confidence": 0.84,
        "entry_price": 3500.0,
        "stop_loss": 3470.0,
        "target": 3560.0,
        "created_at": datetime.now(IST).isoformat(),
        "signal_id": mrf_sig.id,
    }

    # The scan returns a HIGHER-conviction OPPOSING signal (SIC SELL)
    eng._execute_strategy_scan = AsyncMock(
        return_value={
            "symbol": "TCS",
            "direction": "SELL",
            "entry_price": 3500.0,
            "sl_price": 3530.0,
            "target_price": 3440.0,
            "confidence": 0.85,
            "strategy": "SIC",
            "risk_reward": 2.0,
        }
    )
    eng.feed = MagicMock()
    eng.feed.get_candles = AsyncMock(return_value=_candles())
    eng.broker = None

    await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo, open_positions=[])

    # The old opportunity was superseded and REMOVED from pending
    assert "opp-mrf-1" not in eng.pending_opportunities
    assert "opp-mrf-1" in eng.invalidated_opportunities
    assert (
        eng.invalidated_opportunities["opp-mrf-1"]["invalidation_code"]
        == "OPPOSING_SIGNAL_SUPERSEDED"
    )
    # The new SIC opportunity took its place
    assert "opp-new-sic-1" in eng.pending_opportunities

    # THE FIX: the superseded MRF signal is resolved in the DB
    mrf_after = await repo.get_signal(mrf_sig.id)
    assert mrf_after.status == "EXPIRED"
    assert mrf_after.rejection_reason and "Superseded" in mrf_after.rejection_reason

    # The new SIC signal is pending, linked to the new opportunity
    sic_signals = await repo.get_signals_by_strategy("SIC", limit=5)
    assert len(sic_signals) == 1
    assert sic_signals[0].status == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Startup sweep expires orphaned pending signals
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expire_orphaned_pending_signals(async_session):
    """Signals whose in-memory opportunity died with a restart must be
    expired at engine start — not left 'pending' forever."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _engine_stub(repo)

    # Two orphans from "previous runs" + non-pending signals that must survive
    orphan1 = await repo.create_signal(
        symbol="TCS", direction="BUY", strategy="MRF", confidence=0.8,
        entry_price=3500.0, status="pending", signal_data={},
    )
    orphan2 = await repo.create_signal(
        symbol="DABUR", direction="BUY", strategy="MRF", confidence=0.8,
        entry_price=388.0, status="pending", signal_data={},
    )
    expired_sig = await repo.create_signal(
        symbol="CIPLA", direction="SELL", strategy="SIC", confidence=0.8,
        entry_price=1420.0, status="EXPIRED", signal_data={},
    )
    shadow_sig = await repo.create_signal(
        symbol="INFY", direction="BUY", strategy="TRS", confidence=0.8,
        entry_price=1320.0, status="SHADOW", signal_data={},
    )
    await async_session.commit()

    expired_count = await UltraBotEngine._expire_orphaned_pending_signals.__get__(
        eng, UltraBotEngine
    )()

    assert expired_count == 2
    assert (await repo.get_signal(orphan1.id)).status == "EXPIRED"
    assert (await repo.get_signal(orphan2.id)).status == "EXPIRED"
    assert (await repo.get_signal(orphan1.id)).rejection_reason is not None
    # Non-pending statuses untouched
    assert (await repo.get_signal(expired_sig.id)).status == "EXPIRED"
    assert (await repo.get_signal(shadow_sig.id)).status == "SHADOW"


@pytest.mark.asyncio
async def test_expire_orphaned_pending_signals_handles_missing_repo(async_session):
    """A repo failure must never break engine start."""
    from core.engine import UltraBotEngine

    eng = _engine_stub(Repository(async_session))

    class BrokenCtx:
        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, exc_type, exc, tb):
            pass

    eng._repo_context = MagicMock(return_value=BrokenCtx())
    # Must not raise
    assert await UltraBotEngine._expire_orphaned_pending_signals.__get__(
        eng, UltraBotEngine
    )() == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. TTL expiry persists its reason (notes= was silently dropped)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ttl_expiry_persists_rejection_reason(async_session):
    """The TTL path wrote notes= — a column the Signal model does not
    have — so expiry reasons vanished. Must persist rejection_reason."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _engine_stub(repo)

    sig = await repo.create_signal(
        symbol="TCS", direction="BUY", strategy="SIC", confidence=0.85,
        entry_price=3500.0, stop_loss=3470.0, target=3560.0, status="pending",
        signal_data={},
    )
    eng.pending_opportunities["opp-ttl-1"] = {
        "id": "opp-ttl-1",
        "symbol": "TCS",
        "direction": "BUY",
        "strategy": "SIC",
        "confidence": 0.85,
        "entry_price": 3500.0,
        "stop_loss": 3470.0,
        "target": 3560.0,
        # Created 10 minutes ago; TTL below is 60s
        "created_at": (datetime.now(IST) - timedelta(minutes=10)).isoformat(),
        "signal_id": sig.id,
    }

    eng.config = MagicMock()
    eng.config.get_risk_config.return_value = {"opportunity_ttl_seconds": 60}
    eng.feed = None
    eng.broker = None

    await UltraBotEngine._validate_pending_opportunities.__get__(eng, UltraBotEngine)()

    assert "opp-ttl-1" not in eng.pending_opportunities
    assert "opp-ttl-1" in eng.invalidated_opportunities
    assert (
        eng.invalidated_opportunities["opp-ttl-1"]["invalidation_code"]
        == "SETUP_TIMEOUT_EXPIRED"
    )
    sig_after = await repo.get_signal(sig.id)
    assert sig_after.status == "EXPIRED"
    assert sig_after.rejection_reason and "momentum window" in sig_after.rejection_reason


# ─────────────────────────────────────────────────────────────────────────────
# 5. Actual-size cost re-check (G17 fee/risk at the SIZED quantity)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actual_size_cost_recheck_rejects_fee_heavy_small_size(async_session):
    """Live repro (2026-08-28): BHARTIARTL SIC BUY passed G17 at the 796-share
    budget estimate (~9% fee/risk) but the Kelly sizer gave 21 shares where
    round-trip fees = 46.65% of the REAL ₹131.88 risk. The post-sizing
    re-check must reject at the 30% ceiling."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _engine_stub(repo)
    eng.config = MagicMock()
    eng.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
    eng.config.get_risk_config.return_value = {"max_fee_pct_of_risk": 30.0}
    # The live-sized quantity: 21 shares, SL distance ₹6.28 → risk ₹131.88
    eng._calculate_position_size = AsyncMock(
        return_value={"quantity": 21, "position_size": 39410.7}
    )
    eng._execute_strategy_scan = AsyncMock(
        return_value={
            "symbol": "TCS",
            "direction": "BUY",
            "entry_price": 1876.7,
            "sl_price": 1870.42,
            "target_price": 1888.63,
            "confidence": 0.8,
            "strategy": "SIC",
            "risk_reward": 1.9,
        }
    )
    eng.feed = MagicMock()
    eng.feed.get_candles = AsyncMock(return_value=_candles(base=1870))
    eng.broker = None

    await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo, open_positions=[])

    # Rejected at G17 (actual-size re-check) with truthful counters
    assert eng._rejections_by_gate.get("G17_CostPreCheck") == 1
    assert eng._signals_rejected_count == 1
    assert eng._signals_passed_count == 0  # rolled back — failed post-sizing
    # No opportunity, no signal row (ledger stays clean)
    assert eng.pending_opportunities == {}
    assert await repo.get_signals_by_strategy("SIC", limit=5) == []


@pytest.mark.asyncio
async def test_actual_size_cost_recheck_allows_well_sized_trade(async_session):
    """Same signal geometry at the budget-sized quantity (796 shares →
    fees ≈ 11% of ₹4,999 risk) must sail through the re-check."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _engine_stub(repo)
    eng.config = MagicMock()
    eng.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
    eng.config.get_risk_config.return_value = {"max_fee_pct_of_risk": 30.0}
    eng._calculate_position_size = AsyncMock(
        return_value={"quantity": 796, "position_size": 394000.0}
    )
    eng._execute_strategy_scan = AsyncMock(
        return_value={
            "symbol": "TCS",
            "direction": "BUY",
            "entry_price": 1876.7,
            "sl_price": 1870.42,
            "target_price": 1888.63,
            "confidence": 0.8,
            "strategy": "SIC",
            "risk_reward": 1.9,
        }
    )
    eng.feed = MagicMock()
    eng.feed.get_candles = AsyncMock(return_value=_candles(base=1870))
    eng.broker = None

    await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo, open_positions=[])

    assert "G17_CostPreCheck" not in eng._rejections_by_gate
    assert eng._signals_passed_count == 1
    assert "opp-new-sic-1" in eng.pending_opportunities
    sigs = await repo.get_signals_by_strategy("SIC", limit=5)
    assert len(sigs) == 1 and sigs[0].status == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Fresh-session invalidated_opportunities must be a dict
#    (list init raised TypeError on first invalidation — live 13:10/13:19 IST)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fresh_session_ttl_invalidation_does_not_raise(async_session):
    """Live repro: a FRESH session (not same-day resume) initialized
    invalidated_opportunities as a list; the first TTL expiry then raised
    TypeError('list indices must be integers or slices, not str') and aborted
    the scan cycle. After the fix, invalidation works immediately after a
    fresh-session reset."""
    from core.engine import UltraBotEngine
    from core.engine import EngineState

    repo = Repository(async_session)
    eng = _engine_stub(repo)

    # Simulate exactly what start() does on a genuinely new day AFTER the fix
    eng.pending_opportunities = {}
    eng.invalidated_opportunities = {}
    eng._rejections_by_gate = {}

    sig = await repo.create_signal(
        symbol="TCS", direction="BUY", strategy="SIC", confidence=0.8,
        entry_price=3500.0, stop_loss=3470.0, target=3560.0, status="pending",
        signal_data={},
    )
    eng.pending_opportunities["opp-fresh-1"] = {
        "id": "opp-fresh-1",
        "symbol": "TCS",
        "direction": "BUY",
        "strategy": "SIC",
        "confidence": 0.8,
        "entry_price": 3500.0,
        "stop_loss": 3470.0,
        "target": 3560.0,
        "created_at": (datetime.now(IST) - timedelta(minutes=10)).isoformat(),
        "signal_id": sig.id,
    }
    eng.config = MagicMock()
    eng.config.get_risk_config.return_value = {"opportunity_ttl_seconds": 60}
    eng.feed = None
    eng.broker = None

    # Must not raise (this exact call crashed live with a list-initialized
    # invalidated_opportunities)
    await UltraBotEngine._validate_pending_opportunities.__get__(eng, UltraBotEngine)()

    assert "opp-fresh-1" not in eng.pending_opportunities
    assert "opp-fresh-1" in eng.invalidated_opportunities  # dict membership
    assert (await repo.get_signal(sig.id)).status == "EXPIRED"


def test_start_resets_invalidated_opportunities_to_dict():
    """Static guard: the fresh-session reset must not regress to a list."""
    import inspect
    from core.engine import UltraBotEngine

    src = inspect.getsource(UltraBotEngine.start)
    assert "self.invalidated_opportunities = []" not in src, (
        "start() must reset invalidated_opportunities to {} (dict) — "
        "a list breaks every invalidation path with TypeError"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. Direction normalization (BUY/SELL positions managed with inverted logic)
#    Live repro: ASIANPAINT BUY 13:23 IST "stopped out" ABOVE its SL with a
#    sign-inverted +₹12 gross on a −₹12 fill — raw `direction == "LONG"`
#    comparisons sent every BUY/SELL position down the SHORT branch.
# ─────────────────────────────────────────────────────────────────────────────

def _managed_position(direction="BUY", entry=2593.60, sl=2587.42, target=2613.04, qty=15):
    pos = MagicMock()
    pos.symbol = "ASIANPAINT"
    pos.direction = direction          # production emits BUY/SELL
    pos.entry_price = entry
    pos.stop_loss = sl
    pos.target = target
    pos.quantity = qty
    pos.current_price = entry
    pos.id = "pos-test-1"
    pos.trade_id = "trade-test-1"
    pos.extra = "{}"
    pos.strategy = "MRF"
    pos.entry_time = datetime.now(IST).isoformat()
    return pos


def _manage_engine(repo, price):
    from core.engine import UltraBotEngine

    eng = MagicMock(spec=UltraBotEngine)
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=price)
    eng.broker = None
    eng.market_hours = None
    eng._close_position = AsyncMock()
    eng._repo_context = MagicMock(return_value=_repo_ctx(repo))
    eng._position_extra_dict = MagicMock(return_value={})
    eng._position_age_minutes = MagicMock(return_value=2.0)
    eng._time_stop_for = MagicMock(return_value=45)
    eng._fail_fast_atr_mults = {}
    eng.partial_booker = None
    eng.daily_risk = None
    return eng


@pytest.mark.asyncio
async def test_buy_position_above_sl_does_not_stop_out(async_session):
    """Live repro: BUY @2593.60, SL 2587.42, LTP 2592.80 — price is ABOVE the
    SL, so NO stop may fire (the bug closed it here with reason stop_loss)."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _manage_engine(repo, price=2592.80)
    pos = _managed_position(direction="BUY")

    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos, repo)

    eng._close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_position_sl_hit_negative_pnl(async_session):
    """BUY @2593.60, SL 2587.42, LTP 2587.00 → stop fires with NEGATIVE gross
    P&L ((2587.00-2593.60)×15 = -99.00), not the sign-inverted +99."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _manage_engine(repo, price=2587.00)
    pos = _managed_position(direction="BUY")

    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos, repo)

    eng._close_position.assert_awaited_once()
    kwargs = eng._close_position.await_args.kwargs
    assert kwargs.get("close_reason") == "stop_loss"
    assert kwargs.get("pnl_amount") == pytest.approx(-99.00)


@pytest.mark.asyncio
async def test_buy_position_target_hit_positive_pnl(async_session):
    """BUY @2593.60, target 2613.04, LTP 2613.50 → target fires with POSITIVE
    gross ((2613.50-2593.60)×15 = +298.50)."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _manage_engine(repo, price=2613.50)
    pos = _managed_position(direction="BUY")

    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos, repo)

    eng._close_position.assert_awaited_once()
    kwargs = eng._close_position.await_args.kwargs
    assert kwargs.get("close_reason") == "target"
    assert kwargs.get("pnl_amount") == pytest.approx(298.50)


@pytest.mark.asyncio
async def test_sell_position_sl_and_target_directions(async_session):
    """SELL @2593.60, SL 2599.78 (above), target 2574.16 (below):
    LTP 2595.00 (between) → no close; LTP 2600.00 (>= SL) → stop with
    negative pnl ((2593.60-2600.00)×15 = -96.00)."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)

    # Between SL and target — no exit
    eng = _manage_engine(repo, price=2595.00)
    pos = _managed_position(direction="SELL", sl=2599.78, target=2574.16)
    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos, repo)
    eng._close_position.assert_not_awaited()

    # SL hit for the short (price >= SL)
    eng2 = _manage_engine(repo, price=2600.00)
    pos2 = _managed_position(direction="SELL", sl=2599.78, target=2574.16)
    await UltraBotEngine._manage_position.__get__(eng2, UltraBotEngine)(pos2, repo)
    eng2._close_position.assert_awaited_once()
    kwargs = eng2._close_position.await_args.kwargs
    assert kwargs.get("close_reason") == "stop_loss"
    assert kwargs.get("pnl_amount") == pytest.approx(-96.00)


@pytest.mark.asyncio
async def test_sell_position_target_positive_pnl(async_session):
    """SELL @2593.60, target 2574.16, LTP 2574.00 → target with POSITIVE gross
    ((2593.60-2574.00)×15 = +294.00)."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = _manage_engine(repo, price=2574.00)
    pos = _managed_position(direction="SELL", sl=2599.78, target=2574.16)

    await UltraBotEngine._manage_position.__get__(eng, UltraBotEngine)(pos, repo)

    eng._close_position.assert_awaited_once()
    kwargs = eng._close_position.await_args.kwargs
    assert kwargs.get("close_reason") == "target"
    assert kwargs.get("pnl_amount") == pytest.approx(294.00)


def test_is_long_direction_normalization():
    from core.engine import _is_long_direction

    assert _is_long_direction("BUY") is True
    assert _is_long_direction("LONG") is True
    assert _is_long_direction("buy") is True
    assert _is_long_direction("SELL") is False
    assert _is_long_direction("SHORT") is False
    assert _is_long_direction("sell") is False
    assert _is_long_direction(None) is False
    assert _is_long_direction("") is False


# ─────────────────────────────────────────────────────────────────────────────
# 8. Close-path fees: full round trip ONCE (no double-count with the
#    entry-time estimate) + BUY gross P&L from real fills.
#    Live repro: ASIANPAINT recorded ₹99.42 fees (38.08 entry estimate +
#    61.34 round trip) vs the true ₹61.33 round trip.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_position_records_round_trip_fees_once(async_session):
    """Exact live repro: BUY 15 @ 2593.60, exit fill 2592.80 →
    gross pnl -12.00, round-trip fees 61.33, net -73.33."""
    from core.engine import UltraBotEngine

    repo = Repository(async_session)
    eng = MagicMock(spec=UltraBotEngine)
    eng.broker = MagicMock()
    eng.broker.place_order = AsyncMock(
        return_value={"status": "FILLED", "filled_price": 2592.80, "order_id": "PAPER-T1"}
    )
    eng._derive_order_exchange = MagicMock(return_value="NSE")
    eng._position_segment = MagicMock(return_value="EQ")
    eng.error_engine = MagicMock()
    eng.error_engine.handle_error = AsyncMock()
    eng.config = MagicMock()
    eng.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
    eng._repo_context = MagicMock(return_value=_repo_ctx(repo))
    repo.update_trade = AsyncMock()
    repo.update_position = AsyncMock()
    eng._broadcast = AsyncMock()
    eng._route_alert = AsyncMock()
    eng.daily_risk = None
    eng.performance_tracker = None
    eng.session_id = "close-fee-test"
    # v0.4.8: bind the REAL extra parser (MagicMock.__float__ -> 1.0 would
    # fabricate a phantom partial-booking leg in the close-path merge).
    eng._position_extra_dict = UltraBotEngine._position_extra_dict

    pos = _managed_position(direction="BUY", entry=2593.60, sl=2587.42, target=2613.04, qty=15)

    await UltraBotEngine._close_position.__get__(eng, UltraBotEngine)(
        pos, exit_price=2592.80, close_reason="stop_loss",
        pnl_amount=-12.00, pnl_pct=-0.03,
    )

    repo.update_trade.assert_awaited_once()
    kwargs = repo.update_trade.await_args.kwargs
    assert kwargs["exit_price"] == 2592.80
    assert kwargs["pnl"] == pytest.approx(-12.00)          # BUY: exit below entry
    assert kwargs["fees"] == pytest.approx(61.33, abs=0.05)  # round trip ONCE
    assert kwargs["net_pnl"] == pytest.approx(-73.33, abs=0.10)
