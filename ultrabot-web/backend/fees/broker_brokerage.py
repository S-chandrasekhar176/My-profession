"""Per-broker brokerage rate calculator.

Each broker has its own brokerage structure:
- paper: Flat ₹20 per order
- angel_one: ₹20 per order OR 0.03% of turnover, whichever is lower
- shoonya: Zero brokerage
"""
from __future__ import annotations

from typing import Dict


class BrokerBrokerageCalculator:
    """Calculate brokerage based on broker-specific rates."""

    # Broker rate definitions: (flat_per_order, percentage_of_turnover)
    # paper: flat only, no percentage
    _RATES: Dict[str, Dict[str, float]] = {
        "paper": {"flat": 20.0, "pct": 0.0},
        "angel_one": {"flat": 20.0, "pct": 0.0003},  # 0.03%
        "shoonya": {"flat": 0.0, "pct": 0.0},
        "zerodha": {"flat": 20.0, "pct": 0.0003},   # 0.03%
        "dhan": {"flat": 20.0, "pct": 0.0003},      # 0.03%
        "fyers": {"flat": 20.0, "pct": 0.0003},     # 0.03%
    }

    def calculate(
        self,
        symbol: str,
        segment: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float,
        lot_size: int = 1,
    ) -> float:
        """Calculate brokerage for a single order.

        Args:
            symbol: Trading symbol (e.g. 'RELIANCE').
            segment: Market segment ('EQ', 'OPT', 'FUT').
            exchange: Exchange ('NSE', 'NFO').
            transaction_type: 'BUY' or 'SELL'.
            quantity: Number of shares or lots.
            price: Order price per share/lot.
            lot_size: Contract lot size (default 1).

        Returns:
            Brokerage amount rounded to 2 decimal places.
        """
        broker_name = self._resolve_broker(symbol, segment, exchange)
        rates = self._RATES.get(broker_name, self._RATES["paper"])

        flat = rates["flat"]
        pct = rates["pct"]

        if pct > 0.0:
            effective_qty = quantity * lot_size if segment == "OPT" else quantity
            turnover = price * effective_qty
            brokerage_by_pct = turnover * pct
            return round(min(flat, brokerage_by_pct), 2)

        return round(flat, 2)

    def calculate_for_broker(
        self,
        broker_name: str,
        quantity: int,
        price: float,
        lot_size: int = 1,
        segment: str = "EQ",
    ) -> float:
        """Calculate brokerage directly for a named broker.

        Args:
            broker_name: One of 'paper', 'angel_one', 'shoonya', 'zerodha', 'dhan', 'fyers'.
            quantity: Number of shares/units.
            price: Price per unit.
            lot_size: Contract lot size (default 1).
            segment: Market segment ('EQ', 'OPT', 'FUT').

        Returns:
            Brokerage amount rounded to 2 decimal places.
        """
        rates = self._RATES.get(broker_name, self._RATES["paper"])
        flat = rates["flat"]
        pct = rates["pct"]

        if pct > 0.0:
            effective_qty = quantity * lot_size if segment == "OPT" else quantity
            turnover = price * effective_qty
            brokerage_by_pct = turnover * pct
            return round(min(flat, brokerage_by_pct), 2)

        return round(flat, 2)

    def get_all_rates(self) -> Dict[str, Dict[str, float]]:
        """Return all broker rates."""
        return dict(self._RATES)

    def _resolve_broker(self, symbol: str, segment: str, exchange: str) -> str:
        """Determine broker from symbol/segment/exchange.

        Default is 'paper'. Override by setting _current_broker.
        """
        return getattr(self, "_current_broker", "paper")

    def set_broker(self, broker_name: str) -> None:
        """Set the active broker for subsequent calculations.

        Args:
            broker_name: One of 'paper', 'angel_one', 'shoonya'.
        """
        if broker_name not in self._RATES:
            raise ValueError(f"Unknown broker: {broker_name}. Available: {list(self._RATES.keys())}")
        self._current_broker = broker_name
