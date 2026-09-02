import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import websockets

from feeds.base import BaseFeed
from errors.error_types import FeedError, WebSocketDisconnectedError

logger = logging.getLogger(__name__)

# Shoonya Noren WebSocket feed URL
_WS_URL = "wss://api.shoonya.com/NorenWSTP/"
_HEARTBEAT_INTERVAL = 30  # seconds
_RECONNECT_BASE_DELAY = 1.0
_RECONNECT_MAX_DELAY = 60.0
_MAX_RECONNECT_ATTEMPTS = 10


class ShoonyaWebSocketFeed(BaseFeed):
    """Real-time market data feed via Shoonya Noren WebSocket.

    Connects to Shoonya's WebSocket for live LTP updates.
    Supports subscription/unsubscription and automatic reconnection.
    """

    def __init__(
        self,
        user_id: str = "",
        password: str = "",
        vendor_code: str = "",
        app_key: str = "",
        totp_secret: str = "",
    ):
        self.user_id = user_id
        self.password = password
        self.vendor_code = vendor_code
        self.app_key = app_key
        self.totp_secret = totp_secret
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._ltp_data: Dict[str, float] = {}
        self._subscribed_tokens: set = set()
        self._symbol_to_token: Dict[str, str] = {}
        self._token_to_symbol: Dict[str, str] = {}
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_attempts = 0
        self._session_token: str = ""

    async def connect(self) -> Dict[str, Any]:
        import hashlib
        try:
            headers = {
                "Content-Type": "application/json",
            }
            self._ws = await websockets.connect(_WS_URL, extra_headers=headers)

            factor2 = ""
            if self.totp_secret and self.totp_secret.strip():
                try:
                    import pyotp
                    factor2 = pyotp.TOTP(self.totp_secret.replace(" ", "").upper()).now()
                except Exception as e:
                    logger.warning("Failed generating TOTP for Shoonya WS: %s", e)
            if not factor2:
                factor2 = hashlib.sha256(self.password.encode()).hexdigest()

            # Shoonya requires an initial auth message
            auth_msg = json.dumps({
                "t": "c",
                "uid": self.user_id,
                "actid": self.user_id,
                "pwd": hashlib.sha256(self.password.encode()).hexdigest(),
                "factor2": factor2,
                "appkey": self.app_key,
                "v": self.vendor_code,
                "devid": "ULTRABOT",
            })
            await self._ws.send(auth_msg)

            # Wait for auth response
            response = await asyncio.wait_for(self._ws.recv(), timeout=10)
            data = json.loads(response)

            if data.get("s") == "OK":
                self._session_token = data.get("susertoken", "")
                self._connected = True
                self._running = True
                self._reconnect_attempts = 0

                self._receive_task = asyncio.create_task(self._receive_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                # Re-subscribe
                if self._subscribed_tokens:
                    await self._subscribe_tokens(list(self._subscribed_tokens))

                logger.info("Shoonya WebSocket connected")
                return {"success": True, "message": "Connected to Shoonya WebSocket"}
            else:
                errmsg = data.get("emsg", "Authentication failed")
                logger.error("Shoonya WS auth failed: %s", errmsg)
                await self._ws.close()
                self._ws = None
                return {"success": False, "message": errmsg}
        except Exception as e:
            logger.error("Failed to connect Shoonya WebSocket: %s", e)
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
        return {"success": True, "message": "Disconnected from Shoonya WebSocket"}

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "subscribed": 0, "message": "Not connected"}
        tokens = []
        for sym in symbols:
            token = self._symbol_to_token.get(sym.upper())
            if token:
                tokens.append(token)
        if not tokens:
            return {"success": True, "subscribed": 0, "message": "No tokens to subscribe (register tokens first)"}
        result = await self._subscribe_tokens(tokens)
        return result

    async def _subscribe_tokens(self, tokens: List[str]) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "subscribed": 0, "message": "Not connected"}
        try:
            sub_msg = json.dumps({
                "t": "t",
                "k": tokens,
            })
            await self._ws.send(sub_msg)
            self._subscribed_tokens.update(tokens)
            return {"success": True, "subscribed": len(tokens)}
        except Exception as e:
            logger.error("Subscribe failed: %s", e)
            return {"success": False, "subscribed": 0, "message": str(e)}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "unsubscribed": 0, "message": "Not connected"}
        tokens = []
        for sym in symbols:
            token = self._symbol_to_token.get(sym.upper())
            if token:
                tokens.append(token)
        try:
            unsub_msg = json.dumps({
                "t": "u",
                "k": tokens,
            })
            await self._ws.send(unsub_msg)
            for token in tokens:
                self._subscribed_tokens.discard(token)
            return {"success": True, "unsubscribed": len(tokens)}
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
        # WebSocket feeds typically don't store candles
        return []

    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def get_name(self) -> str:
        return "shoonya_websocket"

    def register_symbol_token(self, symbol: str, exchange: str, token: str) -> None:
        """Register a symbol-to-token mapping.

        Args:
            symbol: NSE symbol (e.g. 'RELIANCE').
            exchange: Exchange code (e.g. 'NSE', 'NFO').
            token: Shoonya instrument token.
        """
        key = symbol.upper()
        token_key = f"{exchange}|{token}"
        self._symbol_to_token[key] = token_key
        self._token_to_symbol[token_key] = key

    async def _receive_loop(self) -> None:
        while self._running and self._ws is not None:
            try:
                message = await asyncio.wait_for(self._ws.recv(), timeout=60)
                await self._on_message(message)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                logger.warning("Shoonya WebSocket closed")
                self._connected = False
                await self._try_reconnect()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WS receive error: %s", e)
                self._connected = False
                await self._try_reconnect()
                break

    async def _on_message(self, message: str) -> None:
        try:
            data = json.loads(message)
            msg_type = data.get("t", "")

            if msg_type == "tf":  # Touchline feed (LTP)
                tk = data.get("tk", "")
                symbol = self._token_to_symbol.get(tk)
                lp = float(data.get("lp", 0) or 0)
                if symbol and lp > 0:
                    self._ltp_data[symbol] = lp

            elif msg_type == "dk":  # Depth data
                tk = data.get("tk", "")
                symbol = self._token_to_symbol.get(tk)
                lp = float(data.get("lp", 0) or 0)
                if symbol and lp > 0:
                    self._ltp_data[symbol] = lp

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug("Failed to parse Shoonya WS message: %s", e)

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            if self._ws is not None and self._connected:
                try:
                    await self._ws.send(json.dumps({"t": "h"}))
                except Exception:
                    self._connected = False
                    break

    async def _try_reconnect(self) -> None:
        while self._running:
            if self._reconnect_attempts < _MAX_RECONNECT_ATTEMPTS:
                delay = min(_RECONNECT_BASE_DELAY * (2 ** self._reconnect_attempts), _RECONNECT_MAX_DELAY)
                self._reconnect_attempts += 1
                logger.info("Reconnecting Shoonya WS in %.1fs (attempt %d/%d)",
                             delay, self._reconnect_attempts, _MAX_RECONNECT_ATTEMPTS)
                await asyncio.sleep(delay)
                result = await self.connect()
                if result["success"]:
                    self._reconnect_attempts = 0
                    logger.info("Shoonya WebSocket reconnected")
                    return
            else:
                logger.error("Shoonya WS max reconnect attempts (%d) reached. Cooling down for 60s before retrying...", _MAX_RECONNECT_ATTEMPTS)
                await asyncio.sleep(60.0)
                self._reconnect_attempts = 0

    def get_all_ltps(self) -> Dict[str, float]:
        return dict(self._ltp_data)
