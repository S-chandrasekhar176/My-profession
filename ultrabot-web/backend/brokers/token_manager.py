import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Token validity in seconds (Angel One ~ 1 day, Shoonya ~ 1 day)
_DEFAULT_TOKEN_TTL = 86400


class TokenManager:
    """Manage broker authentication tokens with expiry tracking and background refresh.

    Stores tokens per broker and checks expiry before use.
    Supports background token refresh tasks.
    """

    def __init__(self, default_ttl: int = _DEFAULT_TOKEN_TTL):
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._default_ttl = default_ttl
        self._refresh_tasks: Dict[str, asyncio.Task] = {}
        self._refresh_callbacks: Dict[str, Any] = {}

    def store_token(
        self,
        broker_name: str,
        access_token: str,
        refresh_token: str = "",
        extra: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None,
    ) -> None:
        """Store a token for a broker.

        Args:
            broker_name: Broker identifier.
            access_token: JWT/session token.
            refresh_token: Refresh token if available.
            extra: Additional data to store (e.g. feed_token).
            ttl: Time-to-live in seconds. Uses default if None.
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._tokens[broker_name] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "stored_at": time.time(),
            "expires_at": time.time() + effective_ttl,
            "extra": extra or {},
        }
        logger.info("Token stored for %s, expires in %d seconds", broker_name, effective_ttl)

    def get_token(self, broker_name: str) -> Optional[str]:
        """Get the access token for a broker.

        Returns None if no token or expired.
        """
        entry = self._tokens.get(broker_name)
        if entry is None:
            return None
        if self.is_expired(broker_name):
            return None
        return entry["access_token"]

    def get_extra(self, broker_name: str, key: str, default: Any = None) -> Any:
        """Get extra stored data for a broker.

        Args:
            broker_name: Broker identifier.
            key: Key to look up in extra dict.
            default: Default if key not found.
        """
        entry = self._tokens.get(broker_name)
        if entry is None:
            return default
        return entry.get("extra", {}).get(key, default)

    def is_expired(self, broker_name: str) -> bool:
        """Check if a broker's token has expired.

        Returns True if no token exists or if it has expired.
        Also returns True if token will expire within 5 minutes (safety buffer).
        """
        entry = self._tokens.get(broker_name)
        if entry is None:
            return True
        now = time.time()
        # 5-minute safety buffer
        return now >= (entry["expires_at"] - 300)

    def time_until_expiry(self, broker_name: str) -> float:
        """Seconds until token expires. Returns 0 if already expired."""
        entry = self._tokens.get(broker_name)
        if entry is None:
            return 0.0
        remaining = entry["expires_at"] - time.time()
        return max(0.0, remaining)

    def remove_token(self, broker_name: str) -> None:
        """Remove stored token for a broker."""
        self._tokens.pop(broker_name, None)
        logger.info("Token removed for %s", broker_name)

    def clear_all(self) -> None:
        """Remove all stored tokens."""
        self._tokens.clear()
        logger.info("All tokens cleared")

    def register_refresh_callback(self, broker_name: str, callback: Any) -> None:
        """Register an async callback for token refresh.

        The callback should be an async function that takes no arguments
        and returns True on successful refresh, False on failure.

        Args:
            broker_name: Broker to register for.
            callback: Async callable.
        """
        self._refresh_callbacks[broker_name] = callback

    async def refresh_token(self, broker_name: str) -> bool:
        """Manually trigger a token refresh.

        Returns:
            True if refresh succeeded, False otherwise.
        """
        callback = self._refresh_callbacks.get(broker_name)
        if callback is None:
            logger.warning("No refresh callback registered for %s", broker_name)
            return False
        try:
            result = await callback()
            if result:
                logger.info("Token refreshed successfully for %s", broker_name)
            else:
                logger.warning("Token refresh failed for %s", broker_name)
            return bool(result)
        except Exception as e:
            logger.error("Token refresh error for %s: %s", broker_name, e)
            return False

    def start_background_refresh(self, broker_name: str, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start a background task that refreshes the token before expiry.

        The task checks every 60 seconds and refreshes 10 minutes before expiry.

        Args:
            broker_name: Broker to start refresh for.
            loop: Event loop to use. Uses running loop if None.
        """
        if broker_name in self._refresh_tasks:
            return

        async def _refresh_loop():
            while True:
                try:
                    remaining = self.time_until_expiry(broker_name)
                    # Refresh 10 minutes before expiry
                    sleep_time = max(0, remaining - 600)
                    if sleep_time <= 0:
                        await self.refresh_token(broker_name)
                        sleep_time = 3600  # Check again in 1 hour
                    await asyncio.sleep(min(sleep_time, 60))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Background refresh error for %s: %s", broker_name, e)
                    await asyncio.sleep(60)

        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()

        task = loop.create_task(_refresh_loop())
        self._refresh_tasks[broker_name] = task
        logger.info("Background refresh started for %s", broker_name)

    def stop_background_refresh(self, broker_name: str) -> None:
        """Stop the background refresh task for a broker."""
        task = self._refresh_tasks.pop(broker_name, None)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Background refresh stopped for %s", broker_name)

    def stop_all_refresh_tasks(self) -> None:
        """Stop all background refresh tasks."""
        for name in list(self._refresh_tasks.keys()):
            self.stop_background_refresh(name)

    def get_all_brokers(self) -> list:
        """Return list of brokers with stored tokens."""
        return list(self._tokens.keys())

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """Return status of all stored tokens."""
        status = {}
        for name, entry in self._tokens.items():
            status[name] = {
                "has_token": bool(entry["access_token"]),
                "is_expired": self.is_expired(name),
                "time_until_expiry": round(self.time_until_expiry(name), 1),
                "has_refresh_callback": name in self._refresh_callbacks,
                "background_refresh_active": name in self._refresh_tasks,
            }
        return status
