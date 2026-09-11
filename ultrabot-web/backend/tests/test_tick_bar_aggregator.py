"""Unit tests for in-memory TickBarAggregator."""
import time
from datetime import datetime, timezone
import pytest
import pandas as pd

from feeds.tick_bar_aggregator import TickBarAggregator


class TestTickBarAggregator:
    def test_init_default_timeframes(self):
        agg = TickBarAggregator()
        assert "10s" in agg.timeframes
        assert "1m" in agg.timeframes
        assert "5m" in agg.timeframes
        assert agg.max_bars == 300

    def test_single_tick_forms_bar(self):
        agg = TickBarAggregator(timeframes=["1m"])
        t0 = 1700000000  # epoch seconds
        agg.on_tick("RELIANCE", 2500.0, volume=10, timestamp=t0)
        
        # Forming candle should be visible
        forming = agg.get_forming_candle("RELIANCE", "1m")
        assert forming is not None
        assert forming["open"] == 2500.0
        assert forming["high"] == 2500.0
        assert forming["low"] == 2500.0
        assert forming["close"] == 2500.0
        assert forming["volume"] == 10

    def test_multiple_ticks_update_forming_bar(self):
        agg = TickBarAggregator(timeframes=["1m"])
        t0 = 1700000000
        agg.on_tick("RELIANCE", 2500.0, volume=10, timestamp=t0)
        agg.on_tick("RELIANCE", 2515.0, volume=5, timestamp=t0 + 5)
        agg.on_tick("RELIANCE", 2490.0, volume=15, timestamp=t0 + 10)
        agg.on_tick("RELIANCE", 2505.0, volume=8, timestamp=t0 + 20)

        forming = agg.get_forming_candle("RELIANCE", "1m")
        assert forming["open"] == 2500.0
        assert forming["high"] == 2515.0
        assert forming["low"] == 2490.0
        assert forming["close"] == 2505.0
        assert forming["volume"] == 38

    def test_bar_close_event_and_completed_storage(self):
        agg = TickBarAggregator(timeframes=["1m"])
        closed_bars = []

        def on_close(symbol, tf, bar):
            closed_bars.append((symbol, tf, bar))

        agg.add_bar_listener(on_close)

        # Minute 0: timestamp 60
        agg.on_tick("TCS", 3000.0, volume=10, timestamp=60)
        agg.on_tick("TCS", 3010.0, volume=20, timestamp=80)
        
        # Next minute: timestamp 120 triggers close of bar for [60, 119]
        agg.on_tick("TCS", 3020.0, volume=15, timestamp=120)

        assert len(closed_bars) == 1
        sym, tf, bar = closed_bars[0]
        assert sym == "TCS"
        assert tf == "1m"
        assert bar["open"] == 3000.0
        assert bar["high"] == 3010.0
        assert bar["low"] == 3000.0
        assert bar["close"] == 3010.0
        assert bar["volume"] == 30

        # Check completed bars stored
        bars = agg.get_candles("TCS", "1m", count=10, include_forming=False)
        assert len(bars) == 1
        assert bars[0]["close"] == 3010.0

    def test_cumulative_volume_delta(self):
        agg = TickBarAggregator(timeframes=["1m"])
        t0 = 1000
        # First tick of day: 100 shares traded so far
        agg.on_tick("INFY", 1500.0, volume=100, timestamp=t0, cumulative_volume=True)
        # Next tick: cumulative volume is now 150 (delta = 50)
        agg.on_tick("INFY", 1502.0, volume=150, timestamp=t0 + 5, cumulative_volume=True)
        # Next tick: cumulative volume is 210 (delta = 60)
        agg.on_tick("INFY", 1501.0, volume=210, timestamp=t0 + 10, cumulative_volume=True)

        forming = agg.get_forming_candle("INFY", "1m")
        # First tick establishes baseline (vol_incr = 0) so mid-day connect doesn't spike.
        # Total volume should be delta (150-100=50) + delta (210-150=60) = 110
        assert forming["volume"] == 110

    def test_seed_history(self):
        agg = TickBarAggregator(timeframes=["1m"], max_bars=10)
        history = [
            {"timestamp": f"2026-09-11 09:{i:02d}:00", "open": 100 + i, "high": 105 + i, "low": 99 + i, "close": 102 + i, "volume": 1000}
            for i in range(5)
        ]
        agg.seed_history("SBIN", "1m", history)
        candles = agg.get_candles("SBIN", "1m", include_forming=False)
        assert len(candles) == 5
        assert candles[0]["open"] == 100
        assert candles[-1]["open"] == 104

    def test_ring_buffer_maxlen_eviction(self):
        agg = TickBarAggregator(timeframes=["10s"], max_bars=5)
        # Push 10 distinct 10-second periods
        for i in range(10):
            t = i * 10
            agg.on_tick("TEST", 100.0 + i, volume=1, timestamp=t)
        # Final tick to seal the 9th bar
        agg.on_tick("TEST", 200.0, volume=1, timestamp=100)

        candles = agg.get_candles("TEST", "10s", include_forming=False)
        # Max capacity is 5
        assert len(candles) == 5
        # The oldest remaining candle should be the 5th one (i=4)
        assert candles[-1]["close"] == 108.0 or candles[-1]["open"] >= 104.0

    def test_get_candles_df(self):
        agg = TickBarAggregator(timeframes=["1m"])
        agg.on_tick("NIFTY", 20000.0, volume=50, timestamp=100)
        df = agg.get_candles_df("NIFTY", "1m", include_forming=True)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert "open" in df.columns
        assert "close" in df.columns
        assert df["close"].iloc[0] == 20000.0

    def test_dynamic_timeframe_seeding_no_keyerror(self):
        agg = TickBarAggregator()  # default timeframes
        # Seed 15m candles (must not throw KeyError '15m')
        history = [
            {"timestamp": f"2026-09-11 09:{i*15:02d}:00", "open": 500 + i, "high": 510, "low": 490, "close": 505, "volume": 5000}
            for i in range(3)
        ]
        count = agg.seed_candles("SHRIRAMFIN", "15m", history)
        assert count == 3
        candles = agg.get_candles("SHRIRAMFIN", "15m", include_forming=False)
        assert len(candles) == 3
        assert candles[0]["open"] == 500

    def test_seed_candles_deduplication_and_update(self):
        agg = TickBarAggregator()
        initial = [
            {"timestamp": "2026-09-11 09:15:00", "open": 500, "high": 510, "low": 490, "close": 505, "volume": 1000},
            {"timestamp": "2026-09-11 09:20:00", "open": 505, "high": 515, "low": 500, "close": 510, "volume": 1200},
        ]
        agg.seed_candles("INFY", "5m", initial)
        assert len(agg.get_candles("INFY", "5m", include_forming=False)) == 2

        # Re-seed with updated bar for 09:20 and new bar for 09:25
        updated = [
            {"timestamp": "2026-09-11 09:20:00", "open": 505, "high": 518, "low": 500, "close": 515, "volume": 1500},
            {"timestamp": "2026-09-11 09:25:00", "open": 515, "high": 520, "low": 512, "close": 518, "volume": 800},
        ]
        added = agg.seed_candles("INFY", "5m", updated)
        candles = agg.get_candles("INFY", "5m", include_forming=False)
        assert len(candles) == 3
        # Ensure 09:20 was updated in-place (no duplicates)
        assert candles[1]["timestamp"] == "2026-09-11 09:20:00"
        assert candles[1]["high"] == 518.0
        assert candles[1]["close"] == 515.0
        assert candles[2]["timestamp"] == "2026-09-11 09:25:00"


@pytest.mark.asyncio
async def test_feed_manager_falls_back_when_memory_candles_are_stale():
    from unittest.mock import AsyncMock, MagicMock
    from datetime import datetime, timedelta
    from feeds.feed_manager import FeedManager

    mock_mkt_hours = MagicMock()
    mock_mkt_hours.is_market_open.return_value = True

    # 1 hour old candles in memory
    old_time = (datetime.now() - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
    agg = TickBarAggregator()
    agg.seed_candles("TCS", "5m", [
        {"timestamp": old_time, "open": 3500, "high": 3510, "low": 3495, "close": 3505, "volume": 1000}
        for _ in range(5)
    ])

    fresh_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fresh_candles = [
        {"timestamp": fresh_time, "open": 3520, "high": 3530, "low": 3515, "close": 3525, "volume": 2000}
    ]

    mock_primary = MagicMock()
    mock_primary.get_candles = AsyncMock(return_value=fresh_candles)

    fm = FeedManager(primary=mock_primary, market_hours=mock_mkt_hours, aggregator=agg)

    # When get_candles is called during open market hours, old in-memory candles (> 10m)
    # must trigger primary.get_candles rather than returning stale memory bars
    candles = await fm.get_candles("TCS", timeframe="5m", count=5)
    assert mock_primary.get_candles.called
    assert candles[-1]["timestamp"] == fresh_time
    assert candles[-1]["close"] == 3525.0


