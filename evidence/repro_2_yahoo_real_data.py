"""REAL EVIDENCE repro 2 — Yahoo feed returns REAL market data (network, no mocks)."""
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from feeds.yahoo_historical import YahooHistoricalFeed


async def main():
    print("=" * 72)
    print("REPRO 2 — REAL Yahoo market data over live HTTP")
    print("=" * 72)
    feed = YahooHistoricalFeed()

    for sym in ["RELIANCE.NS", "TCS.NS", "^NSEI", "^INDIAVIX"]:
        try:
            ltp = await feed.get_ltp(sym)
            print(f"[LTP ] {sym:14s} -> {ltp}")
        except Exception as e:
            print(f"[LTP ] {sym:14s} -> ERROR {type(e).__name__}: {e}")

    # Candles (the exact call the scanner uses)
    try:
        candles = await feed.get_candles("RELIANCE.NS", interval="5m", period="5d")
        if candles is None or len(candles) == 0:
            print("[CNDL] RELIANCE.NS 5m/5d -> EMPTY")
        else:
            print(f"[CNDL] RELIANCE.NS 5m/5d -> {len(candles)} candles, "
                  f"last: {candles.index[-1]} close={float(candles['close'].iloc[-1]):.2f}")
    except Exception as e:
        print(f"[CNDL] RELIANCE.NS -> ERROR {type(e).__name__}: {e}")

    # FeedManager wrapping (production path)
    from feeds.feed_manager import FeedManager
    fm = FeedManager(primary=feed, backup=None)
    ltp = await fm.get_ltp("RELIANCE.NS")
    print(f"[FMGR] FeedManager.get_ltp('RELIANCE.NS') -> {ltp}")


if __name__ == "__main__":
    asyncio.run(main())
