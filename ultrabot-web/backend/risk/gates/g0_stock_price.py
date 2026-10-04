"""Gate G0: Penny-Stock Minimum-Price Filter (#15, R3 Prompt B).

2026-09-22 paper forensics: ORB shorted IDEA @ ₹13.70 with 9,117 shares —
one tick (5p) is 0.36% of price, and paper fills massively underestimate
live spread/queue slippage at that scale. Sizing was correct; the
microstructure was untradable. Small-target SL hits on such names carry
~34% fee weight (FORTIS: ₹92.40 on ₹271.05 gross).

Rule: reject EQUITY signals priced below _MIN_TRADABLE_PRICE. A code
constant, not config — the floor is a market-microstructure fact, not a
tunable preference (backlog #15 decision).

EQ segment only: option premiums legitimately sit below ₹50 and never
route through this gate in practice (options don't validate via the equity
RiskEngine) — the segment check is defense in depth.
"""
from typing import Any, Dict

from models.risk_state import GateResult

_MIN_TRADABLE_PRICE = 50.0  # code constant per backlog #15 — do NOT make configurable


class G0StockPrice:
    """Block equity signals below the minimum tradable price."""

    def __init__(self, config: Dict[str, Any] = None):
        # Interface parity with the other gates: the risk engine passes
        # config to every gate. The floor itself is deliberately NOT
        # configurable (see module docstring).
        self.config = config or {}

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        # EQ segment only — option premiums are exempt by design
        segment = str(
            context.get("segment", "EQ") or "EQ"
        ).upper()
        if segment not in ("EQ", ""):
            return GateResult(
                gate_name="G0_StockPriceFilter",
                passed=True,
                message=f"Segment {segment} exempt from penny-stock price floor",
                severity="info",
            )

        price = float(
            getattr(signal, "entry_price", 0)
            or (signal.get("entry_price", 0) if isinstance(signal, dict) else 0)
            or context.get("entry_price", 0)
            or context.get("current_price", 0)
            or 0
        )

        if price <= 0:
            # No price to judge — fail OPEN (other gates own data-quality
            # blocking; G0 must not eat unpriced signals blindly)
            return GateResult(
                gate_name="G0_StockPriceFilter",
                passed=True,
                message="No entry price available, gate passed by default",
                severity="info",
            )

        if price < _MIN_TRADABLE_PRICE:
            return GateResult(
                gate_name="G0_StockPriceFilter",
                passed=False,
                message=(
                    f"Stock price ₹{price:.2f} below minimum tradable "
                    f"₹{_MIN_TRADABLE_PRICE:.0f} — one tick is "
                    f"{5.0 / price * 100:.2f}% of price; live spread/queue "
                    f"slippage makes the economics untradable (penny-stock gate)"
                ),
                value=price,
                threshold=_MIN_TRADABLE_PRICE,
                severity="warning",
            )

        return GateResult(
            gate_name="G0_StockPriceFilter",
            passed=True,
            message=f"Stock price ₹{price:.2f} ≥ minimum ₹{_MIN_TRADABLE_PRICE:.0f}",
            severity="info",
        )
