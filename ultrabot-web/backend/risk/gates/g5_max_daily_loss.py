"""Gate G5: Max Daily Loss.

Blocks new trades when the cumulative daily P&L drops below the
configured maximum loss threshold (expressed as a percentage of capital).
"""
from typing import Any, Dict

from models.risk_state import GateResult
from core.capital_resolver import resolve_total_capital


class G5MaxDailyLoss:
    """Halt trading if the daily loss exceeds a percentage of total capital."""

    def __init__(self, config: Dict[str, Any]):
        self.max_daily_loss_pct: float = float(config.get("max_daily_loss_pct", 3))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        if "daily_pnl" in context:
            daily_pnl = float(context["daily_pnl"])
        elif "daily_loss" in context or "daily_loss_rupees" in context:
            daily_loss_val = float(context.get("daily_loss") or context.get("daily_loss_rupees") or 0.0)
            daily_pnl = -abs(daily_loss_val) if daily_loss_val > 0 else 0.0
        else:
            daily_pnl = 0.0

        total_capital = resolve_total_capital(context=context)

        if total_capital <= 0:
            return GateResult(
                gate_name="G5_MaxDailyLoss",
                passed=False,
                message="Total capital is zero, cannot evaluate daily loss",
                value=daily_pnl,
                threshold=0.0,
                severity="critical",
            )

        loss_limit = total_capital * (self.max_daily_loss_pct / 100.0)

        if daily_pnl <= -loss_limit:
            return GateResult(
                gate_name="G5_MaxDailyLoss",
                passed=False,
                message=(
                    f"Daily P&L (₹{daily_pnl:,.0f}) <= -{self.max_daily_loss_pct}% "
                    f"of capital (₹{loss_limit:,.0f})"
                ),
                value=daily_pnl,
                threshold=loss_limit,
                severity="critical",
            )

        return GateResult(
            gate_name="G5_MaxDailyLoss",
            passed=True,
            message=(
                f"Daily P&L (₹{daily_pnl:,.0f}) within "
                f"-{self.max_daily_loss_pct}% limit (₹{loss_limit:,.0f})"
            ),
            value=daily_pnl,
            threshold=loss_limit,
            severity="info",
        )
