from typing import Any, Dict, List, Optional, TYPE_CHECKING
from types import SimpleNamespace

from models.risk_state import GateResult, RiskResult

from risk.gates.g1_max_positions import G1MaxPositions
from risk.gates.g2_sector_concentration import G2SectorConcentration
from risk.gates.g3_max_position_size import G3MaxPositionSize
from risk.gates.g4_max_daily_trades import G4MaxDailyTrades
from risk.gates.g5_max_daily_loss import G5MaxDailyLoss
from risk.gates.g6_correlation_check import G6CorrelationCheck
from risk.gates.g7_vix_filter import G7VIXFilter
from risk.gates.g8_time_of_day import G8TimeOfDay
from risk.gates.g9_price_mismatch import G9PriceMismatch
from risk.gates.g10_min_confidence import G10MinConfidence
from risk.gates.g11_max_drawdown import G11MaxDrawdown
from risk.gates.g12_margin_check import G12MarginCheck
from risk.gates.g13_duplicate_signal import G13DuplicateSignal
from risk.gates.g14_strategy_backtest import G14StrategyBacktest
from risk.gates.g15_volume_liquidity import G15VolumeLiquidity
from risk.gates.g16_multi_timeframe import G16MultiTimeframe
from risk.gates.g17_cost_precheck import G17CostPreCheck
from risk.gates.g18_strategy_guard import G18StrategyGuard
from risk.gates.g19_min_move import G19MinMoveGate

from core.capital_resolver import resolve_total_capital

if TYPE_CHECKING:
    from db.repository import Repository


def _wrap_signal(signal: Any) -> Any:
    """Ensure signal object has attribute access."""
    if isinstance(signal, dict):
        return SimpleNamespace(**signal)
    return signal


class RiskEngine:
    """Runs all 19 risk gates sequentially, stopping at the first failure.

    Each gate receives the full risk config dict so it can read its own
    parameters. The ``repository`` is injected into G13 (duplicate
    signal check) where it is needed.
    """

    def __init__(self, config: Dict[str, Any]):
        # NOTE: `config` must be the SAME dict object as
        # settings._raw_config["risk"] (a live reference, not a copy) — see
        # config/settings.py's get_risk_config(). Settings updates mutate
        # that dict in place, and _build_gates() re-reads it fresh on every
        # single validate() call below, so a Settings change takes effect
        # on the very next signal — no backend restart required.
        self.config = config or {}
        self._repository: Optional["Repository"] = None
        self.gates: List[Any] = self._build_gates()

    def _build_gates(self) -> List[Any]:
        """Construct all 19 gates fresh from the current config values.

        Each gate reads its threshold(s) out of `config` in its own
        __init__ and caches them as plain instance attributes — cheap
        (dict lookups only, no I/O), so rebuilding on every validate()
        call is negligible cost and guarantees gates never run on stale
        values after a live settings change.
        """
        return [
            G1MaxPositions(self.config),
            G2SectorConcentration(self.config),
            G3MaxPositionSize(self.config),
            G4MaxDailyTrades(self.config),
            G5MaxDailyLoss(self.config),
            G6CorrelationCheck(self.config),
            G7VIXFilter(self.config),
            G8TimeOfDay(self.config),
            G9PriceMismatch(self.config),
            G10MinConfidence(self.config),
            G11MaxDrawdown(self.config),
            G12MarginCheck(self.config),
            G13DuplicateSignal(self.config),
            G14StrategyBacktest(self.config),
            G15VolumeLiquidity(self.config),
            G16MultiTimeframe(self.config),
            G17CostPreCheck(self.config),
            G18StrategyGuard(self.config),
            G19MinMoveGate(self.config),
        ]

    def set_repository(self, repo: "Repository") -> None:
        """Inject the repository into gates that need DB access (G13).
        Stored on self so it survives gate rebuilds triggered by validate().
        """
        self._repository = repo
        for gate in self.gates:
            if isinstance(gate, G13DuplicateSignal):
                gate.set_repository(repo)

    async def validate(self, signal: Any, context: Optional[Dict[str, Any]] = None) -> RiskResult:
        """Run all gates. Returns a RiskResult with ``passed=True`` only if
        every gate passes. Stops on the first failure."""
        # Rebuild gates fresh from current config on every call — this is
        # the fix for settings changes not applying without a backend
        # restart (see _build_gates() docstring).
        self.gates = self._build_gates()
        if self._repository is not None:
            for gate in self.gates:
                if isinstance(gate, G13DuplicateSignal):
                    gate.set_repository(self._repository)

        sig = _wrap_signal(signal)
        ctx = context if context is not None else {}
        results: List[GateResult] = []

        for gate in self.gates:
            result: GateResult = await gate.check(sig, ctx)
            results.append(result)
            if not result.passed:
                return RiskResult(
                    passed=False,
                    all_gates=results,
                    blocked_by=result.gate_name,
                    block_reason=result.message,
                    severity=result.severity,
                    reduced_size=False,
                    notes=f"Blocked by {result.gate_name}",
                )

        return RiskResult(
            passed=True,
            all_gates=results,
            blocked_by=None,
            block_reason=None,
            severity="info",
            reduced_size=False,
            notes="All 19 gates passed",
        )

    async def evaluate(
        self,
        signal: Any,
        symbol: str = "",
        current_price: float = 0.0,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> RiskResult:
        """Convenience evaluation gateway for engine orchestrators.
        
        Accepts raw signal dictionary or object, builds/enriches context,
        and executes all 19 risk gates.
        """
        if isinstance(symbol, dict) and context is None:
            context = symbol
            symbol = ""
        sig = _wrap_signal(signal)
        ctx = dict(context or {})
        # None-preserving fallbacks: an explicit 0 (e.g. capital fully
        # exhausted) is a VALID value and must NOT be replaced by the next
        # candidate or a phantom default — that was the old falsy-`or` bug.
        # Final fallback goes through the canonical resolver (configured
        # settings.virtual_capital → shared 500000.0 default) so this engine
        # can never drift from the capital figure used by G3/G5/G12,
        # position_sizer and core.engine.
        total_cap_raw = ctx.get("total_capital")
        if total_cap_raw is None:
            total_cap_raw = ctx.get("capital")
        if total_cap_raw is None:
            total_cap_raw = resolve_total_capital()
        total_cap = float(total_cap_raw)

        margin_raw = ctx.get("margin_available")
        if margin_raw is None:
            margin_raw = ctx.get("available_capital")
        if margin_raw is None:
            margin_raw = total_cap
        margin_avail = float(margin_raw)

        daily_loss_raw = ctx.get("daily_loss")
        if daily_loss_raw is None:
            daily_loss_raw = ctx.get("daily_loss_rupees")
        if daily_loss_raw is None:
            daily_loss_raw = 0.0
        daily_loss = float(daily_loss_raw)
        daily_pnl = float(ctx.get("daily_pnl") if "daily_pnl" in ctx else -daily_loss)
        entry_px = float(getattr(sig, "entry_price", 0.0) or ctx.get("current_price") or ctx.get("ltp") or 0.0)

        ctx.setdefault("total_capital", total_cap)
        ctx.setdefault("capital", total_cap)
        ctx.setdefault("available_capital", margin_avail)
        ctx.setdefault("margin_available", margin_avail)
        ctx.setdefault("open_positions", 0)
        ctx.setdefault("open_positions_count", 0)
        ctx.setdefault("open_position_symbols", [])
        ctx.setdefault("open_positions_list", [])
        ctx.setdefault("positions_by_sector", {})
        ctx.setdefault("daily_trades", 0)
        ctx.setdefault("daily_trade_count", 0)
        ctx.setdefault("daily_loss", daily_loss)
        ctx.setdefault("daily_loss_rupees", daily_loss)
        dd = ctx.get("current_drawdown_pct")
        if dd is None:
            dd = ctx.get("drawdown_pct")
        if dd is None:
            dd = ctx.get("max_drawdown_pct")
        if dd is None:
            dd = (daily_loss / total_cap) * 100.0 if total_cap > 0 else 0.0
        drawdown_val = float(dd)
        ctx.setdefault("current_drawdown_pct", drawdown_val)
        ctx.setdefault("drawdown_pct", drawdown_val)
        ctx.setdefault("broker_ltp", entry_px)
        ctx.setdefault("current_price", entry_px)
        ctx.setdefault("ltp", entry_px)
        ctx.setdefault("vix", 15.0)
        ctx.setdefault("india_vix", 15.0)
        from datetime import datetime, timezone
        ctx.setdefault("current_time", datetime.now(timezone.utc))

        return await self.validate(sig, ctx)
