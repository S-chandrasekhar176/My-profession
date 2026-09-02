"""Gate G12: Margin Check.

Blocks trades when the required margin for the proposed position
exceeds the available margin/capital.
"""
import re
from typing import Any, Dict, Optional

from models.risk_state import GateResult
from utils.market_utils import get_lot_size, is_fno_stock
from core.capital_resolver import resolve_total_capital



class G12MarginCheck:
    """Ensure sufficient margin is available for the new position."""

    def __init__(self, config: Dict[str, Any]):
        self.max_capital_usage_pct: float = float(
            config.get("max_capital_usage_pct", 90)
        )

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        total_capital = resolve_total_capital(context=context)
        available_margin = float(context.get("available_capital") or context.get("margin_available") or total_capital)
        capital_in_use = float(context.get("capital_in_use") or max(0.0, total_capital - available_margin))

        entry_price = float(
            getattr(signal, "entry_price", 0)
            or (signal.get("entry_price", 0) if isinstance(signal, dict) else 0)
            or context.get("entry_price", 0)
            or context.get("current_price", 0)
            or context.get("broker_ltp", 0)
            or 0.0
        )

        if entry_price <= 0:
            return GateResult(
                gate_name="G12_MarginCheck",
                passed=True,
                message="Entry price not available, gate deferred to execution engine",
                value=0.0,
                threshold=available_margin,
                severity="info",
            )

        # Estimate quantity: use explicit quantity if specified, else F&O lot size or 1
        qty = float(
            getattr(signal, "quantity", 0)
            or (signal.get("quantity", 0) if isinstance(signal, dict) else 0)
            or context.get("quantity", 0)
            or 0
        )
        seg = str(
            getattr(signal, "segment", "")
            or (signal.get("segment", "") if isinstance(signal, dict) else "")
            or context.get("segment", "")
        ).upper()
        sym = str(
            getattr(signal, "symbol", "")
            or (signal.get("symbol", "") if isinstance(signal, dict) else "")
            or context.get("symbol", "")
        )

        is_fno = seg in ("FNO", "F&O", "NFO", "OPTIONS", "FUTURES") or is_fno_stock(sym)
        if qty <= 0:
            if is_fno:
                qty = float(get_lot_size(sym))
            else:
                qty = 1.0

        # Calculate segment-aware margin requirement
        # Equity Intraday: ~20% margin (5x leverage); Delivery/Full: 100%; Options buying: premium * qty
        # NOTE: option-contract detection must NOT use bare substring checks —
        # "CE" is a substring of "RELIAN(CE)" and "PE" of "(PE)TRONET", which
        # misclassified plain equities as options and inflated their margin
        # requirement 4x (1.0x vs 0.25x multiplier). Real option symbols embed
        # the strike before the suffix (e.g. RELIANCE29600CE).
        _is_option_symbol = bool(re.search(r"\d+(CE|PE)$", sym))
        if seg in ("OPTIONS", "OPTION", "NFO_OPT") or _is_option_symbol:
            required_margin = entry_price * qty
        elif seg in ("FUTURES", "FUT", "NFO_FUT"):
            required_margin = entry_price * qty * 0.20
        elif context.get("product_type") == "MIS" or context.get("order_type") == "INTRADAY":
            required_margin = entry_price * qty * 0.20
        else:
            required_margin = entry_price * qty * 0.25

        max_allowed_margin = total_capital * (self.max_capital_usage_pct / 100.0)

        # Check 1: Available margin in account
        if required_margin > available_margin:
            return GateResult(
                gate_name="G12_MarginCheck",
                passed=False,
                message=(
                    f"Required margin (₹{required_margin:,.0f}) exceeds "
                    f"available (₹{available_margin:,.0f})"
                ),
                value=required_margin,
                threshold=available_margin,
                severity="critical",
            )

        # Check 2: Portfolio-wide maximum capital usage limit
        projected_capital_in_use = capital_in_use + required_margin
        if projected_capital_in_use > max_allowed_margin:
            return GateResult(
                gate_name="G12_MarginCheck",
                passed=False,
                message=(
                    f"Projected capital usage (₹{project_capital_usage:,.0f} / {projected_capital_in_use / total_capital * 100:.1f}%) "
                    f"exceeds max allowed {self.max_capital_usage_pct}% (₹{max_allowed_margin:,.0f})"
                ) if (project_capital_usage := projected_capital_in_use) else "",
                value=projected_capital_in_use,
                threshold=max_allowed_margin,
                severity="warning",
            )

        return GateResult(
            gate_name="G12_MarginCheck",
            passed=True,
            message=(
                f"Required margin (₹{required_margin:,.0f}) within available (₹{available_margin:,.0f}), "
                f"projected usage: {projected_capital_in_use / total_capital * 100:.1f}% <= {self.max_capital_usage_pct}%"
            ),
            value=required_margin,
            threshold=available_margin,
            severity="info",
        )
