"""Exit-reason taxonomy (v0.4.8 HF-7).

Single source of truth for classifying WHY a position closed, so that:

  * the DB ``trades.exit_reason`` column (schema comment: TARGET, SL,
    MANUAL, PARTIAL_BOOK) finally receives a standardized value instead of
    staying NULL forever,
  * Telegram templates match what actually happened (the previous
    substring dispatch ``"stop" in reason`` mislabeled every time-stop,
    fail-fast and profit-locking trailing exit as "STOP LOSS HIT" —
    5 of 7 exits on the 2026-09-01 live session),
  * the alert manager's generic position_closed branch classifies with the
    same rules as the engine (no duplicated heuristics).

Canonical classes are uppercase tokens safe for both DB storage and UI
display. ``classify_exit`` is deliberately pure (no I/O, no clock) so it is
trivially unit-testable.
"""
from typing import Any, Optional

# --- Canonical exit classes (stored in trades.exit_reason) -----------------
EXIT_TARGET = "TARGET"                # price reached the target
EXIT_STOP_LOSS = "SL"                 # initial stop loss hit at a loss
EXIT_TRAILING_SL = "TRAILING_SL"      # stop that had trailed past breakeven
EXIT_TIME = "TIME_EXIT"               # per-strategy time budget exhausted
EXIT_FAIL_FAST = "FAIL_FAST"          # early adverse-move ejection
EXIT_SQUAREOFF = "SQUARE_OFF"         # 15:15/15:20 intraday square-off
EXIT_PARTIAL = "PARTIAL_BOOK"         # final partial-booking level closed it
EXIT_MANUAL = "MANUAL"                # human closed it from the UI/API
EXIT_UNKNOWN = "OTHER"                # unrecognized close_reason

# Friendly labels for Telegram / UI rendering
EXIT_LABELS = {
    EXIT_TARGET: "Target Hit",
    EXIT_STOP_LOSS: "Stop Loss Hit",
    EXIT_TRAILING_SL: "Trailing Stop Exit (Profit Locked)",
    EXIT_TIME: "Time Stop (Strategy Budget Exhausted)",
    EXIT_FAIL_FAST: "Fail-Fast Exit (Early Adverse Move)",
    EXIT_SQUAREOFF: "Intraday Auto Square-Off",
    EXIT_PARTIAL: "Final Partial Booking Exit",
    EXIT_MANUAL: "Manual Close",
    EXIT_UNKNOWN: "Position Closed",
}

# Substring probes, checked in order. Order matters: "time_stop" contains
# "stop" and "partial_complete" contains neither "target" nor "stop", but
# e.g. a hypothetical "partial_time_stop" must classify as TIME first — the
# probes below are ordered most-specific first for that reason.
_PROBES = (
    ("partial", EXIT_PARTIAL),
    ("target", EXIT_TARGET),
    ("time_stop", EXIT_TIME),
    ("time", EXIT_TIME),
    ("fail_fast", EXIT_FAIL_FAST),
    ("squareoff", EXIT_SQUAREOFF),
    ("square_off", EXIT_SQUAREOFF),
    ("manual", EXIT_MANUAL),
    ("stop_loss", EXIT_STOP_LOSS),
    ("stop", EXIT_STOP_LOSS),
    ("sl", EXIT_STOP_LOSS),
)


def _is_long(direction: Any) -> bool:
    return str(direction or "").upper() in ("LONG", "BUY", "LONG_BUY")


def classify_exit(
    close_reason: Any,
    direction: Any = "",
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    stop_loss: Optional[float] = None,
) -> str:
    """Classify a close_reason string into a canonical exit class.

    Args:
        close_reason: Raw close reason string from the close path.
        direction: LONG/BUY or SHORT/SELL.
        entry_price: Position entry (fill) price.
        exit_price: Effective (fill) exit price.
        stop_loss: The stop level AT close time. For an SL-classified exit,
            a stop that has trailed past the entry means the exit locked in
            profit (trailing/breakeven stop), which is a materially
            different event from a full-loss initial stop.
    """
    reason = str(close_reason or "").lower().strip()

    base = EXIT_UNKNOWN
    for probe, exit_class in _PROBES:
        if probe in reason:
            base = exit_class
            break

    if base is not EXIT_STOP_LOSS:
        return base

    # SL-family exit: distinguish a profit-locking (trailed) stop from a
    # full-loss initial stop. Prefer the stop-level evidence when available;
    # fall back to the realized direction of the exit.
    entry = float(entry_price or 0.0)
    exit_p = float(exit_price or 0.0)
    long = _is_long(direction)

    if stop_loss is not None and entry > 0:
        sl = float(stop_loss)
        if long and sl > entry:
            return EXIT_TRAILING_SL
        if not long and sl < entry:
            return EXIT_TRAILING_SL

    if entry > 0 and exit_p > 0:
        if long and exit_p > entry:
            return EXIT_TRAILING_SL
        if not long and exit_p < entry:
            return EXIT_TRAILING_SL

    return EXIT_STOP_LOSS


def exit_alert_kind(exit_class: str) -> str:
    """Map a canonical exit class to the alert kind used for templates.

    Returns one of ``"target_hit"``, ``"stop_loss_hit"`` or
    ``"position_closed"`` — mirroring the three dedicated Telegram
    templates. Both the engine close path and the alert manager's generic
    position_closed branch route through this mapping so a given exit can
    never render through different templates at different layers.
    """
    if exit_class == EXIT_TARGET:
        return "target_hit"
    if exit_class in (EXIT_STOP_LOSS, EXIT_TRAILING_SL):
        return "stop_loss_hit"
    return "position_closed"
