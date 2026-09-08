"""v0.4.9 wave-4 — fee-truth + G19 minimum-move gate (2026-09-03 evening).

Two production display defects fixed and one new gate:

1. ENTRY-TIME FEE LIE: the trade row's entry-time ``fees`` (rendered in
   Telegram as "Estimated Fees") used a hand-rolled SINGLE-LEG formula —
   one ₹20 brokerage, one leg of turnover fees, intraday STT wrongly
   applied to the buy leg, GST levied on the entire fee stack. It showed
   ~₹38-40 while the true round trip runs ~₹61-62.
   Live evidence: ASIANPAINT (2026-08-28) recorded ₹38.08 entry estimate
   vs ₹61.33 true round trip; the 2026-09-03 NTPC/DELHIVERY trades
   displayed ₹38.4x vs ₹61.61/₹61.74 implied actuals.
   Fix: entry estimate now delegates to the canonical NSEFeeCalculator
   full round trip (core.engine._estimate_entry_round_trip_fees).

2. EOD DOUBLE-COUNT: _compute_pnl_summary / _compute_strategy_breakdown
   added the per-order ``brokerage`` column (₹20) on top of ``fees``,
   which ALREADY contains both brokerage legs since the close-path
   correction — a phantom ₹20/trade in every EOD summary/PDF.

3. G19 MinMove gate: complementary to G17 (cost vs RISK) — checks the
   TARGET REWARD against round-trip costs (default ≥ 2.0×). Ships in
   ``log_only`` mode: never blocks, surfaces [G19 SHADOW] verdicts so
   live days build the enforcement evidence base.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from db.migrations import Base
from db.repository import Repository
from fees.nse_fee_calculator import NSEFeeCalculator
from notifications.eod_report import EODReportGenerator
from risk.gates.g19_min_move import G19MinMoveGate
from risk.risk_engine import RiskEngine

IST = ZoneInfo("Asia/Kolkata")

_FEES_CFG = {
    "brokerage_per_order": 20.0,
    "exchange_txn_pct": 0.0000345,
    "stt_intraday_sell_pct": 0.00025,
    "sebi_fee_pct": 0.000001,
    "stamp_duty_pct": 0.00003,
    "gst_pct": 0.18,
}


# ─────────────────────────────────────────────────────────────────────────────
# The OLD single-leg formula (verbatim from pre-wave-4 engine.py) — kept here
# as the documented "lie" so the regression proof is bit-exact.
# ─────────────────────────────────────────────────────────────────────────────

def _old_single_leg_formula(invested_amount: float, fees_config: dict) -> float:
    brokerage = float(fees_config.get("brokerage_per_order", 20))
    ex_rate = float(fees_config.get("exchange_txn_pct", 0.0000345))
    exchange_txn = invested_amount * (ex_rate if ex_rate < 0.001 else ex_rate / 100)
    stt_rate = float(fees_config.get("stt_intraday_sell_pct", 0.00025))
    stt = invested_amount * (stt_rate if stt_rate < 0.001 else stt_rate / 100)
    sebi_rate = float(fees_config.get("sebi_fee_pct", 0.000001))
    sebi_fee = invested_amount * (sebi_rate if sebi_rate < 0.0001 else sebi_rate / 100)
    stamp_rate = float(fees_config.get("stamp_duty_pct", 0.00003))
    stamp_duty = invested_amount * (stamp_rate if stamp_rate < 0.001 else stamp_rate / 100)
    gst_rate = float(fees_config.get("gst_pct", 0.18))
    gst_mult = gst_rate if gst_rate <= 1.0 else gst_rate / 100
    gst = (brokerage + exchange_txn + stt + sebi_fee + stamp_duty) * gst_mult
    return round(brokerage + exchange_txn + stt + sebi_fee + stamp_duty + gst, 2)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fee truth — entry-time estimate
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryFeeTruth:
    def test_old_formula_reproduces_asianpaint_38_lie(self):
        """BIT-EXACT documentation of the bug: the old formula on the live
        ASIANPAINT geometry (BUY 15 @ 2593.60 → invested ₹38,904) produces
        exactly the ₹38.08 the trade record showed on 2026-08-28."""
        invested = 2593.60 * 15
        assert _old_single_leg_formula(invested, _FEES_CFG) == pytest.approx(38.08, abs=0.005)

    def test_entry_estimate_now_matches_true_round_trip(self):
        """The new entry estimate on the same geometry must equal the true
        round trip (₹61.33 recorded at the ₹2,592.80 exit fill — the entry
        approximation prices the exit leg at the fill, ±1 paisa)."""
        from core.engine import _estimate_entry_round_trip_fees

        est = _estimate_entry_round_trip_fees(2593.60, 15, _FEES_CFG)
        assert est == pytest.approx(61.33, abs=0.02)
        # and the gap vs the old lie is at least the missing brokerage leg
        assert est - _old_single_leg_formula(2593.60 * 15, _FEES_CFG) >= 20.0

    def test_entry_estimate_equals_canonical_calculator(self):
        """The helper is a thin delegation — outputs must match the
        canonical calculator bit-for-bit."""
        from core.engine import _estimate_entry_round_trip_fees

        canonical = NSEFeeCalculator(brokerage_per_order=20.0).calculate_equity_intraday(
            buy_price=100.0, sell_price=100.0, quantity=500, brokerage_per_order=20.0
        )
        assert _estimate_entry_round_trip_fees(100.0, 500, _FEES_CFG) == canonical["total"]

    def test_entry_estimate_20260903_display_band(self):
        """2026-09-03 evidence band: implied actual fees were ₹61.61 (NTPC)
        and ₹61.74 (DELHIVERY-BUY) → invested ₹39,652-₹40,000 under the
        canonical round-trip model. Across that band the OLD formula emits
        ₹38.36-38.45 (the "₹38.4x" the user actually saw) while the NEW
        estimate brackets the implied actuals."""
        from core.engine import _estimate_entry_round_trip_fees

        for invested in (39652.0, 39820.0, 40000.0):
            old = _old_single_leg_formula(invested, _FEES_CFG)
            new = _estimate_entry_round_trip_fees(invested / 110.0, 110, _FEES_CFG)
            assert 38.3 <= old <= 38.5, f"old formula {old} outside observed 38.4x display band"
            assert 61.5 <= new <= 61.8, f"new estimate {new} outside implied actuals band"
            assert new > old + 20.0

    def test_entry_estimate_zero_qty_is_zero(self):
        from core.engine import _estimate_entry_round_trip_fees

        assert _estimate_entry_round_trip_fees(100.0, 0, _FEES_CFG) == 0.0
        assert _estimate_entry_round_trip_fees(100.0, None, _FEES_CFG) == 0.0

    def test_entry_estimate_honest_floor_when_calculator_broken(self, monkeypatch):
        """If the calculator somehow blows up, fall to the BOTH-legs
        brokerage floor (₹47.20) — never back to the single-leg lie."""
        from core.engine import _estimate_entry_round_trip_fees

        def _boom(*a, **kw):
            raise RuntimeError("calculator down")

        monkeypatch.setattr(NSEFeeCalculator, "calculate_equity_intraday", _boom)
        est = _estimate_entry_round_trip_fees(2593.60, 15, _FEES_CFG)
        assert est == pytest.approx(47.20, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fee truth — EOD summary no longer double-counts brokerage
# ─────────────────────────────────────────────────────────────────────────────

class _FakeTrade:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _closed_trade(fees=61.33, brokerage=20.0, pnl=-12.00, net=-73.33, strategy="SIC"):
    return _FakeTrade(
        status="CLOSED", net_pnl=net, fees=fees, pnl=pnl, brokerage=brokerage,
        invested_amount=38904.0, strategy=strategy, symbol="ASIANPAINT",
        direction="BUY", exit_price=2592.80, entry_price=2593.60, sl=0, target=0,
        qty=15, entry_time="x", exit_time="y",
    )


class TestEodSummaryFeeTruth:
    def test_summary_does_not_double_count_brokerage(self):
        """fees=61.33 (full RT incl. both brokerage legs) + brokerage=20
        (per-order stat) must NOT sum to 81.33."""
        summary = EODReportGenerator._compute_pnl_summary([_closed_trade()])
        assert summary["total_fees"] == pytest.approx(61.33, abs=0.01)

    def test_summary_identity_gross_minus_fees_eq_net(self):
        summary = EODReportGenerator._compute_pnl_summary([_closed_trade()])
        assert summary["net_pnl"] == pytest.approx(summary["gross_pnl"] - summary["total_fees"], abs=0.01)
        assert summary["net_pnl"] == pytest.approx(-73.33, abs=0.01)
        assert summary["gross_pnl"] == pytest.approx(-12.00, abs=0.01)

    def test_summary_multiple_trades_aggregate(self):
        trades = [
            _closed_trade(fees=61.61, pnl=-118.80, net=-180.41, strategy="SIC"),
            _closed_trade(fees=61.74, pnl=56.32, net=-5.42, strategy="MRF"),
        ]
        summary = EODReportGenerator._compute_pnl_summary(trades)
        assert summary["total_fees"] == pytest.approx(123.35, abs=0.01)
        assert summary["net_pnl"] == pytest.approx(-185.83, abs=0.01)

    def test_strategy_breakdown_does_not_double_count(self):
        trades = [
            _closed_trade(fees=61.61, net=-180.41, strategy="SIC"),
            _closed_trade(fees=61.74, net=-5.42, strategy="SIC"),
        ]
        breakdown = EODReportGenerator._compute_strategy_breakdown(trades)
        assert breakdown["SIC"]["total_fees"] == pytest.approx(123.35, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 3. G19 gate — unit behavior across all modes
# ─────────────────────────────────────────────────────────────────────────────

_G19_BASE = {"hard_risk_pct": 1.0, "brokerage_per_order": 20.0, "min_move_fee_multiple": 2.0}


def _geometry(target_distance: float):
    """entry 100 / sl 98 → risk/share 2.0 → capital 100k, 1% budget → qty 500.
    Round-trip fees at (100, 500) = ₹65.06."""
    return {
        "entry_price": 100.0,
        "sl_price": 98.0,
        "target_price": 100.0 + target_distance,
        "strategy": "SIC",
    }


def _g19_fees() -> float:
    return G19MinMoveGate.round_trip_fees(100.0, 500, 20.0)


class TestG19Modes:
    @pytest.mark.asyncio
    async def test_default_mode_is_log_only(self):
        gate = G19MinMoveGate({})
        assert gate.mode == "log_only"
        assert gate.min_move_fee_multiple == 2.0

    @pytest.mark.asyncio
    async def test_unknown_mode_fails_safe_to_log_only(self):
        assert G19MinMoveGate({"g19_mode": "yolo"}).mode == "log_only"

    @pytest.mark.asyncio
    async def test_off_mode_skips(self):
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "off"})
        res = await gate.check(_geometry(0.1), {"total_capital": 100000.0})
        assert res.passed
        assert "disabled" in res.message.lower()

    @pytest.mark.asyncio
    async def test_log_only_never_blocks_below_multiple(self):
        """Reward ₹100 vs fees ₹65.06 → 1.54× < 2.0×: shadow-verdict, PASS."""
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "log_only"})
        res = await gate.check(_geometry(0.2), {"total_capital": 100000.0})
        assert res.passed is True
        assert res.severity == "warning"
        assert "SHADOW" in res.message
        assert res.value < res.threshold

    @pytest.mark.asyncio
    async def test_enforce_blocks_below_multiple(self):
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        res = await gate.check(_geometry(0.2), {"total_capital": 100000.0})
        assert res.passed is False
        assert res.gate_name == "G19_MinMove"
        assert res.severity == "warning"
        assert res.value < res.threshold

    @pytest.mark.asyncio
    async def test_enforce_passes_healthy_multiple(self):
        """Target ₹4 away on qty 500 → reward ₹2,000 ≈ 30.7× fees."""
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        res = await gate.check(_geometry(4.0), {"total_capital": 100000.0})
        assert res.passed is True
        assert res.severity == "info"
        assert res.value > res.threshold

    @pytest.mark.asyncio
    async def test_boundary_just_below_blocks_just_above_passes(self):
        """Float-safe boundary probe around exactly 2.0×."""
        fees = _g19_fees()
        dist_eq = 2.0 * fees / 500.0
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        below = await gate.check(_geometry(dist_eq * 0.999), {"total_capital": 100000.0})
        above = await gate.check(_geometry(dist_eq * 1.001), {"total_capital": 100000.0})
        assert below.passed is False
        assert above.passed is True

    @pytest.mark.asyncio
    async def test_value_is_exact_reward_over_fees(self):
        fees = _g19_fees()
        dist = 0.5  # reward ₹250
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        res = await gate.check(_geometry(dist), {"total_capital": 100000.0})
        assert res.value == pytest.approx((dist * 500.0) / fees, abs=0.01)

    @pytest.mark.asyncio
    async def test_skips_incomplete_geometry(self):
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        res = await gate.check({"entry_price": 100.0, "sl_price": 98.0}, {"total_capital": 100000.0})
        assert res.passed
        assert "skipped" in res.message.lower()

    @pytest.mark.asyncio
    async def test_skips_zero_stop_distance(self):
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        res = await gate.check(
            {"entry_price": 100.0, "sl_price": 100.0, "target_price": 101.0},
            {"total_capital": 100000.0},
        )
        assert res.passed
        assert "skipped" in res.message.lower()

    @pytest.mark.asyncio
    async def test_skips_when_calculator_unavailable(self, monkeypatch):
        """Same fail-open policy as G17: a calculator bug must never block."""

        def _boom(*a, **kw):
            raise RuntimeError("calculator down")

        monkeypatch.setattr(NSEFeeCalculator, "calculate_equity_intraday", _boom)
        gate = G19MinMoveGate({**_G19_BASE, "g19_mode": "enforce"})
        res = await gate.check(_geometry(0.2), {"total_capital": 100000.0})
        assert res.passed
        assert "unavailable" in res.message.lower()

    def test_round_trip_fees_helper_asianpaint(self):
        assert G19MinMoveGate.round_trip_fees(2593.60, 15, 20.0) == pytest.approx(61.33, abs=0.02)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Wiring — G19 registered as the 19th gate
# ─────────────────────────────────────────────────────────────────────────────

class TestG19Wiring:
    def test_risk_engine_registers_19_gates(self):
        re_ = RiskEngine({})
        assert len(re_.gates) == 19
        names = [g.__class__.__name__ for g in re_.gates]
        assert names[-1] == "G19MinMoveGate"
        assert "G18StrategyGuard" in names and "G17CostPreCheck" in names

    def test_registered_gate_defaults_shadow(self):
        gate = RiskEngine({}).gates[-1]
        assert isinstance(gate, G19MinMoveGate)
        assert gate.mode == "log_only"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Engine actual-size re-check — G19 at the REAL sized quantity
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


def _candles(n=25, base=3500.0):
    now = datetime.now(IST)
    return [
        {"open": base + i, "high": base + 2 + i, "low": base - 1 + i,
         "close": base + 1 + i, "volume": 1000,
         "time": (now - timedelta(minutes=5 * (n - i))).isoformat()}
        for i in range(n)
    ]


def _scan_stub(repo):
    """Mirror of the test_live_session2_corrections engine stub, tuned for
    G19 geometry: entry 1000 / sl 995 / target 1001, sized qty 100.
    G17 actual-size: fees ₹83.54 = 16.7% of ₹500 risk → PASSES.
    G19 actual-size: reward ₹100 / fees ₹83.54 = 1.20× < 2.0× → NEGATIVE."""
    from core.engine import UltraBotEngine

    eng = MagicMock(spec=UltraBotEngine)
    eng.vix_critical_stale = False
    eng.active_strategies = ["SIC"]
    eng.shadow_strategies = set()
    eng._shadow_signals = {}
    eng._shadow_max_age_minutes = 90
    eng.current_regime = "Sideways"
    eng.vix = 11.0
    eng.session_id = "g19-recheck-test"
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
    eng._run_risk_gates = AsyncMock(
        return_value={"passed": True, "all_gates": [{"gate": "G1", "passed": True}]}
    )
    eng._calculate_position_size = AsyncMock(return_value={"quantity": 100, "position_size": 100000.0})
    eng._build_opportunity = MagicMock(
        return_value={"id": "opp-g19-1", "symbol": "TCS", "direction": "BUY",
                      "strategy": "SIC", "confidence": 0.8, "signal_id": None}
    )
    eng.market_hours = None
    eng.stale_candle_max_age_minutes = 0
    eng._repo_context = MagicMock(return_value=_repo_ctx(repo))
    eng._execute_strategy_scan = AsyncMock(
        return_value={
            "symbol": "TCS", "direction": "BUY",
            "entry_price": 1000.0, "sl_price": 995.0, "target_price": 1001.0,
            "confidence": 0.8, "strategy": "SIC", "risk_reward": 0.2,
        }
    )
    eng.feed = MagicMock()
    eng.feed.get_candles = AsyncMock(return_value=_candles(base=1000.0))
    eng.broker = None
    return eng


_G19_SIGNAL = {
    "symbol": "TCS", "direction": "BUY",
    "entry_price": 1000.0, "sl_price": 995.0, "target_price": 1001.0,
    "confidence": 0.8, "strategy": "SIC",
}


class TestEngineActualSizeG19Recheck:
    @pytest.mark.asyncio
    async def test_enforce_mode_rejects_small_move_at_actual_size(self, async_session):
        """g19_mode=enforce + fee-heavy reward → G19_MinMove rejection with
        truthful counters, no opportunity, no signal row."""
        from core.engine import UltraBotEngine

        repo = Repository(async_session)
        eng = _scan_stub(repo)
        eng.config = MagicMock()
        eng.config.get_fees_config.return_value = _FEES_CFG
        eng.config.get_risk_config.return_value = {
            "max_fee_pct_of_risk": 30.0, "g19_mode": "enforce", "min_move_fee_multiple": 2.0,
        }

        await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo, open_positions=[])

        assert eng._rejections_by_gate.get("G19_MinMove") == 1
        assert eng._signals_rejected_count == 1
        assert eng._signals_passed_count == 0
        assert eng.pending_opportunities == {}
        assert await repo.get_signals_by_strategy("SIC", limit=5) == []

    @pytest.mark.asyncio
    async def test_log_only_mode_same_geometry_creates_opportunity(self, async_session):
        """DEFAULT config (no g19_mode key): identical fee-heavy geometry
        must NOT be blocked — the shadow verdict goes to the log only."""
        from core.engine import UltraBotEngine

        repo = Repository(async_session)
        eng = _scan_stub(repo)
        eng.config = MagicMock()
        eng.config.get_fees_config.return_value = _FEES_CFG
        eng.config.get_risk_config.return_value = {"max_fee_pct_of_risk": 30.0}

        await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo, open_positions=[])

        assert "G19_MinMove" not in eng._rejections_by_gate
        assert eng._signals_rejected_count == 0
        assert "opp-g19-1" in eng.pending_opportunities
        sigs = await repo.get_signals_by_strategy("SIC", limit=5)
        assert len(sigs) == 1 and sigs[0].status == "pending"

    @pytest.mark.asyncio
    async def test_enforce_mode_healthy_reward_not_blocked(self, async_session):
        """Wide-target geometry (reward ₹2,000 vs fees ₹83.54 ≈ 24×) sails
        through even in enforce mode."""
        from core.engine import UltraBotEngine

        repo = Repository(async_session)
        eng = _scan_stub(repo)
        eng._execute_strategy_scan = AsyncMock(
            return_value={
                "symbol": "TCS", "direction": "BUY",
                "entry_price": 1000.0, "sl_price": 995.0, "target_price": 1020.0,
                "confidence": 0.8, "strategy": "SIC", "risk_reward": 4.0,
            }
        )
        eng.config = MagicMock()
        eng.config.get_fees_config.return_value = _FEES_CFG
        eng.config.get_risk_config.return_value = {
            "max_fee_pct_of_risk": 30.0, "g19_mode": "enforce", "min_move_fee_multiple": 2.0,
        }

        await UltraBotEngine._scan_symbol.__get__(eng, UltraBotEngine)("TCS", repo, open_positions=[])

        assert "G19_MinMove" not in eng._rejections_by_gate
        assert "opp-g19-1" in eng.pending_opportunities
