import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import websockets

from feeds.base import BaseFeed
from errors.error_types import FeedError, WebSocketDisconnectedError

logger = logging.getLogger(__name__)

# Angel One WebSocket feed URL
_WS_URL = "wss://smartapi.angelone.in/socket.io/socket.io/?EIO=4&transport=websocket"
_HEARTBEAT_INTERVAL = 30  # seconds
_RECONNECT_BASE_DELAY = 1.0  # seconds
_RECONNECT_MAX_DELAY = 60.0
_MAX_RECONNECT_ATTEMPTS = 10


class AngelWebSocketFeed(BaseFeed):
    """Real-time market data feed via Angel One WebSocket.

    Connects to Angel One's SmartAPI WebSocket for live LTP updates.
    Supports subscription/unsubscription and automatic reconnection.
    """

    def __init__(
        self,
        jwt_token: str = "",
        client_code: str = "",
        feed_token: str = "",
    ):
        self.jwt_token = jwt_token
        self.client_code = client_code
        self.feed_token = feed_token
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._ltp_data: Dict[str, float] = {}
        self._subscribed_symbols: set = set()
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_attempts = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> Dict[str, Any]:
        try:
            headers = {
                "Authorization": f"Bearer {self.jwt_token}",
                "X-ClientCode": self.client_code,
                "X-FeedToken": self.feed_token,
            }
            self._ws = await websockets.connect(_WS_URL, extra_headers=headers)
            self._connected = True
            self._running = True
            self._reconnect_attempts = 0

            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Re-subscribe to previous symbols
            if self._subscribed_symbols:
                await self.subscribe(list(self._subscribed_symbols))

            logger.info("Angel WebSocket connected")
            return {"success": True, "message": "Connected to Angel One WebSocket"}
        except Exception as e:
            logger.error("Failed to connect Angel WebSocket: %s", e)
            self._connected = False
            return {"success": False, "message": str(e)}

    async def disconnect(self) -> Dict[str, Any]:
        self._running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._connected = False
        return {"success": True, "message": "Disconnected from Angel WebSocket"}

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "subscribed": 0, "message": "Not connected"}
        try:
            # Angel One SmartAPI WebSocket v2 standard subscription format
            token_list = [{"exchangeType": 1, "tokens": [str(s)]} for s in symbols]
            subscribe_msg = json.dumps({
                "correlationID": "ultrabot_sub",
                "action": 1,
                "params": {
                    "mode": 1,  # LTP mode
                    "tokenList": token_list,
                },
            })
            await self._ws.send(subscribe_msg)
            self._subscribed_symbols.update(s.upper() for s in symbols)
            logger.info("Subscribed to %d symbols on Angel WebSocket", len(symbols))
            return {"success": True, "subscribed": len(symbols)}
        except Exception as e:
            logger.error("Subscribe failed: %s", e)
            return {"success": False, "subscribed": 0, "message": str(e)}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "unsubscribed": 0, "message": "Not connected"}
        try:
            token_list = [{"exchangeType": 1, "tokens": [str(s)]} for s in symbols]
            unsubscribe_msg = json.dumps({
                "correlationID": "ultrabot_unsub",
                "action": 0,
                "params": {
                    "mode": 1,
                    "tokenList": token_list,
                },
            })
            await self._ws.send(unsubscribe_msg)
            for s in symbols:
                self._subscribed_symbols.discard(s.upper())
            return {"success": True, "unsubscribed": len(symbols)}
        except Exception as e:
            logger.error("Unsubscribe failed: %s", e)
            return {"success": False, "unsubscribed": 0, "message": str(e)}

    async def get_ltp(self, symbol: str) -> float:
        return self._ltp_data.get(symbol.upper(), 0.0)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        # WebSocket feeds typically don't store candles.
        # Delegate to a historical source if available.
        return []

    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def get_name(self) -> str:
        return "angel_websocket"

    async def _receive_loop(self) -> None:
        """Main loop for receiving WebSocket messages."""
        while self._running and self._ws is not None:
            try:
                message = await asyncio.wait_for(self._ws.recv(), timeout=60)
                await self._on_message(message)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed as e:
                logger.warning("Angel WebSocket closed: %s", e)
                self._connected = False
                await self._try_reconnect()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WebSocket receive error: %s", e)
                self._connected = False
                await self._try_reconnect()
                break

    async def _on_message(self, message: str) -> None:
        """Parse incoming WebSocket messages and update LTP data."""
        try:
            # Angel One sends JSON with LTP data
            if message.startswith("{\"ltp\""):
                data = json.loads(message)
                symbol = data.get("symbol", "").upper()
                ltp = float(data.get("ltp", 0))
                if symbol and ltp > 0:
                    self._ltp_data[symbol] = ltp
            elif message.startswith("42["):
                # Socket.IO format
                inner = message[2:]
                data = json.loads(inner)
                if isinstance(data, list) and len(data) > 0:
                    payloads = []
                    for elem in data:
                        if isinstance(elem, dict):
                            payloads.append(elem)
                        elif isinstance(elem, list):
                            payloads.extend([x for x in elem if isinstance(x, dict)])
                    for item in payloads:
                        symbol = str(item.get("t", item.get("symbol", ""))).upper()
                        ltp = float(item.get("lp", item.get("ltp", 0)) or 0)
                        if symbol and ltp > 0:
                            self._ltp_data[symbol] = ltp
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug("Failed to parse WS message: %s", e)

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat to keep connection alive."""
        while self._running:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            if self._ws is not None and self._connected:
                try:
                    await self._ws.send("2")  # Socket.IO ping
                except Exception as e:
                    logger.warning("Heartbeat failed: %s", e)
                    self._connected = False
                    break

    async def _try_reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while self._running and self._reconnect_attempts < _MAX_RECONNECT_ATTEMPTS:
            delay = min(_RECONNECT_BASE_DELAY * (2 ** self._reconnect_attempts), _RECONNECT_MAX_DELAY)
            self._reconnect_attempts += 1
            logger.info("Reconnecting Angel WebSocket in %.1fs (attempt %d/%d)",
                         delay, self._reconnect_attempts, _MAX_RECONNECT_ATTEMPTS)
            await asyncio.sleep(delay)
            result = await self.connect()
            if result["success"]:
                logger.info("Angel WebSocket reconnected successfully")
                return
        logger.error("Failed to reconnect Angel WebSocket after %d attempts", _MAX_RECONNECT_ATTEMPTS)

    def get_all_ltps(self) -> Dict[str, float]:
        """Return a copy of all current LTP data."""
        return dict(self._ltp_data)
