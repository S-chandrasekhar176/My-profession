"""Capture error context – engine state, positions, market data, etc."""
import traceback
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def capture_context(
    engine_state: Optional[Dict[str, Any]] = None,
    open_positions: Optional[list] = None,
    active_broker: Optional[str] = None,
    feed_status: Optional[str] = None,
    vix: Optional[float] = None,
    regime: Optional[str] = None,
    timestamp: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Build a context dict for error reporting.

    Captures:
    - timestamp (IST)
    - engine state summary
    - open positions count and symbols
    - active broker
    - feed status
    - VIX
    - market regime
    - stack trace of caller
    - any additional kwargs
    """
    ctx: Dict[str, Any] = {
        "timestamp": timestamp or datetime.now(IST).isoformat(),
        "engine_state": _summarize_engine_state(engine_state),
        "open_positions": _summarize_positions(open_positions),
        "active_broker": active_broker or "none",
        "feed_status": feed_status or "unknown",
        "vix": vix,
        "regime": regime,
    }

    # Capture simplified stack trace (last 5 frames)
    stack = traceback.extract_stack(limit=10)
    ctx["stack_trace_summary"] = [
        {"file": f.filename.split("/")[-1], "line": f.lineno, "func": f.name}
        for f in stack[-5:]
    ]

    # Merge additional kwargs
    ctx.update(kwargs)
    return ctx


def _summarize_engine_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a safe summary of engine state."""
    if state is None:
        return {"status": "unknown"}
    summary: Dict[str, Any] = {}
    safe_keys = [
        "status", "session_id", "scan_count", "last_scan_time",
        "is_market_open", "current_regime", "vix_value",
    ]
    for key in safe_keys:
        if key in state:
            summary[key] = state[key]
    if not summary:
        summary["raw_keys"] = list(state.keys())
    return summary


def _summarize_positions(positions: Optional[list]) -> Dict[str, Any]:
    """Return a safe summary of open positions."""
    if positions is None:
        return {"count": 0, "symbols": []}
    symbols = []
    count = 0
    for p in positions:
        count += 1
        if isinstance(p, dict):
            symbols.append({
                "symbol": p.get("symbol", "?"),
                "direction": p.get("direction", "?"),
                "qty": p.get("quantity", p.get("remaining_qty", "?")),
            })
        else:
            symbols.append({
                "symbol": getattr(p, "symbol", "?"),
                "direction": getattr(p, "direction", "?"),
                "qty": getattr(p, "quantity", getattr(p, "remaining_qty", "?")),
            })
    return {"count": count, "symbols": symbols}
