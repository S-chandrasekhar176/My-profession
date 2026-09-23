"""Order Slicer for Indian Exchanges (NSE Equity & F&O).

Enforces exchange-mandated freeze limits and order slice thresholds:
- NIFTY / BANKNIFTY / FINNIFTY freeze quantities (e.g. NIFTY: 1800, BANKNIFTY: 900)
- Single-order equity slice limits (e.g. max 25,000 shares or ₹2 Crore order value)
- Slices oversized parent orders into compliant child tranches.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# NSE standard freeze limits (quantities per single order)
# Source: NSE Circulars on Maximum Order Size / Freeze Limits
FREEZE_LIMITS: Dict[str, int] = {
    "NIFTY": 1800,
    "BANKNIFTY": 900,
    "FINNIFTY": 1800,
    "MIDCPNIFTY": 4200,
    "SENSEX": 1000,
    "BANKEX": 1000,
}

DEFAULT_EQUITY_MAX_QTY = 25000
DEFAULT_EQUITY_MAX_VALUE = 20_000_000.0  # ₹2 Crore per single order


def get_freeze_limit(symbol: str, segment: str = "EQ") -> int:
    """Get the maximum quantity permissible in a single exchange order."""
    sym = (symbol or "").strip().upper()
    
    # Strip prefixes/suffixes like "NSE:", "-EQ", etc.
    if ":" in sym:
        sym = sym.split(":")[-1]
    if sym.endswith("-EQ"):
        sym = sym[:-3]

    seg = str(segment or "EQ").upper()
    if seg in ("FNO", "FUT", "OPT", "FUTURES", "OPTIONS"):
        # Match against index underlyings (longer names first so BANKNIFTY matches before NIFTY)
        for idx, limit in sorted(FREEZE_LIMITS.items(), key=lambda kv: len(kv[0]), reverse=True):
            if idx in sym:
                return limit
        # Default stock F&O freeze limit
        return 10000

    return DEFAULT_EQUITY_MAX_QTY


def slice_order(
    symbol: str,
    quantity: int,
    price: float,
    segment: str = "EQ",
    custom_max_qty: Optional[int] = None,
    custom_max_val: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Slice an order if it exceeds exchange freeze limits or value thresholds.

    Returns a list of child slice dictionaries:
    [
        {"slice_index": 1, "total_slices": N, "quantity": qty_i, "price": price},
        ...
    ]
    """
    qty = int(quantity or 0)
    if qty <= 0:
        return []

    px = float(price or 0.0)
    max_qty = custom_max_qty or get_freeze_limit(symbol, segment)
    max_val = custom_max_val or DEFAULT_EQUITY_MAX_VALUE

    # Value-based quantity cap if price is positive
    if px > 0:
        val_capped_qty = int(max_val / px)
        if val_capped_qty > 0:
            max_qty = min(max_qty, val_capped_qty)

    max_qty = max(1, max_qty)

    if qty <= max_qty:
        return [{
            "slice_index": 1,
            "total_slices": 1,
            "quantity": qty,
            "price": round(px, 2),
            "is_sliced": False,
        }]

    num_slices = math.ceil(qty / max_qty)
    slices: List[Dict[str, Any]] = []
    remaining = qty

    for i in range(1, num_slices + 1):
        slice_qty = min(remaining, max_qty)
        slices.append({
            "slice_index": i,
            "total_slices": num_slices,
            "quantity": slice_qty,
            "price": round(px, 2),
            "is_sliced": True,
        })
        remaining -= slice_qty

    return slices
