"""v0.4.11 — universal shadow-outcome recorder tests.

Covers: pure recorder logic (shadow/shadow_utils.py), the ShadowOutcome DB
model + repository clock aggregation, and the engine's registration hook
behavior (kind tagging, dedupe, never-raises guarantee).
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from db.migrations import Base
from db.repository import Repository
from shadow.shadow_utils import (
    KIND_GATE_BLOCKED,
    KIND_NEVER_TRADED,
    KIND_STRATEGY_SHADOW,
    OUTCOME_EXPIRED,
    OUTCOME_SL,
    OUTCOME_TARGET,
    compute_shadow_outcome,
    extract_blocking_gates,
    feed_is_realtime,
    pnl_per_share,
    should_expire,
    update_excursion,
)


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ────────────────────────────────────────
# Pure logic
# ────────────────────────────────────────

class TestFeedIsRealtime:
    def test_no_feed_is_not_realtime(self):
        assert feed_is_realtime(None) is False

    def test_backup_feed_flagged(self):
        class FM:
            _using_backup = True
        assert feed_is_realtime(FM()) is False

    def test_primary_feedmanager_realtime(self):
        class FM:
            _using_backup = False
        assert feed_is_realtime(FM()) is True

    def test_bare_feed_defaults_realtime(self):
        class PlainFeed:
            pass
        assert feed_is_realtime(PlainFeed()) is True


class TestExtractBlockingGates:
    def test_extracts_failed_gate_names(self):
        risk = {"all_gates": [
            {"gate_name": "G1_MaxPositions", "passed": True},
            {"gate_name": "G15_TTL", "passed": False},
            {"gate_name": "G2_SectorConcentration", "passed": False},
        ]}
        assert extract_blocking_gates(risk) == ["G15_TTL", "G2_SectorConcentration"]

    def test_tolerates_garbage(self):
        assert extract_blocking_gates(None) == []
        assert extract_blocking_gates({}) == []
        assert extract_blocking_gates({"all_gates": "junk"}) == []
        assert extract_blocking_gates({"all_gates": [1, "x", {"passed": False}]}) == ["UNKNOWN_GATE"]

    def test_alt_key_shapes(self):
        assert extract_blocking_gates({"all_gates": [{"gate": "G9", "passed": False}]}) == ["G9"]


class TestOutcomeAndExcursion:
    def test_long_target_and_sl(self):
        assert compute_shadow_outcome(100, 95, 110, 111, True) == (OUTCOME_TARGET, 110)
        assert compute_shadow_outcome(100, 95, 110, 94, True) == (OUTCOME_SL, 95)
        assert compute_shadow_outcome(100, 95, 110, 100, True) is None

    def test_short_mirrored(self):
        assert compute_shadow_outcome(100, 105, 90, 89, False) == (OUTCOME_TARGET, 90)
        assert compute_shadow_outcome(100, 105, 90, 106, False) == (OUTCOME_SL, 105)

    def test_unusable_geometry_never_resolves(self):
        assert compute_shadow_outcome(0, 95, 110, 111, True) is None
        assert compute_shadow_outcome(100, 0, 110, 111, True) is None
        assert compute_shadow_outcome(100, 95, 0, 111, True) is None
        assert compute_shadow_outcome(100, 95, 110, 0, True) is None

    def test_excursion_long(self):
        state = {"entry_price": 100.0, "mfe": 0.0, "mae": 0.0}
        update_excursion(state, 103.0, True)
        update_excursion(state, 99.0, True)
        update_excursion(state, 102.0, True)
        assert state["mfe"] == pytest.approx(3.0)
        assert state["mae"] == pytest.approx(1.0)

    def test_excursion_short_mirrored(self):
        state = {"entry_price": 100.0, "mfe": 0.0, "mae": 0.0}
        update_excursion(state, 97.0, False)   # favorable for short
        update_excursion(state, 101.0, False)  # adverse
        assert state["mfe"] == pytest.approx(3.0)
        assert state["mae"] == pytest.approx(1.0)

    def test_excursion_ignores_zero_price(self):
        state = {"entry_price": 100.0, "mfe": 0.0, "mae": 0.0}
        update_excursion(state, 0.0, True)
        assert state["mfe"] == 0.0 and state["mae"] == 0.0

    def test_pnl_per_share(self):
        assert pnl_per_share(100, 110, True) == pytest.approx(10.0)
        assert pnl_per_share(100, 95, False) == pytest.approx(5.0)
        assert pnl_per_share(100, 95, True) == pytest.approx(-5.0)

    def test_should_expire(self):
        assert should_expire(91, 90, eod=False) is True
        assert should_expire(89, 90, eod=False) is False
        assert should_expire(None, 90, eod=True) is True
        assert should_expire(None, 90, eod=False) is False


# ────────────────────────────────────────
# DB: ShadowOutcome + clock aggregation
# ────────────────────────────────────────

def _mk_outcome(**over):
    base = dict(
        symbol="CIPLA", direction="LONG", strategy="SIC",
        kind=KIND_NEVER_TRADED, never_traded_reason="SETUP_TIMEOUT_EXPIRED",
        entry_price=1384.0, stop_loss=1370.0, target=1410.0,
        exit_price=1410.0, outcome=OUTCOME_TARGET, pnl_per_share=26.0,
        mfe=27.5, mae=2.0, feed_realtime_registered=True,
        feed_realtime_resolved=True, blocking_gates=[],
        registered_at="2026-09-07T09:47:00+05:30",
        resolved_at="2026-09-07T09:52:00+05:30",
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_create_and_aggregate_shadow_clock(async_session):
    repo = Repository(async_session)
    await repo.create_shadow_outcome(**_mk_outcome())
    await repo.create_shadow_outcome(**_mk_outcome(
        outcome=OUTCOME_SL, exit_price=1370.0, pnl_per_share=-14.0,
        strategy="ORB",
    ))
    # Backup-feed sample: recorded but must NOT count toward the clock
    await repo.create_shadow_outcome(**_mk_outcome(
        outcome=OUTCOME_TARGET, feed_realtime_registered=False,
    ))
    # Unverifiable feed at resolve: also flagged out
    await repo.create_shadow_outcome(**_mk_outcome(
        outcome=OUTCOME_SL, exit_price=1370.0, pnl_per_share=-14.0,
        feed_realtime_resolved=False,
    ))
    # Gate-blocked row with gate context (expired at prevailing price)
    await repo.create_shadow_outcome(**_mk_outcome(
        kind=KIND_GATE_BLOCKED, never_traded_reason="GATE_BLOCKED",
        outcome=OUTCOME_EXPIRED, exit_price=1395.0, pnl_per_share=11.0,
        blocking_gates=["G15_TTL", "G18_Spread"],
    ))

    clock = await repo.get_shadow_clock()
    assert clock["resolved_today"] == 5            # everything recorded
    assert clock["realtime_resolved"] == 3         # ladder-eligible only
    assert clock["wins"] == 1
    assert clock["losses"] == 1
    assert clock["expired"] == 1
    assert clock["win_rate_pct"] == pytest.approx(33.33)
    assert set(clock["per_strategy"].keys()) == {"SIC", "ORB"}


@pytest.mark.asyncio
async def test_shadow_clock_empty_day(async_session):
    repo = Repository(async_session)
    clock = await repo.get_shadow_clock()
    assert clock["resolved_today"] == 0
    assert clock["realtime_resolved"] == 0
    assert clock["win_rate_pct"] == 0.0
    assert clock["per_strategy"] == {}


# ────────────────────────────────────────
# Engine registration hook
# ────────────────────────────────────────

def _make_engine(feed=None, enabled=True):
    import core.engine as eng_mod

    eng = object.__new__(eng_mod.UltraBotEngine)
    eng._shadow_signals = {}
    eng._shadow_recorder_enabled = enabled
    eng.feed = feed
    return eng


class TestRegisterShadow:
    def test_registers_with_kind_and_tags(self):
        eng = _make_engine(feed=None)
        eng._register_shadow(
            signal_id=None, symbol="TRENT", direction="BUY", strategy="SIC",
            entry_price=6010.0, stop_loss=5950.0, target=6120.0,
            kind=KIND_GATE_BLOCKED, never_traded_reason="GATE_BLOCKED",
            blocking_gates=["G15_TTL"],
        )
        (key, entry), = eng._shadow_signals.items()
        assert entry["symbol"] == "TRENT"
        assert entry["kind"] == KIND_GATE_BLOCKED
        assert entry["blocking_gates"] == ["G15_TTL"]
        assert entry["feed_realtime_registered"] is False   # no feed -> unverifiable
        assert entry["mfe"] == 0.0 and entry["mae"] == 0.0

    def test_disabled_recorder_noops(self):
        eng = _make_engine(enabled=False)
        eng._register_shadow(
            signal_id="sig-1", symbol="CIPLA", direction="BUY", strategy="SIC",
            entry_price=1384.0, stop_loss=1370.0, target=1410.0,
            kind=KIND_NEVER_TRADED, never_traded_reason="USER_SKIPPED",
        )
        assert eng._shadow_signals == {}

    def test_never_raises_on_garbage(self):
        eng = _make_engine()
        eng._register_shadow(
            signal_id=None, symbol="", direction="", strategy="",
            entry_price="not-a-number", stop_loss=None, target=None,
            kind=KIND_NEVER_TRADED, never_traded_reason="X",
        )
        assert len(eng._shadow_signals) == 1
        entry = next(iter(eng._shadow_signals.values()))
        assert entry["entry_price"] == 0.0

    def test_unique_keys_for_idless_signals(self):
        eng = _make_engine()
        for _ in range(3):
            eng._register_shadow(
                signal_id=None, symbol="CIPLA", direction="BUY", strategy="SIC",
                entry_price=1384.0, stop_loss=1370.0, target=1410.0,
                kind=KIND_GATE_BLOCKED, never_traded_reason="GATE_BLOCKED",
            )
        assert len(eng._shadow_signals) == 3

    def test_strategy_shadow_kind_distinguished(self):
        eng = _make_engine()
        eng._register_shadow(
            signal_id="sig-trs", symbol="NIFTY", direction="LONG", strategy="TRS",
            entry_price=25000.0, stop_loss=24950.0, target=25120.0,
            kind=KIND_STRATEGY_SHADOW,
        )
        entry, = eng._shadow_signals.values()
        assert entry["kind"] == KIND_STRATEGY_SHADOW
        assert entry["signal_id"] == "sig-trs"
