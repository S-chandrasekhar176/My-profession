"""Gate G3: Max Position Size.

Blocks trades where the estimated position value would exceed the configured
percentage of total capital.
"""
from typing import Any, Dict

from models.risk_state import GateResult
from core.capital_resolver import resolve_total_capital


class G3MaxPositionSize:
    """Ensure no single position exceeds a percentage of total capital."""

    def __init__(self, config: Dict[str, Any]):
        self.max_position_pct: float = float(config.get("max_per_position_pct", config.get("max_capital_per_trade_pct", 25)))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        total_capital = resolve_total_capital(context=context)

        if total_capital <= 0:
            return GateResult(
                gate_name="G3_MaxPositionSize",
                passed=False,
                message="Total capital is zero or negative, cannot evaluate position size",
                value=0.0,
                threshold=0.0,
                severity="critical",
            )

        entry_price = float(getattr(signal, "entry_price", 0) or 0)
        raw_quantity = getattr(signal, "quantity", None)
        if raw_quantity is None:
            raw_quantity = context.get("quantity")
        quantity = float(raw_quantity) if raw_quantity is not None else 1.0

        if context.get("position_value") is not None:
            position_value = float(context.get("position_value", 0.0))
        elif entry_price > 0 and quantity > 0:
            position_value = entry_price * quantity
        else:
            position_value = float(context.get("position_value", 0.0))

        max_allowed = total_capital * (self.max_position_pct / 100.0)

        # For intraday equity/options/futures, margin required is ~20% of contract value
        margin_required = position_value * 0.20

        # Position value must be within allowed percentage of capital
        if position_value <= max_allowed:
            return GateResult(
                gate_name="G3_MaxPositionSize",
                passed=True,
                message=(
                    f"Position value ₹{position_value:,.0f} (Margin: ₹{margin_required:,.0f}) within "
                    f"{self.max_position_pct}% of capital (₹{max_allowed:,.0f})"
                ),
                value=position_value,
                threshold=max_allowed,
                severity="info",
            )

        return GateResult(
            gate_name="G3_MaxPositionSize",
            passed=False,
            message=(
                f"Position value ₹{position_value:,.0f} exceeds "
                f"{self.max_position_pct}% of capital (₹{max_allowed:,.0f})"
            ),
            value=position_value,
            threshold=max_allowed,
            severity="warning",
        )
