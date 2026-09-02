"""Gate G10: Minimum Signal Confidence.

Blocks trades from signals whose confidence score is below the
configured minimum threshold.
"""
from typing import Any, Dict

from models.risk_state import GateResult


class G10MinConfidence:
    """Reject signals that lack sufficient confidence."""

    def __init__(self, config: Dict[str, Any]):
        self.min_confidence: float = float(config.get("min_signal_confidence", 0.6))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        _raw_conf = getattr(signal, "confidence", None)
        if _raw_conf is None and isinstance(signal, dict):
            _raw_conf = signal.get("confidence")
        confidence = float(_raw_conf if _raw_conf is not None else 0.0)

        if confidence < self.min_confidence:
            return GateResult(
                gate_name="G10_MinConfidence",
                passed=False,
                message=(
                    f"Signal confidence ({confidence:.2f}) below minimum "
                    f"({self.min_confidence:.2f})"
                ),
                value=confidence,
                threshold=self.min_confidence,
                severity="warning",
            )

        return GateResult(
            gate_name="G10_MinConfidence",
            passed=True,
            message=(
                f"Signal confidence ({confidence:.2f}) meets minimum "
                f"({self.min_confidence:.2f})"
            ),
            value=confidence,
            threshold=self.min_confidence,
            severity="info",
        )
