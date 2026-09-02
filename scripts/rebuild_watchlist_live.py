"""Rebuild the daily watchlist from LIVE market data (operational recovery tool).

Mirrors MarketLifecycleScheduler.on_pre_market_init's watchlist portion
(same WatchlistBuilder, same persistence semantics) so it can be run
mid-session to recover from watchlist corruption — e.g. the 2026-08-28
incident where the test suite (pre-isolation) deleted the live session's
watchlist rows while the engine was trading.

Everything is derived from the real feed at run time: the 51-symbol
candidate universe is scored by the Technical + Kronos scanners on live
candles, and the regime is classified from the live NIFTY/India-VIX
values. No hardcoded symbol lists, no synthetic prices.

Usage (from ultrabot-web/backend):
    ./venv/bin/python ../../scripts/rebuild_watchlist_live.py
"""

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from db.database import async_session_factory, init_db
    from db.repository import Repository
    from feeds.yahoo_historical import YahooHistoricalFeed
    from feeds.feed_manager import FeedManager
    from scanner.watchlist_builder import WatchlistBuilder
    from strategies.regime_detector import RegimeDetector

    await init_db()

    feed_manager = FeedManager(primary=YahooHistoricalFeed(), backup=None)
    await feed_manager.connect()

    # ── Live market context (same sources the engine uses) ──────────────
    nifty = None
    for sym in ("NIFTY", "NIFTY 50", "NSE:NIFTY50-INDEX"):
        try:
            p = await feed_manager.get_latest_price(sym)
            if p and p > 0:
                nifty = float(p)
                break
        except Exception:
            pass
    vix = None
    for sym in ("INDIAVIX", "INDIA VIX", "NSE:INDIAVIX-INDEX"):
        try:
            v = await feed_manager.get_latest_price(sym)
            if v and v > 0:
                vix = float(v)
                break
        except Exception:
            pass

    if nifty is None or vix is None:
        print("FAILED: could not fetch live NIFTY/India-VIX — refusing to "
              "build a watchlist without real market context.")
        return 1

    prev_close = None
    try:
        candles = await feed_manager.get_candles("^NSEI", "1d", 2)
        if candles is not None and len(candles) >= 2:
            prev_close = float(candles.iloc[-2]["close"])
    except Exception:
        prev_close = None
    day_change = ((nifty - prev_close) / prev_close * 100) if prev_close else 0.0

    detector = RegimeDetector()
    verdict = detector.classify(nifty_price=nifty, nifty_day_change_pct=day_change, vix=vix)
    regime = verdict.get("regime", "Sideways")
    print(f"Live context: NIFTY {nifty:.2f} ({day_change:+.2f}% vs prev close), "
          f"India-VIX {vix:.2f} -> regime {regime} (conf {verdict.get('confidence', 0):.2f})")

    # ── Build the Top-10 watchlist on live data (same call the 08:45 cron makes) ──
    builder = WatchlistBuilder()
    top_10 = await builder.build_daily_watchlist(
        feed=feed_manager,
        news_items=[],
        regime=regime,
        final_top_n=10,
    )
    if not top_10:
        print("FAILED: builder returned no symbols on live data — nothing persisted.")
        return 1

    # ── Persist (same semantics as scheduler.on_pre_market_init) ────────
    persisted = []
    session = async_session_factory()
    try:
        repo = Repository(session)
        for old in await repo.get_active_watchlist():
            await repo.update_watchlist_item(old.id, is_active=False)
        for item in top_10:
            sym = item["symbol"]
            existing = await repo.get_watchlist_item_by_symbol(sym)
            if existing:
                await repo.update_watchlist_item(
                    existing.id,
                    name=item.get("name", existing.name),
                    sector=item.get("sector", existing.sector),
                    lot_size=item.get("lot_size", existing.lot_size),
                    is_fno=item.get("is_fno", True),
                    is_active=True,
                    extra=item,
                )
            else:
                await repo.add_watchlist_item(
                    symbol=sym,
                    name=item.get("name", sym),
                    sector=item.get("sector", "Unknown"),
                    lot_size=item.get("lot_size", 1),
                    is_fno=item.get("is_fno", True),
                    is_active=True,
                    extra=item,
                )
            persisted.append(sym)
        await session.commit()
    finally:
        await session.close()

    print(f"Persisted live Top-10 watchlist (regime={regime}): {persisted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
