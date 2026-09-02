import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.market_hours import IST
from feeds.yahoo_historical import YahooHistoricalFeed


@pytest.fixture
def mock_yf_history():
    """Mock yfinance Ticker history DataFrame."""
    import pandas as pd

    timestamps = pd.date_range(start="2026-08-22 09:15", periods=20, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame(
        {
            "Open": [2500.0 + i for i in range(20)],
            "High": [2510.0 + i for i in range(20)],
            "Low": [2490.0 + i for i in range(20)],
            "Close": [2505.0 + i for i in range(20)],
            "Volume": [1000 + i * 10 for i in range(20)],
        },
        index=timestamps,
    )

    with patch("yfinance.Ticker") as mock_ticker_cls:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df
        mock_ticker_cls.return_value = mock_ticker
        yield mock_ticker_cls, mock_ticker


@pytest.mark.asyncio
async def test_candle_caching_hit_and_miss(mock_yf_history):
    """Subsequent get_candles calls within TTL return cached data without hitting yfinance."""
    mock_ticker_cls, mock_ticker = mock_yf_history
    feed = YahooHistoricalFeed(cache_ttl_seconds=30.0)

    # 1. Initial call -> cache miss, hits yfinance
    candles1 = await feed.get_candles("RELIANCE", timeframe="5m", count=20)
    assert len(candles1) == 20
    assert mock_ticker.history.call_count == 1
    stats1 = feed.get_cache_stats()
    assert stats1["misses"] == 1
    assert stats1["hits"] == 0
    assert stats1["cached_entries"] == 1

    # 2. Second call with identical params -> cache hit, does NOT call yfinance
    candles2 = await feed.get_candles("RELIANCE", timeframe="5m", count=20)
    assert len(candles2) == 20
    assert candles1 == candles2
    assert mock_ticker.history.call_count == 1  # Unchanged!
    stats2 = feed.get_cache_stats()
    assert stats2["misses"] == 1
    assert stats2["hits"] == 1


@pytest.mark.asyncio
async def test_cache_ttl_expiry(mock_yf_history):
    """Calls after TTL expires trigger a cache miss and refresh data."""
    mock_ticker_cls, mock_ticker = mock_yf_history
    feed = YahooHistoricalFeed(cache_ttl_seconds=0.1)

    # 1. First fetch
    await feed.get_candles("INFY", timeframe="5m", count=10)
    assert mock_ticker.history.call_count == 1

    # 2. Wait for TTL to expire
    await asyncio.sleep(0.15)

    # 3. Second fetch after TTL -> cache miss, calls yfinance again
    await feed.get_candles("INFY", timeframe="5m", count=10)
    assert mock_ticker.history.call_count == 2
    stats = feed.get_cache_stats()
    assert stats["misses"] == 2
    assert stats["hits"] == 0


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(mock_yf_history):
    """force_refresh=True ignores valid cached entry and fetches fresh data."""
    mock_ticker_cls, mock_ticker = mock_yf_history
    feed = YahooHistoricalFeed(cache_ttl_seconds=60.0)

    await feed.get_candles("TCS", timeframe="5m", count=15)
    assert mock_ticker.history.call_count == 1

    # Force refresh
    await feed.get_candles("TCS", timeframe="5m", count=15, force_refresh=True)
    assert mock_ticker.history.call_count == 2
    stats = feed.get_cache_stats()
    assert stats["misses"] == 2
    assert stats["hits"] == 0


@pytest.mark.asyncio
async def test_clear_cache_and_disconnect(mock_yf_history):
    """clear_cache() and disconnect() evict cached entries."""
    mock_ticker_cls, mock_ticker = mock_yf_history
    feed = YahooHistoricalFeed(cache_ttl_seconds=60.0)

    await feed.get_candles("SBIN", timeframe="5m", count=10)
    assert feed.get_cache_stats()["cached_entries"] == 1

    feed.clear_cache()
    assert feed.get_cache_stats()["cached_entries"] == 0

    # Next call misses
    await feed.get_candles("SBIN", timeframe="5m", count=10)
    assert mock_ticker.history.call_count == 2

    # Disconnect clears cache
    await feed.disconnect()
    assert feed.get_cache_stats()["cached_entries"] == 0


@pytest.mark.asyncio
async def test_cache_returns_defensive_copies(mock_yf_history):
    """Mutating returned candle lists does not corrupt in-memory cache."""
    mock_ticker_cls, mock_ticker = mock_yf_history
    feed = YahooHistoricalFeed(cache_ttl_seconds=60.0)

    candles1 = await feed.get_candles("TATAMOTORS", timeframe="5m", count=10)
    # Mutate the returned list
    candles1[0]["close"] = 999999.0

    # Fetch again from cache
    candles2 = await feed.get_candles("TATAMOTORS", timeframe="5m", count=10)
    assert candles2[0]["close"] != 999999.0
    assert candles2[0]["close"] == 2515.0


@pytest.mark.asyncio
async def test_cache_key_isolation(mock_yf_history):
    """Different symbols, timeframes, or counts create separate cache entries."""
    mock_ticker_cls, mock_ticker = mock_yf_history
    feed = YahooHistoricalFeed(cache_ttl_seconds=60.0)

    await feed.get_candles("RELIANCE", timeframe="5m", count=10)
    await feed.get_candles("RELIANCE", timeframe="15m", count=10)
    await feed.get_candles("TCS", timeframe="5m", count=10)
    await feed.get_candles("TCS", timeframe="5m", count=20)

    assert mock_ticker.history.call_count == 4
    assert feed.get_cache_stats()["cached_entries"] == 4
    assert feed.get_cache_stats()["misses"] == 4
