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
        self.config = config or {}
        self.min_volume_ratio: float = float(self.config.get("min_volume_ratio", 1.0))
        # Mean reversion strategies (MRF, VC) fade exhausted moves, where volume naturally
        # dries up at extreme bands. Requiring 1.0x breakout volume is anti-pattern for MR.
        self.mean_reversion_min_volume_ratio: float = float(
            self.config.get("mean_reversion_min_volume_ratio", 0.3)
        )
        self.midday_min_volume_ratio: float = float(
            self.config.get("midday_min_volume_ratio", 0.5)
        )

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

        # Strategy-aware & time-of-day threshold resolution
        strategy = ""
        if isinstance(signal, dict):
            strategy = str(signal.get("strategy") or "").upper()
        else:
            strategy = str(getattr(signal, "strategy", "") or "").upper()

        if isinstance(signal, dict) and "min_volume_ratio" in signal:
            threshold = float(signal["min_volume_ratio"])
        elif strategy in ("MRF", "MEAN_REVERSION", "MEANREVERSIONFORCE", "VC"):
            threshold = self.mean_reversion_min_volume_ratio
        else:
            time_of_day = str(context.get("time_of_day") or "")
            if time_of_day and "11:30" <= time_of_day <= "13:30":
                threshold = self.midday_min_volume_ratio
            else:
                threshold = self.min_volume_ratio

        if volume_ratio < threshold:
            return GateResult(
                gate_name="G15_VolumeLiquidity",
                passed=False,
                message=(
                    f"Relative volume {volume_ratio:.2f}x is below minimum {threshold:.2f}x "
                    f"spike-trimmed volume baseline — low liquidity risk"
                ),
                value=volume_ratio,
                threshold=threshold,
                severity="warning",
            )

        return GateResult(
            gate_name="G15_VolumeLiquidity",
            passed=True,
            message=(
                f"Volume confirmed: {volume_ratio:.2f}x relative volume >= {threshold:.2f}x requirement"
            ),
            value=volume_ratio,
            threshold=threshold,
            severity="info",
        )
