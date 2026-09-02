from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseFeed(ABC):
    """Abstract base class for all market data feeds.

    Every feed (Yahoo, Angel WebSocket, Shoonya WebSocket) must implement
    these methods to be compatible with the trading engine.
    """

    @abstractmethod
    async def connect(self) -> Dict[str, Any]:
        """Connect to the data source.

        Returns:
            Dict with 'success' (bool) and 'message' (str).
        """

    @abstractmethod
    async def disconnect(self) -> Dict[str, Any]:
        """Disconnect from the data source.

        Returns:
            Dict with 'success' (bool) and 'message' (str).
        """

    @abstractmethod
    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        """Subscribe to real-time updates for a list of symbols.

        Args:
            symbols: List of symbol strings (e.g. ['RELIANCE', 'TCS']).

        Returns:
            Dict with 'success' and count of subscribed symbols.
        """

    @abstractmethod
    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        """Unsubscribe from updates for a list of symbols.

        Args:
            symbols: List of symbol strings.

        Returns:
            Dict with 'success' and count of unsubscribed symbols.
        """

    @abstractmethod
    async def get_ltp(self, symbol: str) -> float:
        """Get the last traded price for a symbol.

        Args:
            symbol: Trading symbol (e.g. 'RELIANCE').

        Returns:
            Last traded price as float. Returns 0.0 if unavailable.
        """

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get historical candle/OHLCV data.

        Args:
            symbol: Trading symbol.
            timeframe: Candle interval ('1m', '5m', '15m', '1h', '1d').
            count: Number of candles to fetch.

        Returns:
            List of dicts with keys: timestamp, open, high, low, close, volume.
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the feed is currently connected.

        Returns:
            True if connected, False otherwise.
        """

    @abstractmethod
    def get_name(self) -> str:
        """Return the feed's name identifier."""

    async def get_latest_price(self, symbol: str) -> float:
        """Get the latest price (LTP) for a symbol."""
        return await self.get_ltp(symbol)

