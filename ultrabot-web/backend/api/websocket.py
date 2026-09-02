import asyncio
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Valid channel names that clients can subscribe to
VALID_CHANNELS = {
    "live_price_updates",
    "opportunity",
    "new_opportunity",
    "trade_fill",
    "trade_exit",
    "partial_booking",
    "risk_event",
    "error_alert",
    "engine_status_change",
    "regime_change",
    "scan_complete",
    "scan_telemetry",
    "telemetry",
}

# All channel names except live_price_updates (which is opt-in)
DEFAULT_CHANNELS = VALID_CHANNELS - {"live_price_updates"}

router = APIRouter()


def _json_safe_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json.dumps.

    Handles Pydantic models (e.g. BookingLevels), datetimes, Decimals and sets
    so that engine broadcasts never fail with "not JSON serializable".
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


class WebSocketManager:
    """Manages WebSocket connections with per-connection queues for non-blocking broadcasts.

    Each client connection gets its own asyncio.Queue. When broadcast() is called,
    the message is pushed to every subscribed client's queue. A separate reader task
    per connection drains the queue and sends messages to the client.
    """

    def __init__(self) -> None:
        # Map: WebSocket -> set of subscribed channel names
        self._subscriptions: Dict[WebSocket, Set[str]] = {}
        # Map: WebSocket -> asyncio.Queue for outbound messages
        self._queues: Dict[WebSocket, asyncio.Queue] = {}
        # Map: WebSocket -> reader task
        self._reader_tasks: Dict[WebSocket, asyncio.Task] = {}
        # Channel -> set of subscribed WebSockets (for fast lookup)
        self._channel_subscribers: Dict[str, Set[WebSocket]] = {}
        self._dropped_messages = 0
        # Lock for thread-safe modifications
        self._lock = asyncio.Lock()

    @property
    def active_connections(self) -> int:
        """Number of currently connected clients."""
        return len(self._subscriptions)

    async def connect(self, ws: WebSocket, default_channels: Optional[Set[str]] = None) -> None:
        """Accept a WebSocket connection and set up its queue and reader task.

        Args:
            ws: The WebSocket connection.
            default_channels: Initial channels to subscribe to. Defaults to all except live_price_updates.
        """
        await ws.accept()
        channels = default_channels or DEFAULT_CHANNELS

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscriptions[ws] = set(channels)
        self._queues[ws] = queue

        # Register in channel subscriber map
        for ch in channels:
            self._channel_subscribers.setdefault(ch, set()).add(ws)

        # Start reader task that drains queue and sends to client
        task = asyncio.create_task(self._reader_loop(ws, queue))
        self._reader_tasks[ws] = task

        logger.info("WebSocket connected. Subscribed to %d channels", len(channels))

    async def disconnect(self, ws: WebSocket) -> None:
        """Clean up a WebSocket connection."""
        async with self._lock:
            # Remove from channel subscriber maps
            channels = self._subscriptions.pop(ws, set())
            for ch in channels:
                subs = self._channel_subscribers.get(ch)
                if subs is not None:
                    subs.discard(ws)
                    if not subs:
                        self._channel_subscribers.pop(ch, None)

            # Cancel reader task
            task = self._reader_tasks.pop(ws, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Remove queue
            self._queues.pop(ws, None)

            logger.info("WebSocket disconnected. Remaining connections: %d", len(self._subscriptions))

    async def subscribe(self, ws: WebSocket, channels: List[str]) -> None:
        """Subscribe a client to additional channels."""
        async with self._lock:
            if ws not in self._subscriptions:
                return

            for ch in channels:
                if ch not in VALID_CHANNELS:
                    continue
                self._subscriptions[ws].add(ch)
                self._channel_subscribers.setdefault(ch, set()).add(ws)

    async def unsubscribe(self, ws: WebSocket, channels: List[str]) -> None:
        """Unsubscribe a client from channels."""
        async with self._lock:
            if ws not in self._subscriptions:
                return

            for ch in channels:
                self._subscriptions[ws].discard(ch)
                subs = self._channel_subscribers.get(ch)
                if subs is not None:
                    subs.discard(ws)
                    if not subs:
                        self._channel_subscribers.pop(ch, None)

    async def broadcast(self, channel: str, data: Dict[str, Any]) -> None:
        """Send data to all clients subscribed to a channel.

        This is the method called by the engine via engine.ws_manager.broadcast().
        Automatically maps engine channel names to WebSocket channel names
        using the data's 'type' field.
        """
        # Map engine channel to WS channel names
        ws_channels = self._resolve_channels(channel, data)

        try:
            message = json.dumps(
                {"channel": channel, "data": data, "ts": datetime.now(IST).isoformat()},
                default=_json_safe_default,
            )
        except (TypeError, ValueError) as encode_err:
            logger.error("WebSocket broadcast encode failed on channel '%s': %s", channel, encode_err)
            return

        all_disconnected: Set[WebSocket] = set()
        for ch in ws_channels:
            subscribers = self._channel_subscribers.get(ch)
            if not subscribers:
                continue
            for ws in list(subscribers):
                queue = self._queues.get(ws)
                if queue is None:
                    all_disconnected.add(ws)
                    continue
                try:
                    # Non-blocking put; if full, drop oldest stale message to keep stream live
                    if queue.full():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        self._dropped_messages += 1
                        logger.warning("Queue full for WebSocket client, dropped oldest message on channel '%s'", ch)
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    self._dropped_messages += 1

        # Clean up any disconnected websockets
        for ws in all_disconnected:
            await self.disconnect(ws)

    def _resolve_channels(self, channel: str, data: Dict[str, Any]) -> List[str]:
        """Resolve engine broadcast channel + data type to list of WS channel names."""
        event_type = data.get("type", "")
        channels = []

        # Route based on data type for precise delivery
        if "trade_exit" in event_type or "exit" in event_type:
            channels.append("trade_exit")
        if "trade" in event_type and ("fill" in event_type or "entry" in event_type):
            channels.append("trade_fill")
        if "opportunity" in event_type or channel == "opportunity":
            channels.append("new_opportunity")
            channels.append("opportunity")
        if "risk" in event_type:
            channels.append("risk_event")
        if "error" in event_type:
            channels.append("error_alert")
        if "regime" in event_type:
            channels.append("regime_change")
        if "scan_complete" in event_type or "scan" in event_type:
            channels.append("scan_complete")
        if "telemetry" in event_type or "scan_telemetry" in event_type or channel in ("telemetry", "scan_telemetry"):
            channels.append("scan_telemetry")
            channels.append("telemetry")
        if "engine_state_change" in event_type or "engine" in event_type:
            channels.append("engine_status_change")
        if "partial" in event_type:
            channels.append("partial_booking")

        # If no specific type matched, use the channel mapping
        if not channels:
            mapped = _CHANNEL_MAP.get(channel)
            if mapped:
                channels.append(mapped)
            else:
                channels.append(channel)

        return channels

    async def send_personal(self, ws: WebSocket, data: Dict[str, Any]) -> None:
        """Send a message directly to a specific client."""
        queue = self._queues.get(ws)
        if queue is None:
            return
        try:
            message = json.dumps(
                {"channel": "personal", "data": data, "ts": datetime.now(IST).isoformat()},
                default=_json_safe_default,
            )
        except (TypeError, ValueError) as encode_err:
            logger.error("WebSocket personal encode failed: %s", encode_err)
            return
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.debug("Queue full, dropping personal message")

    async def _reader_loop(self, ws: WebSocket, queue: asyncio.Queue) -> None:
        """Drain the outbound queue and send messages to the WebSocket client."""
        try:
            while True:
                message = await queue.get()
                try:
                    await ws.send_text(message)
                except Exception as send_exc:
                    logger.debug("Failed to send to WebSocket: %s", send_exc)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as loop_exc:
            logger.debug("WebSocket reader loop error: %s", loop_exc)
        finally:
            # If we exit the loop, the connection is likely dead
            if ws in self._subscriptions:
                await self.disconnect(ws)


# Global singleton
ws_manager = WebSocketManager()


# Channel name mapping: engine broadcast channel -> WS channel
_CHANNEL_MAP = {
    "engine": "engine_status_change",
    "trade": "trade_fill",
    "opportunity": "new_opportunity",
    "risk": "risk_event",
    "error": "error_alert",
    "regime": "regime_change",
    "scan": "scan_complete",
    "partial_booking": "partial_booking",
    "telemetry": "scan_telemetry",
    "scan_telemetry": "scan_telemetry",
}


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: Optional[str] = Query(None),
) -> None:
    """WebSocket endpoint for real-time updates.

    Query params:
        token: JWT token for authentication (optional for now, enforced by middleware).

    Protocol:
        Server -> Client:
            {"channel": "<channel_name>", "data": {...}, "ts": "<timestamp>"}

        Client -> Server:
            {"action": "subscribe", "channels": ["channel1", "channel2"]}
            {"action": "unsubscribe", "channels": ["channel1"]}
            {"action": "ping"}
    """
    # Mandatory Token validation.
    # NOTE: the WebSocket must be ACCEPTED before closing with a policy code —
    # closing an unaccepted socket surfaces as HTTP 403 / close code 1006 in
    # browsers, which bypasses the client's 1008 auth-error handling and
    # causes an infinite reconnect loop.
    async def _reject(reason: str) -> None:
        try:
            await ws.accept()
        except Exception:
            pass
        await ws.close(code=1008, reason=reason)

    if not token:
        logger.warning("WebSocket connection rejected: missing authentication token")
        await _reject("Authentication token required")
        return

    try:
        from api.routes.auth import is_token_revoked
        from jose import jwt, JWTError
        from config.settings import settings

        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
            username = payload.get("sub")
            if not username:
                await _reject("Invalid token payload")
                return
            if is_token_revoked(token):
                await _reject("Token revoked")
                return
        except JWTError:
            await _reject("Invalid token")
            return
    except Exception as exc:
        logger.warning("WebSocket token verification error: %s", exc)
        await _reject("Authentication failed")
        return

    # Connect with default channels
    await ws_manager.connect(ws)

    # Send initial connection confirmation
    await ws_manager.send_personal(ws, {
        "type": "connected",
        "message": "UltraBot WebSocket connected",
        "available_channels": list(VALID_CHANNELS),
        "subscribed_channels": list(ws_manager._subscriptions.get(ws, set())),
    })

    try:
        while True:
            # Receive messages from client (subscribe/unsubscribe/ping)
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws_manager.send_personal(ws, {
                    "type": "error",
                    "message": "Invalid JSON",
                })
                continue

            action = msg.get("action", "")

            if action == "subscribe":
                channels = msg.get("channels", [])
                if isinstance(channels, str):
                    channels = [channels]
                await ws_manager.subscribe(ws, channels)
                await ws_manager.send_personal(ws, {
                    "type": "subscribed",
                    "channels": channels,
                })

            elif action == "unsubscribe":
                channels = msg.get("channels", [])
                if isinstance(channels, str):
                    channels = [channels]
                await ws_manager.unsubscribe(ws, channels)
                await ws_manager.send_personal(ws, {
                    "type": "unsubscribed",
                    "channels": channels,
                })

            elif action == "ping":
                await ws_manager.send_personal(ws, {
                    "type": "pong",
                    "ts": datetime.now(IST).isoformat(),
                })

            else:
                await ws_manager.send_personal(ws, {
                    "type": "error",
                    "message": f"Unknown action: {action}",
                })

    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket")
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        await ws_manager.disconnect(ws)
