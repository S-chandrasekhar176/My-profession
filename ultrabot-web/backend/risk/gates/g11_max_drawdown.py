"""Gate G11: Maximum Drawdown.

Blocks new trades when the current portfolio drawdown exceeds the
configured maximum drawdown percentage.
"""
from typing import Any, Dict

from models.risk_state import GateResult


class G11MaxDrawdown:
    """Halt trading when the current drawdown is too deep."""

    def __init__(self, config: Dict[str, Any]):
        self.max_drawdown_pct: float = float(config.get("max_drawdown_pct", 5))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        current_drawdown = float(context.get("current_drawdown_pct", 0))

        if current_drawdown > self.max_drawdown_pct:
            return GateResult(
                gate_name="G11_MaxDrawdown",
                passed=False,
                message=(
                    f"Current drawdown ({current_drawdown:.2f}%) exceeds "
                    f"limit ({self.max_drawdown_pct:.2f}%)"
                ),
                value=current_drawdown,
                threshold=self.max_drawdown_pct,
                severity="critical",
            )

        return GateResult(
            gate_name="G11_MaxDrawdown",
            passed=True,
            message=(
                f"Current drawdown ({current_drawdown:.2f}%) within "
                f"limit ({self.max_drawdown_pct:.2f}%)"
            ),
            value=current_drawdown,
            threshold=self.max_drawdown_pct,
            severity="info",
        )
