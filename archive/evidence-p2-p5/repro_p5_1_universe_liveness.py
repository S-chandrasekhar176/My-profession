#!/usr/bin/env python3
"""Phase 5 Evidence — Live universe liveness validation (REAL EVIDENCE, NOT DERIVED).

Validates every symbol in the canonical scanner universe (utils/market_utils.py
FNO_UNIVERSE) plus successor/rename candidates through the REAL production feed
code path (feeds/yahoo_historical.YahooHistoricalFeed.get_candles) — the exact
method core/engine.py:_scan_symbol calls every scan cycle.

Classification per symbol (daily candles, last bar age):
  LIVE   — last daily candle within 7 calendar days (weekly/holiday tolerance)
  STALE  — candles exist but last bar older than 7 days  -> delisted/suspended
  EMPTY  — feed returned no candles at all                -> dead/unresolvable

For every non-LIVE symbol we also fetch 5m x 100 candles to show exactly what
the engine scanner would see (delisted tickers can still serve OLD history —
the phantom-signal hazard).
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make backend importable
BACKEND = Path(__file__).resolve().parent.parent / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))

from feeds.yahoo_historical import YahooHistoricalFeed  # noqa: E402
from utils.market_utils import get_all_fno_symbols, FNO_UNIVERSE  # noqa: E402

STALE_DAYS = 7
EXTRA_CANDIDATES = [
    "TMPV",       # possible Tata Motors PV successor post Oct-2025 demerger
    "TMCV",       # possible Tata Motors CV successor post Oct-2025 demerger
    "ZOMATO",     # route list entry — renamed to ETERNAL in 2025?
    "ETERNAL",    # candidate new name
    "M_M",        # route-list oddity (canonical NSE symbol is M&M)
]


def _last_candle_age_days(candles):
    if not candles:
        return None, None
    last_ts = candles[-1].get("timestamp", "")
    if not last_ts:
        return None, None
    try:
        # isoformat with tz e.g. "2026-08-27T14:25:00+05:30"
        ts = datetime.fromisoformat(last_ts)
        if ts.tzinfo is not None:
            ts = ts.astimezone(tz=None).replace(tzinfo=None)
        now = datetime.now()
        return (now - ts).total_seconds() / 86400.0, last_ts
    except Exception:
        return None, last_ts


async def main():
    feed = YahooHistoricalFeed()
    symbols = get_all_fno_symbols()  # the canonical 50 (sorted)
    route_extras = [s for s in EXTRA_CANDIDATES if s not in symbols]
    all_syms = symbols + route_extras
    print(f"Validating {len(all_syms)} symbols via YahooHistoricalFeed.get_candles "
          f"(production code path) @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    sem = asyncio.Semaphore(8)
    results = {}

    async def check(sym):
        async with sem:
            daily = await feed.get_candles(sym, timeframe="1d", count=10)
            age, last_ts = _last_candle_age_days(daily)
            if not daily:
                status = "EMPTY"
            elif age is not None and age <= STALE_DAYS:
                status = "LIVE"
            else:
                status = "STALE"
            rec = {
                "status": status,
                "daily_bars": len(daily),
                "last_daily_ts": last_ts,
                "age_days": round(age, 1) if age is not None else None,
                "last_close": daily[-1].get("close") if daily else None,
                "intraday_5m_bars": None,
                "intraday_last_ts": None,
            }
            # For non-live symbols, show what the ENGINE scanner sees (5m x 100)
            if status != "LIVE":
                intr = await feed.get_candles(sym, timeframe="5m", count=100)
                rec["intraday_5m_bars"] = len(intr)
                rec["intraday_last_ts"] = intr[-1].get("timestamp") if intr else None
            results[sym] = rec

    await asyncio.gather(*[check(s) for s in all_syms])

    live = sorted([s for s, r in results.items() if r["status"] == "LIVE"])
    stale = sorted([s for s, r in results.items() if r["status"] == "STALE"])
    empty = sorted([s for s, r in results.items() if r["status"] == "EMPTY"])

    print(f"\n--- LIVE ({len(live)}/{len(all_syms)}) ---")
    print(", ".join(live))

    print(f"\n--- STALE: candles exist but last bar older than {STALE_DAYS} days ({len(stale)}) ---")
    for s in stale:
        r = results[s]
        print(f"  {s:<14} last daily bar: {r['last_daily_ts']}  (age {r['age_days']}d, close={r['last_close']})")
        print(f"  {'':<14} engine 5m view: {r['intraday_5m_bars']} bars, last={r['intraday_last_ts']}")

    print(f"\n--- EMPTY: no candles at all ({len(empty)}) ---")
    for s in empty:
        r = results[s]
        print(f"  {s:<14} daily_bars={r['daily_bars']}  engine 5m view: {r['intraday_5m_bars']} bars")

    # Candidate successors detail
    print("\n--- SUCCESSOR/RENAME CANDIDATES (detail) ---")
    for s in ["TATAMOTORS", "TMPV", "TMCV", "ZOMATO", "ETERNAL", "M_M", "M&M"]:
        if s in results:
            r = results[s]
            print(f"  {s:<12} {r['status']:<6} daily_bars={r['daily_bars']:<3} "
                  f"last={str(r['last_daily_ts'])[:19]:<20} close={r['last_close']}")

    out = {
        "generated_at": datetime.now().isoformat(),
        "canonical_universe_size": len(FNO_UNIVERSE),
        "live": live, "stale": stale, "empty": empty,
        "detail": results,
    }
    outp = Path(__file__).parent / "p5_universe_liveness.json"
    outp.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved: {outp}")


if __name__ == "__main__":
    asyncio.run(main())
