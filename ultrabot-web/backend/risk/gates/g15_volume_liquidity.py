"""Gate G15: Volume Profile & Liquidity Validation.

Ensures that the trade setup has confirmed institutional or retail volume
participation (relative volume ratio >= threshold), preventing low-volume
false breakouts or illiquid slippage traps.
"""
from typing import Any, Dict

from models.risk_state import GateResult


class G15VolumeLiquidity:
    """Validate relative volume ratio and liquidity before opening trades."""

    def __init__(self, config: Dict[str, Any]):
        self.min_volume_ratio: float = float(config.get("min_volume_ratio", 1.0))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        # Check volume ratio
        volume_ratio = getattr(signal, "volume_ratio", None)
        if volume_ratio is None and isinstance(signal, dict):
            volume_ratio = signal.get("volume_ratio")

        if volume_ratio is None:
            # Check context
            if "volume" in context and "avg_volume" in context and context["avg_volume"] > 0:
                volume_ratio = float(context["volume"]) / float(context["avg_volume"])
            else:
                volume_ratio = context.get("volume_ratio", 1.0)

        volume_ratio = float(volume_ratio)

        if volume_ratio < self.min_volume_ratio:
            return GateResult(
                gate_name="G15_VolumeLiquidity",
                passed=False,
                message=(
                    f"Relative volume {volume_ratio:.2f}x is below minimum {self.min_volume_ratio:.2f}x "
                    f"spike-trimmed volume baseline — low liquidity risk"
                ),
                value=volume_ratio,
                threshold=self.min_volume_ratio,
                severity="warning",
            )

        return GateResult(
            gate_name="G15_VolumeLiquidity",
            passed=True,
            message=(
                f"Volume confirmed: {volume_ratio:.2f}x relative volume >= {self.min_volume_ratio:.2f}x requirement"
            ),
            value=volume_ratio,
            threshold=self.min_volume_ratio,
            severity="info",
        )
