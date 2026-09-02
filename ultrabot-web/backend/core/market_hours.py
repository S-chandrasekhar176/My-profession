"""NSE market hours utility for UltraBot.

All times use Asia/Kolkata (IST) via zoneinfo.ZoneInfo.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# NSE trading hours
NSE_PRE_MARKET_START = time(9, 0)
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)
NSE_POST_MARKET_END = time(16, 0)

# Safe auto square-off time (intraday positions auto-closed to prevent penalties)
SAFE_EXIT_TIME = time(15, 15)

# New trade window (configurable, defaults from defaults.yaml)
NEW_TRADE_WINDOW_START = time(9, 15)
NEW_TRADE_WINDOW_END = time(15, 15)

# NSE holidays for 2025 (16 dates)
NSE_HOLIDAYS_2025: list[date] = [
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Maha Shivaratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Ram Navami
    date(2025, 4, 14),   # Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 6, 5),    # Bakri Id
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Janmashtami
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 20),  # Dussehra
    date(2025, 10, 21),  # Diwali - Laxmi Pujan (Muhurat Trading)
    date(2025, 11, 5),   # Gurunanak Jayanti
    date(2025, 12, 25),  # Christmas
]

# NSE holidays for 2026
NSE_HOLIDAYS_2026: list[date] = [
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 17),   # Maha Shivaratri
    date(2026, 3, 3),    # Holi
    date(2026, 3, 20),   # Id-Ul-Fitr
    date(2026, 3, 27),   # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 27),   # Bakri Id / Eid ul-Adha
    date(2026, 6, 26),   # Muharram
    date(2026, 8, 15),   # Independence Day
    date(2026, 9, 4),    # Milad-un-Nabi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 8),   # Diwali Laxmi Pujan
    date(2026, 11, 10),  # Diwali Balipratipada
    date(2026, 11, 24),  # Gurunanak Jayanti
    date(2026, 12, 25),  # Christmas
]

NSE_HOLIDAYS: list[date] = NSE_HOLIDAYS_2025 + NSE_HOLIDAYS_2026


class MarketHours:
    """Utility class for NSE market hours and holiday checks."""

    def __init__(
        self,
        pre_market_start: time = NSE_PRE_MARKET_START,
        market_open: time = NSE_OPEN,
        market_close: time = NSE_CLOSE,
        post_market_end: time = NSE_POST_MARKET_END,
        trade_window_start: time = NEW_TRADE_WINDOW_START,
        trade_window_end: time = NEW_TRADE_WINDOW_END,
        safe_exit_time: time = SAFE_EXIT_TIME,
        holidays: Optional[list[date]] = None,
    ):
        self.pre_market_start = pre_market_start
        self.market_open = market_open
        self.market_close = market_close
        self.post_market_end = post_market_end
        self.trade_window_start = trade_window_start
        self.trade_window_end = trade_window_end
        self.safe_exit_time = safe_exit_time
        self.holidays = holidays if holidays is not None else NSE_HOLIDAYS

    def _ist_now(self) -> datetime:
        """Get current datetime in IST."""
        return datetime.now(IST)

    def _ist_today(self) -> date:
        """Get today's date in IST."""
        return self._ist_now().date()

    def is_market_holiday(self, check_date: Optional[date] = None) -> bool:
        """Check if a given date is an NSE holiday.

        Args:
            check_date: Date to check. Defaults to today in IST.

        Returns:
            True if the date is a holiday, False otherwise.
        """
        if check_date is None:
            check_date = self._ist_today()
        return check_date in self.holidays

    def is_market_open(self) -> bool:
        """Check if the NSE market is currently in regular trading hours.

        Returns:
            True if market is between 09:15 and 15:30 IST on a non-holiday weekday.
        """
        now = self._ist_now()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        if self.is_market_holiday(now.date()):
            return False
        current_time = now.time()
        return self.market_open <= current_time < self.market_close

    def get_current_session(self) -> str:
        """Determine the current market session.

        Returns:
            One of: 'pre_market', 'market', 'post_market', 'closed'
        """
        now = self._ist_now()
        current_time = now.time()

        if self.is_market_holiday(now.date()) or now.weekday() >= 5:
            return "closed"

        if self.pre_market_start <= current_time < self.market_open:
            return "pre_market"
        if self.market_open <= current_time < self.market_close:
            return "market"
        if self.market_close <= current_time < self.post_market_end:
            return "post_market"
        return "closed"

    def get_market_status(self) -> Dict:
        """Get detailed market status information.

        Returns:
            Dict with keys: is_open, session, next_open, time_to_open.
        """
        now = self._ist_now()
        is_open = self.is_market_open()
        session = self.get_current_session()

        # Calculate time to close if market is open
        time_to_close_seconds = 0
        if is_open:
            close_dt = datetime.combine(now.date(), self.market_close, tzinfo=IST)
            time_to_close_seconds = max(0, int((close_dt - now).total_seconds()))

        # Calculate next market open
        next_open_dt = self._calculate_next_open(now)
        if next_open_dt is not None:
            time_to_open = next_open_dt - now
            next_open_str = next_open_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        else:
            time_to_open = None
            next_open_str = None

        return {
            "is_open": is_open,
            "session": session,
            "next_open": next_open_str,
            "time_to_open_seconds": time_to_open.total_seconds() if time_to_open else None,
            "time_to_close_seconds": time_to_close_seconds,
            "current_time_ist": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        }

    def _calculate_next_open(self, now: datetime) -> Optional[datetime]:
        """Calculate the next market open datetime from a given moment.

        Args:
            now: Current datetime in IST.

        Returns:
            Datetime of next market open, or None if market is currently open.
        """
        # If market is open right now, return None
        today_open = datetime.combine(now.date(), self.market_open, tzinfo=IST)
        today_close = datetime.combine(now.date(), self.market_close, tzinfo=IST)
        if today_open <= now < today_close and now.weekday() < 5 and not self.is_market_holiday(now.date()):
            return None

        # If market hasn't opened yet today and it's a valid day, today is next open
        if now < today_open and now.weekday() < 5 and not self.is_market_holiday(now.date()):
            return today_open

        # Otherwise, find the next valid trading day
        search_date = now.date() + timedelta(days=1)
        max_search = 14  # Safety: don't search more than 14 days ahead
        while max_search > 0:
            if search_date.weekday() < 5 and not self.is_market_holiday(search_date):
                return datetime.combine(search_date, self.market_open, tzinfo=IST)
            search_date += timedelta(days=1)
            max_search -= 1

        logger.warning("Could not find next market open within 14 days")
        return None

    def get_time_to_close(self) -> timedelta:
        """Get time remaining until market close.

        Returns:
            timedelta until 15:30 IST. If market is closed, returns timedelta(0).
            If market hasn't opened yet today, returns 0.
        """
        now = self._ist_now()
        close_dt = datetime.combine(now.date(), self.market_close, tzinfo=IST)

        if now >= close_dt:
            return timedelta(0)

        # Only return positive time_to_close during market hours or pre-market
        open_dt = datetime.combine(now.date(), self.market_open, tzinfo=IST)
        if now < open_dt:
            return timedelta(0)

        return close_dt - now

    def is_new_trade_window(self) -> bool:
        """Check if we're in the new-trade window (09:15 - 15:15 IST).

        This is when the engine is allowed to take new positions.
        Outside this window, only exit/management actions are allowed.

        Returns:
            True if current IST time is within 09:15-15:15 on a trading day.
        """
        now = self._ist_now()
        if now.weekday() >= 5:
            return False
        if self.is_market_holiday(now.date()):
            return False
        current_time = now.time()
        return self.trade_window_start <= current_time < self.trade_window_end

    def is_safe_exit_time(self) -> bool:
        """Check if current time is at or past the safe square-off time (default 15:15 IST)
        or if the market has closed for the day.

        When this is True, all open intraday positions should be squared off immediately.

        Returns:
            True if it's weekend/holiday, or on a trading day if IST time >= safe_exit_time.
        """
        now = self._ist_now()
        if now.weekday() >= 5:
            return True
        if self.is_market_holiday(now.date()):
            return True
        current_time = now.time()
        # If past square-off time or past market close
        return current_time >= self.safe_exit_time

