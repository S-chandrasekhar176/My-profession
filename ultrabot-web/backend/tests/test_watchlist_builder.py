"""Tests for WatchlistBuilder, TechnicalScanner, and Pre-Market Scheduler Integration."""
import asyncio
from datetime import datetime, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock

from scanner.watchlist_builder import WatchlistBuilder
from core.scheduler import MarketLifecycleScheduler


class MockFeed:
    """Mock candle feed providing synthetic data for testing."""

    def __init__(self, trends=None):
        self.trends = trends or {}

    async def get_candles(self, symbol: str, timeframe: str = "15m", count: int = 100):
        # Generate synthetic candles based on symbol trend. Timestamps are
        # relative to 'now' so the Phase-5 freshness guards (which drop
        # symbols whose newest candle is > 7 days old) see this as live data.
        trend = self.trends.get(symbol, "neutral")
        base_price = 1000.0
        candles = []
        now = datetime.now()

        for i in range(count):
            ts = (now - timedelta(minutes=15 * (count - i))).isoformat()
            if trend == "strong_bull":
                price = base_price * (1 + (i * 0.005))
                vol = 5000 + (i * 200)
            elif trend == "strong_bear":
                price = base_price * (1 - (i * 0.005))
                vol = 5000 + (i * 200)
            elif trend == "squeeze_bull":
                price = base_price * (1 + (0.001 * (i % 5)))
                if i >= count - 3:
                    price = base_price * 1.04  # Breakout
                vol = 2000 if i < count - 3 else 10000
            elif trend == "squeeze_bear":
                price = base_price * (1 - (0.001 * (i % 5)))
                if i >= count - 3:
                    price = base_price * 0.96  # Breakdown
                vol = 2000 if i < count - 3 else 10000
            else:
                price = base_price + (i % 7) - 3
                vol = 2000

            candles.append({
                "timestamp": ts,
                "open": price - 1.0,
                "high": price + 2.0,
                "low": price - 2.0,
                "close": price,
                "volume": vol,
            })
        return candles


class MockWatchlistItem:
    def __init__(self, id, symbol, name, sector, lot_size, is_fno, is_active, extra=None):
        self.id = id
        self.symbol = symbol
        self.name = name
        self.sector = sector
        self.lot_size = lot_size
        self.is_fno = is_fno
        self.is_active = is_active
        self.extra = extra or {}


class MockRepository:
    def __init__(self):
        self.items = {}

    async def get_active_watchlist(self):
        return [item for item in self.items.values() if item.is_active]

    async def get_watchlist_item_by_symbol(self, symbol):
        return self.items.get(symbol)

    async def add_watchlist_item(self, **kwargs):
        sym = kwargs["symbol"]
        item = MockWatchlistItem(
            id=f"id_{sym}",
            symbol=sym,
            name=kwargs.get("name", sym),
            sector=kwargs.get("sector", "Unknown"),
            lot_size=kwargs.get("lot_size", 1),
            is_fno=kwargs.get("is_fno", True),
            is_active=kwargs.get("is_active", True),
            extra=kwargs.get("extra", {}),
        )
        self.items[sym] = item
        return item

    async def update_watchlist_item(self, item_id, **kwargs):
        for sym, item in self.items.items():
            if item.id == item_id:
                for k, v in kwargs.items():
                    setattr(item, k, v)
                return item
        return None


def test_watchlist_builder_returns_top_10_no_duplicates():
    builder = WatchlistBuilder()
    symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK", "MARUTI", "SUNPHARMA"]
    trends = {
        "RELIANCE": "strong_bull",
        "TCS": "squeeze_bull",
        "INFY": "strong_bear",
        "HDFCBANK": "strong_bull",
    }
    feed = MockFeed(trends)

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(
            builder.build_daily_watchlist(
                feed=feed,
                regime="Sideways",
                candidate_symbols=symbols,
                final_top_n=10,
            )
        )
    finally:
        loop.close()

    assert len(results) == 10
    res_symbols = [r["symbol"] for r in results]
    assert len(set(res_symbols)) == 10
    # Every returned element has valid fields
    for r in results:
        assert "symbol" in r
        assert "score" in r
        assert r["score"] > 0
        assert "is_fno" in r


def test_different_synthetic_data_produces_different_top_10():
    builder = WatchlistBuilder()
    symbols = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
        "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK",
        "MARUTI", "SUNPHARMA", "TATAMOTORS", "WIPRO", "TITAN"
    ]

    # Dataset A: RELIANCE & TCS strong
    feed_a = MockFeed({"RELIANCE": "strong_bull", "TCS": "strong_bull"})
    # Dataset B: WIPRO & TITAN strong
    feed_b = MockFeed({"WIPRO": "strong_bull", "TITAN": "strong_bull"})

    loop = asyncio.new_event_loop()
    try:
        results_a = loop.run_until_complete(
            builder.build_daily_watchlist(feed=feed_a, regime="Sideways", candidate_symbols=symbols, final_top_n=10)
        )
        results_b = loop.run_until_complete(
            builder.build_daily_watchlist(feed=feed_b, regime="Sideways", candidate_symbols=symbols, final_top_n=10)
        )
    finally:
        loop.close()

    symbols_a = [r["symbol"] for r in results_a]
    symbols_b = [r["symbol"] for r in results_b]

    # Confirm results are dynamic and vary based on incoming data
    assert symbols_a != symbols_b
    assert "RELIANCE" in symbols_a[:3]
    assert "WIPRO" in symbols_b[:3]


def test_regime_directional_bias():
    builder = WatchlistBuilder()
    candidates = [
        {"symbol": "BULL_STOCK", "score": 0.60, "bias": "BUY", "setup_type": "bb_squeeze_breakout_bullish", "details": {"price_change_pct": 2.5}},
        {"symbol": "BEAR_STOCK", "score": 0.60, "bias": "SELL", "setup_type": "bb_squeeze_breakout_bearish", "details": {"price_change_pct": -2.5}},
    ]

    # 1. Bull regime: Bullish stock boosted, Bearish penalized
    bull_biased = builder.apply_regime_bias(candidates, regime="Bull")
    assert bull_biased[0]["symbol"] == "BULL_STOCK"
    assert bull_biased[0]["score"] > bull_biased[1]["score"]
    assert bull_biased[0]["score"] == round(0.60 * 1.25, 3)
    assert bull_biased[1]["score"] == round(0.60 * 0.75, 3)

    # 2. Bear regime: Bearish stock boosted, Bullish penalized
    bear_biased = builder.apply_regime_bias(candidates, regime="Bear")
    assert bear_biased[0]["symbol"] == "BEAR_STOCK"
    assert bear_biased[0]["score"] > bear_biased[1]["score"]
    assert bear_biased[0]["score"] == round(0.60 * 1.25, 3)
    assert bear_biased[1]["score"] == round(0.60 * 0.75, 3)

    # 3. Sideways regime: Neutral scores
    sideways_biased = builder.apply_regime_bias(candidates, regime="Sideways")
    assert sideways_biased[0]["score"] == 0.60
    assert sideways_biased[1]["score"] == 0.60


def test_scheduler_pre_market_init_populates_db():
    repo = MockRepository()
    mock_engine = MagicMock()
    mock_engine.feed = MockFeed({"RELIANCE": "strong_bull", "INFY": "squeeze_bull"})
    mock_engine.current_regime = "Bull"
    mock_engine._broadcast = AsyncMock()

    async def get_repo():
        return repo

    scheduler = MarketLifecycleScheduler(engine=mock_engine, repository_getter=get_repo)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(scheduler.on_pre_market_init(force=True))
        active_items = loop.run_until_complete(repo.get_active_watchlist())
    finally:
        loop.close()

    # Confirm the configured top-N active items populated in DB with no
    # manual intervention (P2-d: final_top_n=20 in defaults.yaml).
    assert len(active_items) == 20
    for item in active_items:
        assert item.is_active is True
        assert item.symbol is not None
        assert item.lot_size >= 1

    # Confirm WebSocket broadcast fired
    assert mock_engine._broadcast.called
