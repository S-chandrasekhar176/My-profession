"""Direction-convention utilities shared across the backend.

The codebase carries TWO direction vocabularies:
  * Strategies / signals / positions emit broker vocabulary: "BUY" / "SELL"
  * Legacy code (and some internal modules like the backtest simulator,
    the fee calculator and the Kronos scorer) use "LONG" / "SHORT"

History (live-market validation run 2, 2026-08-28): exit-path code compared
``direction == "LONG"`` exactly while every real position carries "BUY"/"SELL"
— every real position took the wrong branch (inverted P&L, inverted SL/target
triggers, trailing SL never moving, fees computed on swapped legs). A second
instance of the same bug class survived in ``core/scheduler.py``'s 15:20
auto-squareoff and a third in ``api/routes/dashboard.py``'s repo-fallback
path (v0.4.4 audit round) — both silently inverted P&L for BUY positions.

RULE: any comparison of a position/signal ``direction`` against the
long/short axis MUST go through :func:`is_long_direction`. A static guard
test (tests/test_opportunity_direction_rr.py) enforces this across the
backend source tree.
"""
from __future__ import annotations

from typing import Any

# Values that unambiguously mean "long side of the market", in both
# vocabularies plus common shorthand variants.
_LONG_VALUES = frozenset({"BUY", "LONG", "B"})


def is_long_direction(direction: Any) -> bool:
    """True for long positions — accepts BUY/LONG (and 'B').

    Tolerates None, non-strings, surrounding whitespace and any casing.
    Anything not recognisably long (SELL/SHORT/None/garbage) returns False,
    matching the pre-existing conservative default in exit-order routing
    (``exit_tx_type = "SELL" if direction in ("LONG", "BUY") else "BUY"``).
    """
    try:
        return str(direction or "").strip().upper() in _LONG_VALUES
    except Exception:  # defensive: str() on exotic objects
        return False
