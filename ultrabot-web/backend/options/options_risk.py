"""Options-specific risk checker.

Validates capital limits, max loss, and position sizing
constraints before an options trade is placed.
"""
import logging
from typing import Any, Dict, Optional

from utils.formatters import format_currency

logger = logging.getLogger(__name__)

# Default capital constraints
_DEFAULT_MAX_CAPITAL_PER_TRADE_PCT = 0.05  # 5% of total capital
_DEFAULT_MAX_TOTAL_CAPITAL_PCT = 0.30  # 30% of total capital
_DEFAULT_MAX_LOSS_PER_TRADE_PCT = 0.02  # 2% of total capital


class OptionsRiskChecker:
    """Check capital limits and risk constraints for options trades.

    Ensures that:
    - Single trade doesn't exceed max capital allocation
    - Total capital in use doesn't exceed portfolio limit
    - Potential loss is within acceptable bounds
    - Premium is not excessive relative to underlying
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.max_capital_per_trade_pct = float(
            self.config.get("max_capital_per_trade_pct", _DEFAULT_MAX_CAPITAL_PER_TRADE_PCT)
        )
        self.max_total_capital_pct = float(
            self.config.get("max_total_capital_pct", _DEFAULT_MAX_TOTAL_CAPITAL_PCT)
        )
        self.max_loss_per_trade_pct = float(
            self.config.get("max_loss_per_trade_pct", _DEFAULT_MAX_LOSS_PER_TRADE_PCT)
        )

    def check_capital_limits(
        self,
        entry_price: float,
        lot_size: int,
        premium: float,
        total_capital: float,
        capital_in_use: float = 0.0,
        is_buying: bool = True,
        quantity: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Check if an options trade fits within capital limits.

        Args:
            entry_price: Underlying entry price.
            lot_size: Number of shares per lot.
            premium: Option premium per share.
            total_capital: Total available capital.
            capital_in_use: Capital already deployed in open positions.
            is_buying: True for long options (buying CE/PE), False for writing options.
            quantity: ACTUAL planned quantity (lots × lot_size). Defaults to a
                single lot for backward compatibility.

        Returns:
            Dict with keys:
                - passed: bool
                - margin_required: float
                - premium_cost: float
                - max_loss: float (premium paid for long, unlimited for short)
                - capital_in_use_after: float
                - reasons: list of str (empty if passed)
                - warnings: list of str (non-blocking)
        """
        reasons = []
        warnings = []

        # Calculate trade cost
        qty = int(quantity) if quantity and int(quantity) > 0 else lot_size
        premium_cost = premium * qty
        # For option buyers, max capital requirement is the premium paid
        margin_required = premium_cost if is_buying else max(premium_cost, entry_price * qty * 0.15)

        # Max loss calculation: premium paid for long options, tail risk for short options
        if is_buying:
            max_loss = premium_cost
        else:
            # For option sellers, tail risk is high (strike difference buffer minus premium received)
            strike_diff_est = entry_price * 0.20
            max_loss = max(margin_required, (strike_diff_est * qty) - premium_cost)


        # Capital in use after this trade
        capital_in_use_after = capital_in_use + margin_required

        # Check 1: Single trade capital limit
        max_capital_per_trade = total_capital * self.max_capital_per_trade_pct
        if margin_required > max_capital_per_trade:
            reasons.append(
                f"Trade margin ({format_currency(margin_required)}) exceeds max per-trade limit ({format_currency(max_capital_per_trade)})"
            )

        # Check 2: Total capital usage limit
        max_total_capital = total_capital * self.max_total_capital_pct
        if capital_in_use_after > max_total_capital:
            reasons.append(
                f"Capital in use after trade ({format_currency(capital_in_use_after)}) would exceed limit ({format_currency(max_total_capital)})"
            )

        # Check 3: Max loss per trade
        max_loss_limit = total_capital * self.max_loss_per_trade_pct
        if max_loss > max_loss_limit:
            reasons.append(
                f"Max loss ({format_currency(max_loss)}) exceeds per-trade loss limit ({format_currency(max_loss_limit)})"
            )

        # Check 4: Premium as % of underlying (warning if > 5%)
        if entry_price > 0:
            premium_pct = (premium / entry_price) * 100
            if premium_pct > 5.0:
                warnings.append(f"Premium is {premium_pct:.1f}% of underlying price (high)")
            elif premium_pct > 3.0:
                warnings.append(f"Premium is {premium_pct:.1f}% of underlying price (moderate)")

        # Check 5: Capital in use as % of total (warning if > 50%)
        if total_capital > 0:
            usage_pct = (capital_in_use_after / total_capital) * 100
            if usage_pct > 50 and not any("exceed" in r for r in reasons):
                warnings.append(f"Capital usage would be {usage_pct:.1f}% of total")

        passed = len(reasons) == 0

        return {
            "passed": passed,
            "margin_required": round(margin_required, 2),
            "premium_cost": round(premium_cost, 2),
            "max_loss": round(max_loss, 2),
            "capital_in_use_after": round(capital_in_use_after, 2),
            "reasons": reasons,
            "warnings": warnings,
        }
