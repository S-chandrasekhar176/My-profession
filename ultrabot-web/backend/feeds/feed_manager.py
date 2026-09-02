import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.market_hours import MarketHours, IST
from feeds.base import BaseFeed
from feeds.yahoo_historical import YahooHistoricalFeed

logger = logging.getLogger(__name__)


class FeedManager:
    """Manage primary and backup market data feeds.

    Tries the primary feed first for LTP/candles.
    Falls back to backup if primary fails or returns 0.
    Supports active probe health checking, frozen data detection, and failure tracking.
    """

    def __init__(
        self,
        primary: Optional[BaseFeed] = None,
        backup: Optional[BaseFeed] = None,
        watchdog_interval_seconds: float = 120.0,
        market_hours: Optional[MarketHours] = None,
    ):
        self.primary = primary or YahooHistoricalFeed()
        self.backup = backup
        self.market_hours = market_hours or MarketHours()
        self._using_backup = False
        self._primary_failure_count = 0
        self._max_failures_before_switch = 3
        self._last_health_check: float = 0.0
        self._last_successful_fetch_time: float = 0.0
        self._watchdog_interval_seconds = float(watchdog_interval_seconds)
        self._primary_healthy = True
        self._last_health_result: Dict[str, Any] = {}

        # Frozen feed detection state
        self._last_observed_probe_timestamp: Optional[str] = None
        self._last_observed_probe_price: Optional[float] = None
        self._consecutive_frozen_checks: int = 0
        self._max_frozen_checks_before_alert: int = 5
        self._feed_is_frozen: bool = False

    async def get_ltp(self, symbol: str) -> float:
        """Get LTP, trying primary first, then backup."""
        if not self._using_backup:
            try:
                ltp = await self.primary.get_ltp(symbol)
                if ltp and ltp > 0:
                    self._primary_failure_count = 0
                    self._primary_healthy = True
                    self._last_successful_fetch_time = time.time()
                    return ltp
                self._primary_failure_count += 1
                self._primary_healthy = False
            except Exception as e:
                logger.warning("Primary feed LTP error for %s: %s", symbol, e)
                self._primary_failure_count += 1
                self._primary_healthy = False

            if self._primary_failure_count >= self._max_failures_before_switch:
                await self.switch_to_backup()

        if self.backup is not None:
            try:
                ltp = await self.backup.get_ltp(symbol)
                if ltp and ltp > 0:
                    return float(ltp)
            except Exception as e:
                logger.warning("Backup feed LTP error for %s: %s", symbol, e)

        return 0.0

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get candles, trying primary first, then backup."""
        if not self._using_backup:
            try:
                candles = await self.primary.get_candles(symbol, timeframe, count)
                # Only update last_successful_fetch_time when data is genuinely non-empty
                if candles and len(candles) > 0:
                    self._primary_failure_count = 0
                    self._primary_healthy = True
                    self._last_successful_fetch_time = time.time()
                    return candles
                self._primary_failure_count += 1
                self._primary_healthy = False
            except Exception as e:
                logger.warning("Primary feed candle error for %s: %s", symbol, e)
                self._primary_failure_count += 1
                self._primary_healthy = False

            if self._primary_failure_count >= self._max_failures_before_switch:
                await self.switch_to_backup()

        if self.backup is not None:
            try:
                candles = await self.backup.get_candles(symbol, timeframe, count)
                if candles:
                    return candles
            except Exception as e:
                logger.warning("Backup feed candle error for %s: %s", symbol, e)

        return []

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._using_backup:
            return await self.primary.subscribe(symbols)
        if self.backup is not None:
            return await self.backup.subscribe(symbols)
        return {"success": False, "message": "No active feed"}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        if not self._using_backup:
            return await self.primary.unsubscribe(symbols)
        if self.backup is not None:
            return await self.backup.unsubscribe(symbols)
        return {"success": False, "message": "No active feed"}

    async def switch_to_backup(self) -> Dict[str, Any]:
        if self.backup is None:
            logger.warning("No backup feed configured, cannot switch")
            return {"success": False, "message": "No backup feed available"}
        self._using_backup = True
        logger.warning("Switched to backup feed: %s", self.backup.get_name())
        return {"success": True, "message": f"Switched to backup: {self.backup.get_name()}", "backup_feed": self.backup.get_name()}

    async def switch_to_primary(self) -> Dict[str, Any]:
        self._using_backup = False
        self._primary_failure_count = 0
        logger.info("Switched back to primary feed: %s", self.primary.get_name())
        return {"success": True, "message": f"Switched to primary: {self.primary.get_name()}", "primary_feed": self.primary.get_name()}

    async def health_check(self, probe_symbol: str = "^NSEI", force_probe: bool = False) -> Dict[str, Any]:
        """Check if feed is alive using passive traffic confirmation or active probe with frozen detection."""
        now = time.time()
        self._last_health_check = now

        # 1. Passive confirmation: Recent non-empty data received within watchdog interval
        if (
            not force_probe
            and (now - self._last_successful_fetch_time) < self._watchdog_interval_seconds
            and self._primary_failure_count == 0
            and self._primary_healthy
            and not self._feed_is_frozen
            and self._consecutive_frozen_checks == 0
        ):
            result = {
                "name": self.primary.get_name(),
                "connected": True,
                "healthy": True,
                "status": "HEALTHY",
                "failure_count": 0,
                "frozen": False,
                "consecutive_frozen_checks": 0,
                "check_mode": "passive_traffic",
                "last_successful_fetch": self._last_successful_fetch_time,
                "last_check": now,
            }
            self._last_health_result = result
            return result

        # 2. Active Probe: Fetch current candle on benchmark index via get_candles (for timestamp advancement)
        probe_ok = False
        error_msg = None
        probe_price = 0.0
        probe_ts = None

        try:
            if hasattr(self.primary, "get_candles"):
                try:
                    probe_candles = await self.primary.get_candles(probe_symbol, timeframe="5m", count=1, force_refresh=True)
                    if probe_candles and len(probe_candles) > 0:
                        c = probe_candles[-1]
                        probe_price = float(c.get("close", 0.0))
                        probe_ts = c.get("timestamp")
                except Exception as cand_exc:
                    logger.debug("Probe get_candles exception for %s: %s", probe_symbol, cand_exc)

            if probe_price <= 0:
                probe_price = await self.primary.get_ltp(probe_symbol)
                probe_ts = None

            if probe_price and probe_price > 0:
                probe_ok = True
                self._last_successful_fetch_time = time.time()
                self._primary_failure_count = 0
            else:
                self._primary_failure_count += 1
                self._primary_healthy = False
                error_msg = f"Probe returned invalid price: {probe_price}"
        except Exception as exc:
            self._primary_failure_count += 1
            self._primary_healthy = False
            error_msg = str(exc)

        # 3. Multi-condition Frozen Feed Detection:
        # Conditions: Market open, outside opening window (>09:30), timestamp/price stalled across >= 5 checks
        now_dt = datetime.now(IST)
        is_mkt_open = self.market_hours.is_market_open() if hasattr(self.market_hours, "is_market_open") else False
        is_after_opening = False
        if is_mkt_open:
            open_window_end = now_dt.replace(hour=9, minute=30, second=0, microsecond=0)
            is_after_opening = now_dt >= open_window_end

        if probe_ok:
            if is_mkt_open and is_after_opening:
                # Compare against last observed probe
                is_stalled = False
                if probe_ts is not None and self._last_observed_probe_timestamp is not None:
                    is_stalled = (probe_ts == self._last_observed_probe_timestamp and probe_price == self._last_observed_probe_price)
                elif probe_price == self._last_observed_probe_price and self._last_observed_probe_price is not None:
                    is_stalled = True

                if is_stalled:
                    self._consecutive_frozen_checks += 1
                else:
                    self._consecutive_frozen_checks = 0
                    self._last_observed_probe_timestamp = probe_ts
                    self._last_observed_probe_price = probe_price
            else:
                self._consecutive_frozen_checks = 0
                self._last_observed_probe_timestamp = probe_ts
                self._last_observed_probe_price = probe_price

            if self._consecutive_frozen_checks >= self._max_frozen_checks_before_alert:
                self._feed_is_frozen = True
                self._primary_healthy = False
                status_str = "FROZEN"
            else:
                self._feed_is_frozen = False
                self._primary_healthy = True
                status_str = "HEALTHY"
        else:
            self._feed_is_frozen = False
            status_str = "DEGRADED" if self._primary_failure_count < self._max_failures_before_switch else "DOWN"

        if probe_ok and self._using_backup:
            await self.switch_to_primary()

        result = {
            "name": self.primary.get_name(),
            "connected": probe_ok,
            "healthy": self._primary_healthy,
            "status": status_str,
            "failure_count": self._primary_failure_count,
            "frozen": self._feed_is_frozen,
            "consecutive_frozen_checks": self._consecutive_frozen_checks,
            "check_mode": "active_probe",
            "last_successful_fetch": self._last_successful_fetch_time if self._last_successful_fetch_time > 0 else None,
            "last_check": now,
            "error": error_msg,
        }
        self._last_health_result = result
        return result

    def get_active_feed(self) -> BaseFeed:
        if self._using_backup and self.backup is not None:
            return self.backup
        return self.primary

    def get_status(self) -> Dict[str, Any]:
        return {
            "primary": self.primary.get_name(),
            "backup": self.backup.get_name() if self.backup else None,
            "using_backup": self._using_backup,
            "primary_failures": self._primary_failure_count,
            "primary_healthy": self._primary_healthy,
            "status": "FROZEN" if self._feed_is_frozen else ("HEALTHY" if self._primary_healthy and self._primary_failure_count == 0 else ("DEGRADED" if self._primary_failure_count < self._max_failures_before_switch else "DOWN")),
            "frozen": self._feed_is_frozen,
            "consecutive_frozen_checks": self._consecutive_frozen_checks,
            "last_health_check": self._last_health_check if self._last_health_check > 0 else None,
            "last_successful_fetch": self._last_successful_fetch_time if self._last_successful_fetch_time > 0 else None,
        }

    async def connect(self) -> Dict[str, Any]:
        result = await self.primary.connect()
        if self.backup is not None:
            backup_result = await self.backup.connect()
            result["backup"] = backup_result
        return result

    async def disconnect(self) -> Dict[str, Any]:
        result = await self.primary.disconnect()
        if self.backup is not None:
            backup_result = await self.backup.disconnect()
            result["backup"] = backup_result
        return result

    async def get_latest_price(self, symbol: str) -> float:
        """Get the latest price (LTP) for a symbol."""
        return await self.get_ltp(symbol)

