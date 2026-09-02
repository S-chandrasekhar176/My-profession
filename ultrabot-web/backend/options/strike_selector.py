"""Select the optimal option strike for a given trade direction and market conditions.

Uses spot price, option chain, VIX, and dynamic instrument-specific strike step rules
to pick an ATM or slightly OTM strike with full tradeable Fyers contract symbol.
"""
import logging
from typing import Any, Dict, Optional, List

from utils.market_utils import get_lot_size, get_stock_info

logger = logging.getLogger(__name__)

# Typical NSE index/stock strike steps
_DEFAULT_STRIKE_STEP = 10.0


class StrikeSelector:
    """Select an option strike for entry.

    Selection logic:
    1. Find accurate strike step (50 for Nifty, 100 for BankNifty, price bands for equities).
    2. Find ATM strike closest to spot.
    3. For LONG (BUY): Select ATM or 1-2 strikes OTM Call (CE).
    4. For SHORT (SELL): Select ATM or 1-2 strikes OTM Put (PE).
    5. Adjust OTM offset based on VIX (lower VIX allows 1-2 strikes OTM, elevated VIX sticks to ATM).
    6. If an Option Chain is passed, match the exact tradeable Fyers contract symbol and live premium.
    """

    def __init__(self, lot_size_override: Optional[Dict[str, int]] = None):
        self._lot_size_override = lot_size_override or {}

    def select_strike(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        sl: float = 0.0,
        target: float = 0.0,
        vix: float = 15.0,
        option_chain: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Select optimal strike for a trade.

        Args:
            symbol: Underlying symbol (e.g. 'RELIANCE', 'NIFTY', 'BANKNIFTY').
            direction: 'LONG'/'BUY' or 'SHORT'/'SELL'.
            entry_price: Current spot price of underlying.
            sl: Stop-loss price on underlying.
            target: Target price on underlying.
            vix: India VIX value for volatility adjustment.
            option_chain: Optional parsed option chain dict from OptionChainFetcher.

        Returns:
            Dict with: strike, option_type, option_symbol, lot_size, strike_step,
            premium, iv, delta, risk_reward_ratio, atm_strike, selection_reason.
        """
        if entry_price <= 0 and option_chain and option_chain.get("spot_price"):
            entry_price = float(option_chain["spot_price"])

        if entry_price <= 0:
            return self._empty_result(symbol, "Invalid entry price")

        # Determine dynamic strike step
        strike_step = self._get_strike_step(symbol, entry_price)

        # Determine ATM strike
        atm_strike = round(entry_price / strike_step) * strike_step

        # Determine option type based on direction
        direction_upper = direction.upper()
        if direction_upper in ("LONG", "BUY"):
            option_type = "CE"
            offset = self._compute_offset(vix, direction_upper, strike_step)
            selected_strike = atm_strike + offset
        elif direction_upper in ("SHORT", "SELL"):
            option_type = "PE"
            offset = self._compute_offset(vix, direction_upper, strike_step)
            selected_strike = atm_strike - offset
        else:
            return self._empty_result(symbol, f"Unknown direction: {direction}")

        # Lot size
        lot_size = self._get_lot_size(symbol)

        # Approximate theoretical premium estimate fallback
        distance_from_atm = abs(selected_strike - atm_strike)
        base_premium = entry_price * 0.012
        otm_penalty = distance_from_atm * 0.002
        vix_multiplier = max(0.8, vix / 15.0)
        premium_estimate = max(1.0, (base_premium - otm_penalty) * vix_multiplier)
        premium_estimate = round(premium_estimate, 2)

        # Risk-reward ratio on underlying
        sl_distance = abs(entry_price - sl) if sl > 0 else 0
        target_distance = abs(target - entry_price) if target > 0 else 0
        risk_reward = round(target_distance / sl_distance, 2) if sl_distance > 0 else 0.0

        # Match exact tradeable contract from option_chain if provided
        option_symbol = ""
        actual_premium = premium_estimate
        iv = 0.0
        delta = 0.50 if option_type == "CE" else -0.50
        expiry_str = ""

        if option_chain:
            expiry_str = option_chain.get("expiry", "")
            side_options: List[Dict[str, Any]] = (
                option_chain.get("calls", []) if option_type == "CE" else option_chain.get("puts", [])
            )
            if side_options:
                # Find exact or closest strike
                matched = min(side_options, key=lambda o: abs(float(o.get("strike", 0)) - selected_strike))
                selected_strike = float(matched.get("strike", selected_strike))
                option_symbol = matched.get("symbol", "")
                actual_premium = float(matched.get("ltp") or matched.get("ask") or matched.get("premium") or premium_estimate)
                iv = float(matched.get("iv", 0.0) or 0.0)
                delta = float(matched.get("delta", 0.0) or (0.50 if option_type == "CE" else -0.50))

        # Selection reason
        if abs(selected_strike - atm_strike) < 0.01:
            reason = "ATM strike selected"
        else:
            steps_otm = round(abs(offset / strike_step))
            reason = f"{steps_otm} strike{'s' if steps_otm != 1 else ''} OTM"

        return {
            "strike": selected_strike,
            "option_type": option_type,
            "option_symbol": option_symbol,
            "lot_size": lot_size,
            "strike_step": strike_step,
            "premium": round(actual_premium, 2),
            "premium_estimate": round(actual_premium, 2),
            "iv": iv,
            "delta": delta,
            "expiry": expiry_str,
            "risk_reward_ratio": risk_reward,
            "atm_strike": atm_strike,
            "selection_reason": reason,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_offset(vix: float, direction: str, strike_step: float) -> float:
        """Compute how many strike steps OTM to go based on VIX.
        
        - Calm VIX (< 13): 1 strike OTM (cheaper premium, higher convexity)
        - Normal VIX (13-18): 1 strike OTM
        - Elevated/Volatile VIX (> 18): ATM strike (preserves delta, minimizes theta decay risk)
        """
        if vix < 13:
            offset_steps = 1
        elif vix < 18:
            offset_steps = 1
        else:
            offset_steps = 0

        return float(offset_steps * strike_step)

    def _get_strike_step(self, symbol: str, price: float = 0.0) -> float:
        """Get accurate NSE strike step for indices and equities."""
        sym_clean = symbol.upper().replace(" ", "").replace("_", "")
        if "BANKNIFTY" in sym_clean or "SENSEX" in sym_clean:
            return 100.0
        if "NIFTY" in sym_clean or "FINNIFTY" in sym_clean:
            return 50.0

        if price > 10000:
            return 100.0
        elif price > 4000:
            return 50.0
        elif price > 1500:
            return 20.0
        elif price > 500:
            return 10.0
        elif price > 200:
            return 5.0
        elif price > 0:
            return 2.5
        return _DEFAULT_STRIKE_STEP

    def _get_lot_size(self, symbol: str) -> int:
        """Get lot size, using override if available."""
        if symbol in self._lot_size_override:
            return self._lot_size_override[symbol]
        return get_lot_size(symbol)

    @staticmethod
    def _empty_result(symbol: str, reason: str) -> Dict[str, Any]:
        return {
            "strike": 0.0,
            "option_type": "",
            "option_symbol": "",
            "lot_size": 0,
            "strike_step": 0.0,
            "premium": 0.0,
            "premium_estimate": 0.0,
            "iv": 0.0,
            "delta": 0.0,
            "expiry": "",
            "risk_reward_ratio": 0.0,
            "atm_strike": 0.0,
            "selection_reason": reason,
        }
