"""Tests for FeedManager dynamic hot-upgrade from Yahoo fallback to Fyers primary."""
import pytest
from unittest.mock import MagicMock
from feeds.feed_manager import FeedManager
from feeds.yahoo_historical import YahooHistoricalFeed
from feeds.fyers_candles import FyersCandleFeed
from brokers.relogin import apply_tokens_to_engine


def test_feed_hot_upgrade_from_yahoo_to_fyers():
    # 1. Simulate engine booted without Fyers token (Yahoo primary)
    yahoo_feed = YahooHistoricalFeed()
    feed_manager = FeedManager(primary=yahoo_feed, backup=None)
    
    mock_engine = MagicMock()
    mock_engine.feed = feed_manager
    mock_engine.broker_name = "paper"

    assert isinstance(feed_manager.primary, YahooHistoricalFeed)
    assert feed_manager.backup is None

    # 2. Simulate user completes OAuth post-boot
    tokens = {
        "kind": "fyers",
        "access_token": "test_jwt_token_123",
        "app_id": "TESTAPP-100",
    }
    applied = apply_tokens_to_engine(mock_engine, "fyers", tokens)
    assert applied is True

    # 3. Verify FeedManager has been hot-upgraded to FyersCandleFeed primary
    assert isinstance(feed_manager.primary, FyersCandleFeed)
    assert isinstance(feed_manager.backup, YahooHistoricalFeed)
    assert feed_manager._using_backup is False
    assert feed_manager._primary_healthy is True

    # 4. Verify subsequent token refresh calls apply_new_token on existing FyersCandleFeed
    tokens_v2 = {
        "kind": "fyers",
        "access_token": "test_jwt_token_456",
        "app_id": "TESTAPP-100",
    }
    applied_v2 = apply_tokens_to_engine(mock_engine, "fyers", tokens_v2)
    assert applied_v2 is True
    assert isinstance(feed_manager.primary, FyersCandleFeed)
    assert feed_manager.primary._broker.access_token == "test_jwt_token_456"
