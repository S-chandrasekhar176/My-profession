"""Shadow-outcome recorder — pure, engine-free logic (v0.4.11).

Everything here is deterministic and unit-testable without the engine or a
database. The engine (core/engine.py) calls these helpers at four hook
points:

  1. strategy-shadow registration   (existing Phase-1 path, now tagged)
  2. never-traded registration      (TTL expiry / user skip / orphan sweep)
  3. gate-blocked registration      (blocked signals finally enter the dataset)
  4. resolution                     (_evaluate_shadow_signals: MFE/MAE + row)

Promotion-ladder rule encoded here: a shadow sample only counts toward the
Gate-2 clock when BOTH registration and resolution happened on the realtime
(primary) feed — backup-feed samples are recorded but flagged out.
"""
from typing import Any, Dict, List, Optional


# Registry entry kinds
KIND_STRATEGY_SHADOW = "strategy_shadow"
KIND_NEVER_TRADED = "never_traded"
KIND_GATE_BLOCKED = "gate_blocked"

# Outcomes (kept identical to the existing engine vocabulary)
OUTCOME_TARGET = "SHADOW_TARGET"
OUTCOME_SL = "SHADOW_SL"
OUTCOME_EXPIRED = "SHADOW_EXPIRED"


def feed_is_realtime(feed: Any) -> bool:
    """Classify the feed as realtime (primary) vs backup at this instant.

    - No feed object at all  -> False (cannot verify: do NOT count the sample)
    - FeedManager with _using_backup=True -> False (Yahoo backup, ladder rule)
    - Anything else          -> True (plain primary feed)
    """
    if feed is None:
        return False
    using_backup = getattr(feed, "_using_backup", None)
    if using_backup is None:
        # Not a FeedManager — a bare feed object is the primary by definition.
        return True
    return not bool(using_backup)


def extract_blocking_gates(risk_result: Optional[Dict[str, Any]]) -> List[str]:
    """Names of the gates that FAILED from a risk_result dict.

    risk_result["all_gates"] entries are GateResult dumps with at least
    gate_name + passed. Defensive against missing/renamed keys — a broken
    gate list must never crash the scan loop.
    """
    if not isinstance(risk_result, dict):
        return []
    gates = risk_result.get("all_gates")
    if not isinstance(gates, (list, tuple)):
        return []
    failed: List[str] = []
    for g in gates:
        if not isinstance(g, dict):
            continue
        passed = g.get("passed")
        if passed is not False:
            continue
        name = g.get("gate_name") or g.get("gate") or g.get("name") or "UNKNOWN_GATE"
        failed.append(str(name))
    return failed


def compute_shadow_outcome(
    entry: float, sl: float, target: float, price: float, is_long: bool
) -> Optional[tuple]:
    """(outcome, exit_price) for one price tick, mirroring the engine's rules.

    Long : price >= target -> TARGET at target; price <= sl -> SL at sl.
    Short: mirrored. None when unresolved at this tick.
    """
    if entry <= 0 or sl <= 0 or target <= 0 or price <= 0:
        return None
    if is_long:
        if price >= target:
            return (OUTCOME_TARGET, target)
        if price <= sl:
            return (OUTCOME_SL, sl)
    else:
        if price <= target:
            return (OUTCOME_TARGET, target)
        if price >= sl:
            return (OUTCOME_SL, sl)
    return None


def should_expire(age_minutes: Optional[float], max_age_minutes: float, eod: bool) -> bool:
    """Time-based expiry: aged out or session ending (market safe-exit)."""
    if eod:
        return True
    if age_minutes is None:
        return False
    return age_minutes >= max_age_minutes


def update_excursion(state: Dict[str, Any], price: float, is_long: bool) -> None:
    """Update running MFE/MAE (per-share price points) on the registry state.

    LTP-basis honesty: we only see last-traded prices through get_latest_price,
    not intrabar highs/lows — MFE/MAE are therefore lower bounds of the true
    excursion. Documented limitation, never presented as exact.
    """
    if price <= 0:
        return
    entry = float(state.get("entry_price") or 0.0)
    if entry <= 0:
        return
    move = (price - entry) if is_long else (entry - price)
    if move > 0:
        state["mfe"] = max(float(state.get("mfe") or 0.0), move)
    else:
        state["mae"] = max(float(state.get("mae") or 0.0), -move)


def pnl_per_share(entry: float, exit_price: float, is_long: bool) -> float:
    """Gross per-share hypothetical P&L (quantity was never sized)."""
    if is_long:
        return exit_price - entry
    return entry - exit_price
