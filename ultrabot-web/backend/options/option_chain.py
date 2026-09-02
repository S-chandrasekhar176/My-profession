"""Fyers Real-Time Option Chain Fetcher and Parser.

Parses Fyers API v3 option chain responses, computes ATM strikes,
and performs automatic expiry rollover for near-expiry contracts.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Default minimum days before expiry before triggering automatic rollover
_DEFAULT_MIN_DAYS_TO_EXPIRY = 2


class OptionChainFetcher:
    """Fetch and parse real option chain data from Fyers API v3.

    Handles real option chain payloads with:
    - Real Fyers option symbols (e.g. 'NSE:NIFTY24AUG24000CE')
    - Automatic expiry rollover when nearest expiry <= min_days_to_expiry
    - Standardized calls/puts dictionary separation with greeks and liquidity metrics
    """

    def __init__(self, broker: Any = None, min_days_to_expiry: int = _DEFAULT_MIN_DAYS_TO_EXPIRY):
        self.broker = broker
        self.min_days_to_expiry = min_days_to_expiry

    async def fetch_option_chain(
        self,
        symbol: str,
        expiry_date: Optional[str] = None,
        strikecount: int = 12,
        min_days_to_expiry: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Fetch option chain for a given underlying symbol and resolve optimal expiry.

        Args:
            symbol: Underlying symbol (e.g. 'NIFTY', 'BANKNIFTY', 'RELIANCE').
            expiry_date: Optional specific expiry date timestamp or string.
            strikecount: Number of strikes above/below ATM to fetch.
            min_days_to_expiry: Override days before expiry to trigger rollover.

        Returns:
            Dict containing: symbol, expiry, spot_price, calls, puts, atm_strike, expiries.
        """
        if self.broker is None or not hasattr(self.broker, "get_option_chain"):
            logger.warning("No broker with get_option_chain configured on OptionChainFetcher")
            return self._empty_chain(symbol, expiry_date or "")

        try:
            raw_data = await self.broker.get_option_chain(
                symbol=symbol,
                strikecount=strikecount,
                timestamp=expiry_date or "",
            )
            return self.parse_fyers_chain(
                raw_data=raw_data,
                symbol=symbol,
                min_days_to_expiry=min_days_to_expiry or self.min_days_to_expiry,
            )
        except Exception as exc:
            logger.error("Failed to fetch/parse option chain for %s: %s", symbol, exc, exc_info=True)
            return self._empty_chain(symbol, expiry_date or "")

    def parse_fyers_chain(
        self,
        raw_data: Dict[str, Any],
        symbol: str,
        min_days_to_expiry: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Parse raw Fyers option chain response into structured format.

        Applies expiry rollover if the nearest expiry is <= min_days_to_expiry.
        """
        if not isinstance(raw_data, dict) or raw_data.get("s") != "ok":
            data_body = raw_data.get("data", raw_data) if isinstance(raw_data, dict) else {}
        else:
            data_body = raw_data.get("data", {})

        options_raw = data_body.get("optionsChain", [])
        expiry_list = data_body.get("expiryData", [])

        if not options_raw:
            return self._empty_chain(symbol, "")

        # 1. Resolve Active Expiry with Rollover
        active_expiry_epoch, active_expiry_str, rolled_over = self.resolve_tradeable_expiry(
            expiry_list=expiry_list,
            min_days_to_expiry=min_days_to_expiry or self.min_days_to_expiry,
        )

        calls: List[Dict[str, Any]] = []
        puts: List[Dict[str, Any]] = []
        spot_price = 0.0

        for item in options_raw:
            # Check expiry filter if present
            item_expiry = item.get("expiry")
            if active_expiry_epoch and item_expiry and item_expiry != active_expiry_epoch:
                continue

            strike = float(item.get("strike_price", 0.0) or 0.0)
            opt_type = str(item.get("option_type", "")).upper()
            fyers_sym = item.get("symbol", "")
            ltp = float(item.get("ltp", 0.0) or 0.0)
            oi = int(item.get("oi", 0) or 0)
            vol = int(item.get("volume", 0) or 0)
            bid = float(item.get("bid", 0.0) or 0.0)
            ask = float(item.get("ask", 0.0) or 0.0)
            iv = float(item.get("iv", 0.0) or 0.0)
            delta = float(item.get("delta", 0.0) or 0.0)

            # Detect spot price from underlying or ATM option metadata
            if spot_price <= 0 and item.get("spot_price"):
                spot_price = float(item["spot_price"])

            option_dict = {
                "symbol": fyers_sym,
                "strike": strike,
                "strike_price": strike,
                "option_type": opt_type,
                "ltp": ltp,
                "premium": ltp,
                "oi": oi,
                "openInterest": oi,
                "open_interest": oi,
                "volume": vol,
                "bid": bid,
                "ask": ask,
                "iv": iv,
                "delta": delta,
                "expiry": active_expiry_str or str(item_expiry),
                "expiry_epoch": item_expiry,
            }

            if opt_type == "CE":
                calls.append(option_dict)
            elif opt_type == "PE":
                puts.append(option_dict)

        # Compute ATM strike
        all_strikes = sorted(set([c["strike"] for c in calls] + [p["strike"] for p in puts]))
        if spot_price <= 0 and all_strikes:
            spot_price = all_strikes[len(all_strikes) // 2]

        atm_strike = self._find_atm(all_strikes, spot_price)

        return {
            "symbol": symbol,
            "expiry": active_expiry_str,
            "expiry_epoch": active_expiry_epoch,
            "rolled_over": rolled_over,
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "calls": calls,
            "puts": puts,
            "expiries": expiry_list,
        }

    def resolve_tradeable_expiry(
        self,
        expiry_list: List[Dict[str, Any]],
        min_days_to_expiry: int = _DEFAULT_MIN_DAYS_TO_EXPIRY,
    ) -> tuple[Optional[int], str, bool]:
        """Determine whether to trade the nearest expiry or roll over to the next.

        Returns:
            (expiry_epoch, expiry_str, rolled_over_boolean)
        """
        if not expiry_list:
            return None, "", False

        today = datetime.now(IST).date()
        valid_expiries = []

        for exp_item in expiry_list:
            epoch = exp_item.get("expiry")
            date_str = exp_item.get("date", "")
            exp_date = None

            if epoch:
                try:
                    exp_date = datetime.fromtimestamp(epoch, tz=IST).date()
                except Exception:
                    pass
            elif date_str:
                for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
                    try:
                        exp_date = datetime.strptime(date_str, fmt).date()
                        break
                    except Exception:
                        pass

            if exp_date and exp_date >= today:
                days_left = (exp_date - today).days
                valid_expiries.append((days_left, epoch, date_str or str(exp_date)))

        if not valid_expiries:
            first = expiry_list[0]
            return first.get("expiry"), first.get("date", ""), False

        valid_expiries.sort(key=lambda x: x[0])

        nearest_days, nearest_epoch, nearest_str = valid_expiries[0]

        # Trigger rollover if nearest expiry <= min_days_to_expiry and next expiry is available
        if nearest_days <= min_days_to_expiry and len(valid_expiries) > 1:
            next_days, next_epoch, next_str = valid_expiries[1]
            logger.info(
                "Expiry Rollover: Nearest expiry (%s, %d days left) <= %d days. Rolling to next expiry (%s).",
                nearest_str, nearest_days, min_days_to_expiry, next_str,
            )
            return next_epoch, next_str, True

        return nearest_epoch, nearest_str, False

    @staticmethod
    def _find_atm(strikes: List[float], spot: float) -> float:
        """Find the strike closest to the spot price."""
        if not strikes:
            return round(spot, 0)
        return min(strikes, key=lambda s: abs(s - spot))

    @staticmethod
    def _empty_chain(symbol: str, expiry: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "expiry": expiry,
            "expiry_epoch": None,
            "rolled_over": False,
            "spot_price": 0.0,
            "calls": [],
            "puts": [],
            "atm_strike": 0.0,
            "expiries": [],
        }
