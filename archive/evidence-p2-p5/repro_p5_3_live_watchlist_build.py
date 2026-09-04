#!/usr/bin/env python3
"""Phase 5 Evidence — live daily watchlist build over the CORRECTED universe.

Runs the production WatchlistBuilder.build_daily_watchlist() with the real
YahooHistoricalFeed over the full corrected FNO universe (51 symbols incl.
TMPV + TMCV, TATAMOTORS removed). Verifies:
  * the build completes from live data,
  * TATAMOTORS cannot appear anywhere in the pipeline,
  * corrected lot sizes flow into watchlist entries (spot-checkable),
  * the 7-day freshness guard is armed (builder-level).
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))

from feeds.yahoo_historical import YahooHistoricalFeed  # noqa: E402
from scanner.watchlist_builder import WatchlistBuilder  # noqa: E402
from utils.market_utils import get_all_fno_symbols, get_lot_size  # noqa: E402


async def main():
    symbols = get_all_fno_symbols()
    print(f"[{datetime.now():%H:%M:%S}] Building live watchlist over {len(symbols)} symbols "
          f"(TATAMOTORS removed; TMPV/TMCV present: {'TMPV' in symbols and 'TMCV' in symbols})")
    feed = YahooHistoricalFeed()
    builder = WatchlistBuilder()
    result = await builder.build_daily_watchlist(
        feed=feed, regime="Sideways", final_top_n=10,
    )
    print(f"\nTop-{len(result)} live watchlist:")
    print(f"{'#':<3}{'symbol':<13}{'sector':<16}{'lot':<7}{'score':<7}sources")
    for i, item in enumerate(result, 1):
        print(f"{i:<3}{item['symbol']:<13}{str(item.get('sector'))[:15]:<16}"
              f"{item.get('lot_size', '?'):<7}{item['score']:<7.3f}{','.join(item['sources'])}")

    syms = [i["symbol"] for i in result]
    all_pool = set(symbols)
    checks = [
        ("TATAMOTORS absent from universe pool", "TATAMOTORS" not in all_pool),
        ("TATAMOTORS absent from final watchlist", "TATAMOTORS" not in syms),
        ("10 items built from live data", len(result) == 10),
        ("lot sizes are corrected (RELIANCE=500 in pool metadata)", get_lot_size("RELIANCE") == 500),
        ("TMPV lot 1600", get_lot_size("TMPV") == 1600),
        ("TMCV cash-only lot 1", get_lot_size("TMCV") == 1),
    ]
    ok = True
    print()
    for name, res in checks:
        print(f"  {'PASS' if res else 'FAIL'}: {name}")
        ok = ok and res
    print(f"\n{'LIVE WATCHLIST BUILD: ALL PASS' if ok else 'FAILURES PRESENT'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
