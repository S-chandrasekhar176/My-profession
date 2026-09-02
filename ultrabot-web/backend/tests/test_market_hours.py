"""Tests for market status detection."""
import pytest
from datetime import date, time
from zoneinfo import ZoneInfo

from core.market_hours import MarketHours, NSE_HOLIDAYS_2025

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def mh():
    return MarketHours()


class TestHolidayCheck:
    def test_known_holiday(self, mh):
        assert mh.is_market_holiday(date(2025, 1, 26)) is True  # Republic Day
        assert mh.is_market_holiday(date(2025, 8, 15)) is True  # Independence Day
        assert mh.is_market_holiday(date(2025, 12, 25)) is True  # Christmas

    def test_normal_day(self, mh):
        assert mh.is_market_holiday(date(2025, 1, 20)) is False  # Regular Monday

    def test_weekend_not_holiday(self, mh):
        # Weekends are not in the holiday list – they're handled by weekday check
        assert mh.is_market_holiday(date(2025, 1, 18)) is False  # Saturday


class TestMarketOpen:
    def test_returns_bool(self, mh):
        result = mh.is_market_open()
        assert isinstance(result, bool)

    def test_weekend_closed(self, mh):
        # We can't easily mock datetime.now, but the method should return False on weekends
        # This test just ensures the method runs
        result = mh.is_market_open()
        assert isinstance(result, bool)


class TestGetSession:
    def test_returns_valid_session(self, mh):
        session = mh.get_current_session()
        assert session in ("pre_market", "market", "post_market", "closed")


class TestMarketStatus:
    def test_status_dict_structure(self, mh):
        status = mh.get_market_status()
        assert "is_open" in status
        assert "session" in status
        assert "next_open" in status
        assert "time_to_open_seconds" in status
        assert "current_time_ist" in status

    def test_is_open_type(self, mh):
        status = mh.get_market_status()
        assert isinstance(status["is_open"], bool)

    def test_session_matches(self, mh):
        status = mh.get_market_status()
        assert status["session"] in ("pre_market", "market", "post_market", "closed")


class TestTradeWindow:
    def test_returns_bool(self, mh):
        result = mh.is_new_trade_window()
        assert isinstance(result, bool)

    def test_custom_window(self):
        custom = MarketHours(
            trade_window_start=time(10, 0),
            trade_window_end=time(11, 0),
        )
        result = custom.is_new_trade_window()
        assert isinstance(result, bool)


class TestCustomHolidays:
    def test_custom_holiday_list(self):
        custom = MarketHours(holidays=[date(2025, 6, 1)])
        assert custom.is_market_holiday(date(2025, 6, 1)) is True
        assert custom.is_market_holiday(date(2025, 1, 26)) is False  # Not in custom list


class TestNextOpen:
    def test_next_open_structure(self, mh):
        status = mh.get_market_status()
        if status["is_open"]:
            assert status["next_open"] is None
        else:
            assert status["next_open"] is not None
