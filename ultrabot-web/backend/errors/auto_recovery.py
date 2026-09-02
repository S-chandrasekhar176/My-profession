"""AutoRecovery class with strategies for common errors.
Uses exponential backoff where appropriate.
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from errors.error_types import (
    UltraBotError,
    TokenExpiredError,
    WebSocketDisconnectedError,
    StaleDataError,
    PriceMismatchError,
    BrokerError,
    FeedError,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Backoff sequence: 5s, 10s, 20s, 40s, 80s
BACKOFF_DELAYS = [5, 10, 20, 40, 80]
MAX_RECONNECT_ATTEMPTS = 5


class AutoRecovery:
    """Attempts automatic recovery for specific error types."""

    def __init__(self):
        self._reconnect_attempts: Dict[str, int] = {}
        self._last_attempt: Dict[str, str] = {}
        self._lock = threading.Lock()

    def reset_state(self, key: Optional[str] = None) -> None:
        """Reset reconnect attempts and last attempt timestamps."""
        with self._lock:
            if key:
                self._reconnect_attempts.pop(key, None)
                self._last_attempt.pop(key, None)
            else:
                self._reconnect_attempts.clear()
                self._last_attempt.clear()

    # ────────────────────────────────────────
    # Token expired recovery
    # ────────────────────────────────────────

    async def recover_token_expired(
        self,
        error: TokenExpiredError,
        refresh_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Attempt to recover an expired broker token.

        Args:
            error: The TokenExpiredError that was raised.
            refresh_fn: Async callable that takes (broker_name) -> bool.
                        Should attempt to refresh the token and return True on success.

        Returns:
            dict with 'success' (bool), 'message' (str), 'action' (str).
        """
        broker = error.context.get("broker", "unknown")
        logger.warning(f"Auto-recovery: attempting token refresh for broker={broker}")

        if refresh_fn is None:
            return {
                "success": False,
                "message": "No refresh function provided. Manual intervention required.",
                "action": "prompt_relogin",
            }

        try:
            refreshed = await refresh_fn(broker)
            if refreshed:
                logger.info(f"Auto-recovery: token refreshed successfully for broker={broker}")
                return {
                    "success": True,
                    "message": f"Token refreshed successfully for {broker}",
                    "action": "token_refreshed",
                }
            else:
                logger.error(f"Auto-recovery: token refresh failed for broker={broker}")
                return {
                    "success": False,
                    "message": f"Token refresh returned False for {broker}",
                    "action": "prompt_relogin",
                }
        except Exception as e:
            logger.error(f"Auto-recovery: token refresh threw exception for broker={broker}: {e}")
            return {
                "success": False,
                "message": f"Token refresh exception: {str(e)}",
                "action": "prompt_relogin",
            }

    # ────────────────────────────────────────
    # WebSocket reconnection with exponential backoff
    # ────────────────────────────────────────

    async def recover_websocket_disconnected(
        self,
        error: WebSocketDisconnectedError,
        connect_fn: Callable,
    ) -> Dict[str, Any]:
        """Reconnect WebSocket with exponential backoff.

        Args:
            error: The WebSocketDisconnectedError.
            connect_fn: Async callable that takes (feed_url) -> bool.
                        Should attempt to (re)connect and return True on success.

        Returns:
            dict with 'success', 'message', 'action', 'attempts'.
        """
        feed_url = error.context.get("feed_url", "unknown")
        key = f"ws:{feed_url}"

        with self._lock:
            attempts = self._reconnect_attempts.get(key, 0)
            if attempts >= MAX_RECONNECT_ATTEMPTS:
                # Reset counter for future attempts
                self._reconnect_attempts[key] = 0
                logger.error(f"Auto-recovery: max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) reached for {feed_url}")
                return {
                    "success": False,
                    "message": f"Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) exhausted for {feed_url}",
                    "action": "manual_reconnect_required",
                    "attempts": attempts,
                }

            delay = BACKOFF_DELAYS[min(attempts, len(BACKOFF_DELAYS) - 1)]
            self._reconnect_attempts[key] = attempts + 1
            self._last_attempt[key] = datetime.now(IST).isoformat()

        logger.warning(
            f"Auto-recovery: WebSocket reconnect attempt {attempts + 1}/{MAX_RECONNECT_ATTEMPTS} "
            f"for {feed_url} after {delay}s"
        )

        await asyncio.sleep(delay)

        try:
            connected = await connect_fn(feed_url)
            if connected:
                with self._lock:
                    self._reconnect_attempts[key] = 0
                logger.info(f"Auto-recovery: WebSocket reconnected successfully for {feed_url}")
                return {
                    "success": True,
                    "message": f"WebSocket reconnected after {attempts + 1} attempts (waited {delay}s)",
                    "action": "reconnected",
                    "attempts": attempts + 1,
                }
            else:
                logger.warning(f"Auto-recovery: WebSocket reconnect attempt {attempts + 1} failed for {feed_url}")
                return {
                    "success": False,
                    "message": f"Reconnect attempt {attempts + 1} failed for {feed_url}. Will retry.",
                    "action": "retry",
                    "attempts": attempts + 1,
                    "next_delay": BACKOFF_DELAYS[min(attempts + 1, len(BACKOFF_DELAYS) - 1)],
                }
        except Exception as e:
            logger.error(f"Auto-recovery: WebSocket reconnect threw exception: {e}")
            return {
                "success": False,
                "message": f"Reconnect attempt {attempts + 1} exception: {str(e)}",
                "action": "retry",
                "attempts": attempts + 1,
                "next_delay": BACKOFF_DELAYS[min(attempts + 1, len(BACKOFF_DELAYS) - 1)],
            }

    # ────────────────────────────────────────
    # Stale data recovery
    # ────────────────────────────────────────

    async def recover_stale_data(
        self,
        error: StaleDataError,
        fetch_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Attempt to recover stale data by re-fetching.

        Args:
            error: The StaleDataError.
            fetch_fn: Async callable that takes (symbol) -> dict or list.
                        Should fetch fresh data and return it.

        Returns:
            dict with 'success', 'message', 'action', 'data'.
        """
        symbol = error.context.get("symbol", "unknown")
        seconds_stale = error.context.get("seconds_stale", 0)
        logger.warning(f"Auto-recovery: attempting data refresh for {symbol} (stale {seconds_stale}s)")

        if fetch_fn is None:
            return {
                "success": False,
                "message": f"No fetch function provided for {symbol}. Data remains stale.",
                "action": "skip_signal",
            }

        try:
            data = await fetch_fn(symbol)
            if data is not None:
                logger.info(f"Auto-recovery: fresh data fetched for {symbol}")
                return {
                    "success": True,
                    "message": f"Fresh data retrieved for {symbol}",
                    "action": "data_refreshed",
                    "data": data,
                }
            else:
                return {
                    "success": False,
                    "message": f"Fetch returned None for {symbol}",
                    "action": "skip_signal",
                }
        except Exception as e:
            logger.error(f"Auto-recovery: data fetch exception for {symbol}: {e}")
            return {
                "success": False,
                "message": f"Data fetch exception: {str(e)}",
                "action": "skip_signal",
            }

    # ────────────────────────────────────────
    # Price mismatch recovery
    # ────────────────────────────────────────

    async def recover_price_mismatch(
        self,
        error: PriceMismatchError,
        threshold_pct: float = 0.5,
    ) -> Dict[str, Any]:
        """Handle price mismatch – typically just skip the signal.

        If mismatch is within an acceptable tolerance, allow the trade
        with updated prices. Otherwise, skip the signal.

        Args:
            error: The PriceMismatchError.
            threshold_pct: If mismatch is below this, the signal can still be used.

        Returns:
            dict with 'success', 'message', 'action'.
        """
        mismatch_pct = error.context.get("mismatch_pct", 999)
        symbol = error.context.get("symbol", "unknown")
        market_price = error.context.get("market_price", 0)

        logger.warning(
            f"Auto-recovery: price mismatch for {symbol}: {mismatch_pct}% "
            f"(threshold: {threshold_pct}%, market_price: {market_price})"
        )

        if mismatch_pct <= threshold_pct:
            logger.info(f"Auto-recovery: price mismatch within tolerance for {symbol}")
            return {
                "success": True,
                "message": f"Price mismatch {mismatch_pct:.2f}% is within threshold {threshold_pct}% for {symbol}",
                "action": "use_market_price",
                "market_price": market_price,
                "adjusted": True,
            }
        else:
            logger.info(f"Auto-recovery: price mismatch too large for {symbol}, skipping signal")
            return {
                "success": False,
                "message": f"Price mismatch {mismatch_pct:.2f}% exceeds threshold {threshold_pct}% for {symbol}. Skipping signal.",
                "action": "skip_signal",
            }

    # ────────────────────────────────────────
    # Generic recovery dispatcher
    # ────────────────────────────────────────

    async def recover(
        self,
        error: UltraBotError,
        **kwargs,
    ) -> Dict[str, Any]:
        """Dispatch to the appropriate recovery method based on error type."""
        if isinstance(error, TokenExpiredError):
            return await self.recover_token_expired(
                error, refresh_fn=kwargs.get("refresh_fn")
            )
        elif isinstance(error, WebSocketDisconnectedError):
            return await self.recover_websocket_disconnected(
                error, connect_fn=kwargs.get("connect_fn")
            )
        elif isinstance(error, StaleDataError):
            return await self.recover_stale_data(
                error, fetch_fn=kwargs.get("fetch_fn")
            )
        elif isinstance(error, PriceMismatchError):
            return await self.recover_price_mismatch(
                error, threshold_pct=kwargs.get("threshold_pct", 0.5)
            )
        else:
            return {
                "success": False,
                "message": f"No auto-recovery strategy for error type: {type(error).__name__}",
                "action": "manual_intervention",
            }

    # ────────────────────────────────────────
    # Reset attempts (call on successful reconnect or manual reset)
    # ────────────────────────────────────────

    def reset_attempts(self, key: str) -> None:
        """Reset reconnect attempt counter for a given key."""
        self._reconnect_attempts.pop(key, None)
        self._last_attempt.pop(key, None)

    def reset_all_attempts(self) -> None:
        """Reset all reconnect attempt counters."""
        self._reconnect_attempts.clear()
        self._last_attempt.clear()
