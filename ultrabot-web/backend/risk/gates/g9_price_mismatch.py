"""Gate G9: Price Mismatch.

Critical P0 guard that blocks a trade when the signal's entry price
deviates significantly from the broker's live last-traded price (LTP).
"""
from typing import Any, Dict

from models.risk_state import GateResult


class G9PriceMismatch:
    """Block trades when signal price deviates too much from broker LTP."""

    def __init__(self, config: Dict[str, Any]):
        self.threshold_pct: float = float(
            config.get("price_mismatch_threshold_pct", 0.5)
        )

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        broker_ltp = context.get("broker_ltp")

        if broker_ltp is None or float(broker_ltp) <= 0:
            return GateResult(
                gate_name="G9_PriceMismatch",
                passed=True,
                message="Broker LTP not available, gate passed by default",
                severity="info",
            )

        broker_ltp = float(broker_ltp)
        entry_price = float(
            getattr(signal, "entry_price", 0)
            or (signal.get("entry_price", 0) if isinstance(signal, dict) else 0)
            or context.get("entry_price", 0)
            or context.get("current_price", 0)
            or 0
        )

        if entry_price <= 0:
            # For market orders or deferred entry, assume broker LTP as entry baseline
            entry_price = broker_ltp

        mismatch_pct = abs(entry_price - broker_ltp) / broker_ltp * 100.0

        if mismatch_pct > self.threshold_pct:
            return GateResult(
                gate_name="G9_PriceMismatch",
                passed=False,
                message=(
                    f"Price mismatch {mismatch_pct:.2f}% exceeds threshold "
                    f"{self.threshold_pct}% (signal={entry_price:.2f}, "
                    f"ltp={broker_ltp:.2f})"
                ),
                value=mismatch_pct,
                threshold=self.threshold_pct,
                severity="critical",
            )

        return GateResult(
            gate_name="G9_PriceMismatch",
            passed=True,
            message=(
                f"Price mismatch {mismatch_pct:.2f}% within threshold "
                f"{self.threshold_pct}% (signal={entry_price:.2f}, "
                f"ltp={broker_ltp:.2f})"
            ),
            value=mismatch_pct,
            threshold=self.threshold_pct,
            severity="info",
        )
