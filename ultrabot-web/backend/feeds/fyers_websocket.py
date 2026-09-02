"""Real-time market data feed via Fyers' official Data WebSocket.

Built on `fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket` — Fyers'
own maintained client — rather than a hand-rolled WebSocket/binary
protocol implementation, since the wire format for the v3 data socket is
not fully documented publicly and guessing at it risks silent, hard-to-
detect data corruption in something feeding real trading decisions.

`FyersDataSocket` runs its own connection/heartbeat/reconnect loop in a
background thread (it is not asyncio-native). This class bridges that
thread's `on_message` callback into a plain dict that async code can read
from (safe under the GIL for simple dict item assignment/lookup — the
same pattern used by AngelWebSocketFeed for its own thread/coroutine mix).
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fyers_apiv3.FyersWebsocket import data_ws

from feeds.base import BaseFeed

logger = logging.getLogger(__name__)

_RECONNECT_RETRY = 5


class FyersWebSocketFeed(BaseFeed):
    """Real-time LTP feed via Fyers' Data WebSocket (SymbolUpdate channel).

    access_token must be in Fyers' combined "app_id:access_token" format,
    since that's what the SDK expects.
    """

    def __init__(self, app_id: str, access_token: str):
        self.app_id = app_id
        self.access_token = access_token
        self._combined_token = f"{app_id}:{access_token}" if app_id and access_token else access_token
        self._socket: Optional[data_ws.FyersDataSocket] = None
        self._connected = False
        self._ltp_data: Dict[str, float] = {}
        self._last_update: Dict[str, float] = {}
        self._subscribed_symbols: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ────────────────────────────────────────────────────────────
    # BaseFeed interface
    # ────────────────────────────────────────────────────────────

    async def connect(self) -> Dict[str, Any]:
        if not self.app_id or not self.access_token:
            return {"success": False, "message": "Fyers app_id/access_token not configured"}

        try:
            self._loop = asyncio.get_running_loop()
            self._socket = data_ws.FyersDataSocket(
                access_token=self._combined_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                reconnect_retry=_RECONNECT_RETRY,
                on_connect=self._on_open,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_message,
            )
            # The SDK's connect()/keep_running() are synchronous and spin up
            # their own background thread — run the blocking connect() call
            # off the event loop so FastAPI's loop isn't blocked.
            await asyncio.to_thread(self._socket.connect)
            self._connected = True
            return {"success": True, "message": "Connected to Fyers Data WebSocket"}
        except Exception as exc:
            logger.error("Failed to connect Fyers WebSocket: %s", exc, exc_info=True)
            self._connected = False
            return {"success": False, "message": str(exc)}

    async def disconnect(self) -> Dict[str, Any]:
        try:
            if self._socket is not None:
                await asyncio.to_thread(self._socket.close_connection)
            self._connected = False
            return {"success": True, "message": "Disconnected from Fyers WebSocket"}
        except Exception as exc:
            logger.warning("Error disconnecting Fyers WebSocket: %s", exc)
            self._connected = False
            return {"success": False, "message": str(exc)}

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._connected or self._socket is None:
            return {"success": False, "subscribed": 0, "message": "Not connected"}
        try:
            fyers_symbols = [self._to_fyers_symbol(s) for s in symbols]
            await asyncio.to_thread(
                self._socket.subscribe, symbols=fyers_symbols, data_type="SymbolUpdate"
            )
            self._subscribed_symbols.update(s.upper() for s in symbols)
            logger.info("Subscribed to %d symbols on Fyers WebSocket", len(symbols))
            return {"success": True, "subscribed": len(symbols)}
        except Exception as exc:
            logger.error("Fyers WebSocket subscribe failed: %s", exc)
            return {"success": False, "subscribed": 0, "message": str(exc)}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._connected or self._socket is None:
            return {"success": False, "unsubscribed": 0, "message": "Not connected"}
        try:
            fyers_symbols = [self._to_fyers_symbol(s) for s in symbols]
            await asyncio.to_thread(
                self._socket.unsubscribe, symbols=fyers_symbols, data_type="SymbolUpdate"
            )
            for s in symbols:
                self._subscribed_symbols.discard(s.upper())
            return {"success": True, "unsubscribed": len(symbols)}
        except Exception as exc:
            logger.error("Fyers WebSocket unsubscribe failed: %s", exc)
            return {"success": False, "unsubscribed": 0, "message": str(exc)}

    async def get_ltp(self, symbol: str) -> float:
        return self._ltp_data.get(symbol.upper(), 0.0)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        # The data websocket only streams live ticks, not historical
        # candles. Historical OHLCV goes through FyersBroker.get_candles()
        # (REST /data/history via the SDK), not this feed.
        return []

    def is_connected(self) -> bool:
        return self._connected and self._socket is not None

    def get_name(self) -> str:
        return "fyers_websocket"

    def get_all_ltps(self) -> Dict[str, float]:
        """Return a copy of all current LTP data (parity with AngelWebSocketFeed)."""
        return dict(self._ltp_data)

    def staleness_seconds(self, symbol: str) -> float:
        """Seconds since the last tick for a symbol; large value = likely stale/disconnected."""
        last = self._last_update.get(symbol.upper())
        if last is None:
            return float("inf")
        return time.time() - last

    # ────────────────────────────────────────────────────────────
    # Internal: symbol formatting + SDK thread callbacks
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_fyers_symbol(symbol: str, exchange: str = "NSE") -> str:
        if ":" in symbol:
            return symbol
        return f"{exchange}:{symbol.upper()}-EQ"

    @staticmethod
    def _from_fyers_symbol(fyers_symbol: str) -> str:
        # "NSE:RELIANCE-EQ" -> "RELIANCE"
        try:
            tail = fyers_symbol.split(":")[-1]
            return tail.replace("-EQ", "").replace("-INDEX", "").upper()
        except Exception:
            return fyers_symbol.upper()

    def _on_open(self) -> None:
        logger.info("Fyers WebSocket connection opened")
        self._connected = True

    def _on_close(self, message: Any) -> None:
        logger.warning("Fyers WebSocket closed: %s", message)
        self._connected = False

    def _on_error(self, message: Any) -> None:
        logger.error("Fyers WebSocket error: %s", message)

    def _on_message(self, message: Dict[str, Any]) -> None:
        """Runs on the SDK's background thread — keep this fast and
        exception-safe; never block or raise here."""
        try:
            if not isinstance(message, dict):
                return
            fyers_symbol = message.get("symbol")
            ltp = message.get("ltp")
            if fyers_symbol and ltp:
                symbol = self._from_fyers_symbol(fyers_symbol)
                self._ltp_data[symbol] = float(ltp)
                self._last_update[symbol] = time.time()
        except Exception as exc:
            logger.debug("Failed to parse Fyers WS message: %s", exc)
