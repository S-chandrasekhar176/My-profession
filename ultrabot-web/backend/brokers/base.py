from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseBroker(ABC):
    """Abstract base class for all broker integrations.

    Every broker (paper, Angel One, Shoonya, etc.) must implement
    these methods to be compatible with the trading engine.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config: Dict[str, Any] = config or {}

    @abstractmethod
    async def authenticate(self) -> Dict[str, Any]:
        """Authenticate with the broker.

        Returns:
            Dict with at least 'success' (bool) and 'message' (str).
        """

    @abstractmethod
    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Get the last traded price for a symbol.

        Args:
            symbol: Trading symbol (e.g. 'RELIANCE').
            exchange: Exchange name (e.g. 'NSE', 'NFO').

        Returns:
            Last traded price as float.

        Raises:
            BrokerError: If price cannot be fetched.
        """

    @abstractmethod
    async def get_margin(self) -> Dict[str, float]:
        """Get available margin/capital information.

        Returns:
            Dict with keys like 'available', 'used', 'total'.
        """

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float,
        order_type: str = "MARKET",
        product: str = "MIS",
        segment: str = "EQ",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # **kwargs absorbs engine-level order metadata (stop_loss=, target=,
        # direction=...) so callers never hit a TypeError; brokers that do not
        # need them simply ignore the extras (PaperBroker stores them).
        """Place an order.

        Args:
            symbol: Trading symbol.
            exchange: Exchange to route order to.
            transaction_type: 'BUY' or 'SELL'.
            quantity: Number of shares/lots.
            price: Order price (ignored for MARKET orders).
            order_type: 'MARKET' or 'LIMIT'.
            product: Product type ('MIS' for intraday, 'CNC' for delivery, 'NRML' for F&O).
            segment: Market segment ('EQ', 'OPT', 'FUT').

        Returns:
            Dict with 'success', 'order_id', 'message', etc.
        """

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a pending order.

        Args:
            order_id: The broker's order ID.

        Returns:
            Dict with 'success', 'message'.
        """

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions.

        Returns:
            List of position dicts with keys like 'symbol', 'quantity',
            'avg_price', 'pnl', 'side'.
        """

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Check the status of an order.

        Args:
            order_id: The broker's order ID.

        Returns:
            Dict with 'status', 'avg_price', 'filled_qty', etc.
        """

    @abstractmethod
    def get_name(self) -> str:
        """Return the broker's name identifier."""

    async def get_latest_price(self, symbol: str, exchange: str = "NSE") -> float:
        """Get the latest price (LTP) for a symbol."""
        return await self.get_ltp(symbol, exchange)

