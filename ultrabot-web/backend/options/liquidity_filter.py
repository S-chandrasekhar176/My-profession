"""Liquidity filter for option strikes.

Filters an option chain to keep only liquid strikes based on
open interest and volume thresholds.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default thresholds
_DEFAULT_MIN_OI = 5000
_DEFAULT_MIN_VOLUME = 100
_DEFAULT_MIN_BID_ASK_SPREAD_PCT = 5.0  # Max 5% spread
_DEFAULT_MAX_STRIKES_FROM_ATM = 10  # Only keep within 10 strikes of ATM


class LiquidityFilter:
    """Filter option chain data to keep only liquid strikes.

    A strike is considered liquid if:
    - OI >= min_oi
    - Volume >= min_volume
    - Bid-ask spread is reasonable
    - Within a configurable number of strikes from ATM
    """

    def __init__(
        self,
        min_oi: int = _DEFAULT_MIN_OI,
        min_volume: int = _DEFAULT_MIN_VOLUME,
        max_bid_ask_spread_pct: float = _DEFAULT_MIN_BID_ASK_SPREAD_PCT,
        max_strikes_from_atm: int = _DEFAULT_MAX_STRIKES_FROM_ATM,
    ):
        self.min_oi = min_oi
        self.min_volume = min_volume
        self.max_bid_ask_spread_pct = max_bid_ask_spread_pct
        self.max_strikes_from_atm = max_strikes_from_atm

    def filter_liquid_strikes(
        self,
        chain: Dict[str, Any],
        min_oi: Optional[int] = None,
        min_volume: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Filter an option chain dict to keep only liquid strikes.

        Args:
            chain: Dict as returned by OptionChainFetcher with
                'calls', 'puts', 'atm_strike', 'spot_price'.
            min_oi: Override min OI for this call.
            min_volume: Override min volume for this call.

        Returns:
            Filtered chain dict with the same structure but fewer
            strikes in 'calls' and 'puts'.
        """
        threshold_oi = min_oi if min_oi is not None else self.min_oi
        threshold_vol = min_volume if min_volume is not None else self.min_volume

        atm_strike = float(chain.get("atm_strike", 0))
        spot = float(chain.get("spot_price", 0))

        # Effective ATM if not provided
        if atm_strike <= 0 and spot > 0:
            atm_strike = round(spot / 10.0) * 10.0

        filtered_calls = self._filter_chain_side(
            chain.get("calls", []),
            threshold_oi,
            threshold_vol,
            atm_strike,
        )
        filtered_puts = self._filter_chain_side(
            chain.get("puts", []),
            threshold_oi,
            threshold_vol,
            atm_strike,
        )

        return {
            "symbol": chain.get("symbol", ""),
            "expiry": chain.get("expiry", ""),
            "spot_price": spot,
            "atm_strike": atm_strike,
            "calls": filtered_calls,
            "puts": filtered_puts,
            "total_calls_before": len(chain.get("calls", [])),
            "total_puts_before": len(chain.get("puts", [])),
            "total_calls_after": len(filtered_calls),
            "total_puts_after": len(filtered_puts),
        }

    def validate_strike_liquidity(
        self,
        contract: Dict[str, Any],
        min_oi: Optional[int] = None,
        min_volume: Optional[int] = None,
        max_spread_pct: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Validate if a specific option contract passes liquidity gates."""
        if not contract:
            return False, "Empty option contract data"

        threshold_oi = min_oi if min_oi is not None else self.min_oi
        threshold_vol = min_volume if min_volume is not None else self.min_volume
        threshold_spread = max_spread_pct if max_spread_pct is not None else self.max_bid_ask_spread_pct

        oi = int(contract.get("oi", contract.get("openInterest", contract.get("open_interest", 0))) or 0)
        volume = int(contract.get("volume", 0) or 0)

        if oi < threshold_oi:
            return False, f"Open interest ({oi:,}) below minimum threshold ({threshold_oi:,})"

        if volume < threshold_vol:
            return False, f"Volume ({volume:,}) below minimum threshold ({threshold_vol:,})"

        bid = float(contract.get("bid", 0) or 0)
        ask = float(contract.get("ask", 0) or 0)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            if mid > 0:
                spread_pct = (ask - bid) / mid * 100.0
                if spread_pct > threshold_spread:
                    return False, f"Bid-ask spread ({spread_pct:.1f}%) exceeds maximum limit ({threshold_spread:.1f}%)"

        return True, "Liquidity check passed"

    def _filter_chain_side(
        self,
        options: List[Dict[str, Any]],
        min_oi: int,
        min_vol: int,
        atm_strike: float,
    ) -> List[Dict[str, Any]]:
        """Filter a single side (calls or puts) of the chain."""
        if not options:
            return []

        strike_step = self._estimate_strike_step(options)
        max_distance = self.max_strikes_from_atm * strike_step

        filtered = []
        for opt in options:
            strike = float(opt.get("strike", opt.get("strike_price", 0)) or 0)
            oi = int(opt.get("oi", opt.get("openInterest", opt.get("open_interest", 0))) or 0)
            volume = int(opt.get("volume", 0) or 0)

            # Distance from ATM
            if atm_strike > 0 and abs(strike - atm_strike) > max_distance:
                continue

            # OI filter
            if oi < min_oi:
                continue

            # Volume filter
            if volume < min_vol:
                continue

            # Bid-ask spread filter
            bid = float(opt.get("bid", 0) or 0)
            ask = float(opt.get("ask", 0) or 0)
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                if mid > 0:
                    spread_pct = (ask - bid) / mid * 100.0
                    if spread_pct > self.max_bid_ask_spread_pct:
                        continue

            filtered.append(opt)

        return filtered


    @staticmethod
    def _estimate_strike_step(options: List[Dict[str, Any]]) -> float:
        """Estimate the strike step from consecutive strikes."""
        if len(options) < 2:
            return 10.0

        strikes = sorted(set(float(o.get("strike", 0)) for o in options if o.get("strike", 0) > 0))
        if len(strikes) < 2:
            return 10.0

        # Find the minimum difference between consecutive strikes
        min_diff = float('inf')
        for i in range(1, len(strikes)):
            diff = strikes[i] - strikes[i - 1]
            if 0 < diff < min_diff:
                min_diff = diff

        return min_diff if min_diff != float('inf') else 10.0

    def get_most_liquid_strike(
        self,
        chain: Dict[str, Any],
        option_type: str = "CE",
        direction: str = "LONG",
    ) -> Optional[Dict[str, Any]]:
        """Find the most liquid strike for a given option type.

        Filters by liquidity and then picks the one with highest OI.

        Args:
            chain: Full option chain dict.
            option_type: 'CE' or 'PE'.
            direction: 'LONG' or 'SHORT'.

        Returns:
            The option dict with highest OI, or None.
        """
        filtered = self.filter_liquid_strikes(chain)

        if option_type.upper() == "PE":
            candidates = filtered.get("puts", [])
        else:
            candidates = filtered.get("calls", [])

        if not candidates:
            return None

        # Sort by OI descending, then volume descending
        candidates.sort(
            key=lambda o: (
                int(o.get("openInterest", o.get("open_interest", 0))),
                int(o.get("volume", 0)),
            ),
            reverse=True,
        )

        return candidates[0] if candidates else None
