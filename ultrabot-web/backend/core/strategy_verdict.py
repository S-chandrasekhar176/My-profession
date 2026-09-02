"""Strategy promote/retire verdict engine (P3).

Turns accumulated SHADOW-signal statistics into evidence-based promote /
retire decisions — the decision layer the P2 shadow ledger feeds.

Rules (documented, deterministic, no opinion):
  * A verdict requires MIN_SAMPLE resolved shadow signals (default 100).
  * Breakeven win-rate is fee-adjusted: the known per-strategy economics
    where measured (v0.4.1 analysis), else derived from the strategy's own
    average shadow risk:reward (BE = 1 / (1 + RR)), else a conservative
    default.
  * PROMOTE_CANDIDATE: sample ≥ MIN_SAMPLE AND win-rate ≥ breakeven + margin
  * RETIRE_CANDIDATE:  sample ≥ MIN_SAMPLE AND win-rate < breakeven − margin
  * KEEP_COLLECTING:   sample < MIN_SAMPLE (no verdict yet — keep shadowing)
  * NO_DATA:           no shadow signals recorded at all

Live (v2) strategies get HEALTH bars on the same math — MRF's 50.2%
breakeven means it needs the strongest shadow record to justify its slot.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Fee-adjusted breakeven win-rates (%) measured for the v2 strategies
# (round-trip NSE fees + slippage included; from the v0.4.1 economics audit).
FEE_ADJUSTED_BREAKEVEN: Dict[str, float] = {
    "ORB": 36.8,
    "MB": 36.1,
    "PTC": 39.4,
    "SIC": 37.8,
    "VC": 39.8,
    "MRF": 50.2,
    "TRS": 31.0,
}

DEFAULT_BREAKEVEN = 45.0   # conservative fallback when no measured economics
MIN_SAMPLE = 100            # resolved signals before any verdict
PROMOTE_MARGIN_PCT = 3.0    # pp above breakeven to recommend promotion
RETIRE_MARGIN_PCT = 3.0     # pp below breakeven to recommend retirement


def _breakeven_for(strategy: str, avg_rr: Optional[float]) -> float:
    """Fee-adjusted breakeven WR% for a strategy."""
    measured = FEE_ADJUSTED_BREAKEVEN.get((strategy or "").upper())
    if measured:
        return measured
    try:
        if avg_rr and float(avg_rr) > 0:
            return round(100.0 / (1.0 + float(avg_rr)), 1)
    except (TypeError, ValueError):
        pass
    return DEFAULT_BREAKEVEN


def evaluate_strategy_verdicts(
    shadow_stats: Dict[str, Dict[str, Any]],
    live_strategies: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Compute promote/retire verdicts from shadow statistics.

    Args:
        shadow_stats: per-strategy dict as returned by
            Repository.compute_shadow_signal_stats() — keys: total_signals,
            resolved, wins, losses, expired, pending, signal_win_rate,
            (optional) avg_risk_reward.
        live_strategies: names currently TRADING (v2). Their verdicts carry
            an "economics_bar" note instead of promote suggestions.

    Returns a list (sorted: verdict severity then name) of dicts:
        {strategy, total_signals, resolved, signal_win_rate, breakeven_wr,
         margin_needed, verdict, rationale}
    """
    live = {str(s).upper() for s in (live_strategies or [])}
    out: List[Dict[str, Any]] = []

    for name, st in (shadow_stats or {}).items():
        try:
            total = int(st.get("total_signals", 0) or 0)
            resolved = int(st.get("resolved", 0) or 0)
            wins = int(st.get("wins", 0) or 0)
            losses = int(st.get("losses", 0) or 0)
            wr = float(st.get("signal_win_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue

        be = _breakeven_for(name, st.get("avg_risk_reward"))
        key = (name or "").upper()

        if total <= 0:
            verdict, rationale = "NO_DATA", "No shadow signals recorded yet."
        elif resolved < MIN_SAMPLE:
            verdict = "KEEP_COLLECTING"
            rationale = (
                f"Sample {resolved}/{MIN_SAMPLE} resolved — verdicts unlock at "
                f"{MIN_SAMPLE} resolved shadow signals."
            )
        elif wr >= be + PROMOTE_MARGIN_PCT:
            verdict = "PROMOTE_CANDIDATE"
            rationale = (
                f"Win-rate {wr}% ≥ breakeven {be}% + {PROMOTE_MARGIN_PCT}pp margin "
                f"over {resolved} resolved signals."
            )
        elif wr < be - RETIRE_MARGIN_PCT:
            verdict = "RETIRE_CANDIDATE"
            rationale = (
                f"Win-rate {wr}% < breakeven {be}% − {RETIRE_MARGIN_PCT}pp margin "
                f"over {resolved} resolved signals."
            )
        else:
            verdict = "BORDERLINE"
            rationale = (
                f"Win-rate {wr}% is within ±{PROMOTE_MARGIN_PCT}pp of the "
                f"{be}% fee-adjusted breakeven — not decisive either way."
            )

        entry = {
            "strategy": name,
            "is_live": key in live,
            "total_signals": total,
            "resolved": resolved,
            "wins": wins,
            "losses": losses,
            "expired": int(st.get("expired", 0) or 0),
            "pending": int(st.get("pending", 0) or 0),
            "signal_win_rate": wr,
            "breakeven_wr": be,
            "verdict": verdict,
            "rationale": rationale,
        }
        if key in live and key == "MRF":
            entry["economics_note"] = (
                "MRF carries the highest fee-adjusted breakeven (50.2%) — it "
                "must clear a materially higher bar than every other v2 "
                "strategy to keep its live slot."
            )
        out.append(entry)

    order = {"PROMOTE_CANDIDATE": 0, "RETIRE_CANDIDATE": 1, "BORDERLINE": 2, "KEEP_COLLECTING": 3, "NO_DATA": 4}
    out.sort(key=lambda e: (order.get(e["verdict"], 9), e["strategy"]))
    return out
