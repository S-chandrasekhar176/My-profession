"""Realistic slippage model for paper fills (P2-b).

Paper fills previously executed at the exact LTP — a zero-friction fantasy
that inflates strategy performance versus live trading. This module models
the two dominant market-order frictions on NSE equities:

* HALF-SPREAD cost: a market order crosses the bid-ask spread. Liquid F&O
  underlyings typically quote 1–5 ticks wide; ~5 bps (0.05%) is a defensible
  default for the half-spread on a ₹1000-class stock.
* SIZE IMPACT: orders large relative to typical liquidity walk the book.
  Modelled linearly as extra bps per ₹1 crore of order value, capped at
  max_bps so pathological sizes stay bounded.

Formula:
    slippage_bps = min(max_bps, base_bps + impact_bps_per_crore × order_value/1e7)

BUY fills pay it upward (worse entry); SELL fills pay it downward (worse
exit). The model is deterministic (no random noise) so paper results are
reproducible and comparable run-to-run; every fill reports its applied
slippage for transparency.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

_CRORE = 1e7


def estimate_slippage_bps(order_value: float, config: Dict[str, Any]) -> float:
    """Slippage in basis points for an order of the given value.

    Returns 0.0 when disabled, order value is non-positive, or the config
    is malformed — the model must never break order placement.
    """
    try:
        if not config or not bool(config.get("enabled", False)):
            return 0.0
        if order_value is None or order_value <= 0:
            return 0.0

        base_bps = float(config.get("base_bps", 5.0))
        per_cr = float(config.get("impact_bps_per_crore", 2.0))
        max_bps = float(config.get("max_bps", 25.0))

        impact_bps = per_cr * (float(order_value) / _CRORE)
        return max(0.0, min(max_bps, base_bps + impact_bps))
    except (TypeError, ValueError):
        return 0.0


def apply_slippage(
    price: float,
    is_buy: bool,
    quantity: int,
    config: Dict[str, Any],
) -> Tuple[float, float, float]:
    """Apply slippage to a reference price.

    Returns (fill_price, slippage_bps, slippage_amount):
      fill_price      — price worsened by the slippage (rounded to 2dp)
      slippage_bps    — the applied basis points (0 when disabled)
      slippage_amount — total ₹ impact on this fill (|fill − price| × qty)

    BUY  → fill = price × (1 + bps/10000)  (pay up to get filled)
    SELL → fill = price × (1 − bps/10000)  (sell down to get filled)
    """
    if price is None or price <= 0 or quantity is None or quantity <= 0:
        return round(float(price or 0.0), 2), 0.0, 0.0

    order_value = float(price) * int(quantity)
    bps = estimate_slippage_bps(order_value, config)
    if bps <= 0:
        return round(float(price), 2), 0.0, 0.0

    factor = bps / 10000.0
    fill = float(price) * (1.0 + factor if is_buy else 1.0 - factor)
    fill = round(fill, 2)
    amount = round(abs(fill - float(price)) * int(quantity), 2)
    return fill, bps, amount
