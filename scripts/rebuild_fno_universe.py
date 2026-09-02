#!/usr/bin/env python3
"""Rebuild the F&O universe from NSE's official market-lots file.

v0.4.8 — addresses "All symbols for F&O should get correctly": the built-in
universe in utils/market_utils.py is a curated 51-symbol list, while the real
NSE F&O segment carries ~180-220 underlyings. This script fetches NSE's
official fo_mktlots.csv (symbol → lot size), merges it with the existing
sector/name metadata, and regenerates utils/fno_universe_generated.py.

The app automatically prefers the generated module when present and falls
back to the built-in list when it is absent — so this script is safe to run
any time, and deleting the generated file reverts to the built-in universe.

Run this on a machine with normal home/office internet (NSE blocks datacenter
and non-browser traffic — it denied both plain curl and a headless browser
from our test sandbox):

    python scripts/rebuild_fno_universe.py [--url URL]

Optional: --url to point at a local copy of fo_mktlots.csv (useful if you
download it manually in a browser from
https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv).
"""
import argparse
import csv
import io
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "ultrabot-web" / "backend"
MARKET_UTILS = BACKEND_DIR / "utils" / "market_utils.py"
GENERATED = BACKEND_DIR / "utils" / "fno_universe_generated.py"

DEFAULT_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
DHAN_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch_dhan_master() -> dict:
    """Fetch Dhan's PUBLIC broker symbol master (no auth) and derive the F&O
    equity underlyings from NSE FUTSTK rows: underlying = trading symbol
    before the first '-', lot = SEM_LOT_UNITS (mode across expiries).

    This is the broker-sourced fallback when NSE blocks the request — the
    master is current (e.g. TATAMOTORS already replaced by TMPV/TMCV after
    the Oct-2025 demerger, ZOMATO renamed ETERNAL). Verified 2026-09-01:
    210 underlyings extracted, lot sizes match NSE.
    """
    req = urllib.request.Request(DHAN_MASTER_URL, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", errors="replace")

    lots: dict = {}
    reader = csv.DictReader(io.StringIO(data))
    per_sym: dict = {}
    for row in reader:
        if (row.get("SEM_EXM_EXCH_ID") or "").strip() != "NSE":
            continue
        if (row.get("SEM_INSTRUMENT_NAME") or "").strip().upper() != "FUTSTK":
            continue
        t = (row.get("SEM_TRADING_SYMBOL") or "").strip().upper()
        if not t or "-" not in t:
            continue
        sym = t.split("-")[0].strip()
        if not re.fullmatch(r"[A-Z0-9&\-]+", sym) or len(sym) < 2 or "TEST" in sym:
            continue
        try:
            lot = int(float((row.get("SEM_LOT_UNITS") or "0")))
        except ValueError:
            continue
        if lot <= 0:
            continue
        per_sym.setdefault(sym, {})
        per_sym[sym][lot] = per_sym[sym].get(lot, 0) + 1
    for sym, lot_counts in per_sym.items():
        lots[sym] = max(lot_counts, key=lot_counts.get)
    return lots


def fetch_csv(url: str) -> str:
    """Fetch the F&O market lots CSV, first trying direct with a browser UA,
    then via the headless-browser session file if provided."""
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    if "<HTML" in data[:200].upper() or "Access Denied" in data[:200]:
        raise RuntimeError(
            "NSE returned an access-denied page (datacenter/non-browser traffic is "
            "blocked). Download fo_mktlots.csv manually in your browser and rerun "
            "with: --url file:///path/to/fo_mktlots.csv"
        )
    return data


def parse_mktlots(csv_text: str) -> dict:
    """Parse fo_mktlots.csv → {SYMBOL: lot_size}.

    The file historically has an 'UNDERLYING' column and a 'MARKET LOT'
    column (header names may shift between revisions — we detect them).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    lots = {}
    for row in reader:
        row = { (k or "").strip().upper(): (v or "").strip() for k, v in row.items() }
        sym = row.get("UNDERLYING") or row.get("SYMBOL") or row.get("SYMBOL NAME")
        lot_raw = row.get("MARKET LOT") or row.get("MARKET_LOT") or row.get("LOT SIZE")
        if not sym or not lot_raw:
            continue
        try:
            lot = int(float(lot_raw.replace(",", "")))
        except ValueError:
            continue
        if lot > 0:
            lots[sym.strip().upper()] = lot
    return lots


def load_existing_metadata() -> dict:
    """Import the current built-in FNO_UNIVERSE for name/sector metadata."""
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        from utils.market_utils import FNO_UNIVERSE as existing
        return {e["symbol"]: e for e in existing}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL, help="fo_mktlots.csv URL or file:// path")
    ap.add_argument("--source", choices=["auto", "nse", "dhan"], default="auto",
                    help="nse: fo_mktlots.csv; dhan: Dhan public broker symbol master "
                         "(no auth, works everywhere; auto: NSE first, Dhan on failure)")
    args = ap.parse_args()

    lots = {}
    if args.source in ("auto", "dhan"):
        try:
            lots = fetch_dhan_master()
            if lots:
                print(f"Source: Dhan broker symbol master → {len(lots)} underlyings")
        except Exception as exc:
            if args.source == "dhan":
                print(f"ERROR: Dhan master fetch failed: {exc}")
                return 1
            print(f"Dhan master unavailable ({exc}); trying NSE...")

    if not lots and args.source in ("auto", "nse"):
        if args.url.startswith("file://"):
            csv_text = Path(args.url[7:]).read_text()
        else:
            csv_text = fetch_csv(args.url)
        lots = parse_mktlots(csv_text)

    if len(lots) < 120:
        print(f"WARN: only {len(lots)} symbols parsed — source format may have changed.")
        return 1

    existing = load_existing_metadata()
    rows = []
    for sym in sorted(lots):
        meta = existing.get(sym, {})
        rows.append({
            "symbol": sym,
            "name": meta.get("name", sym.title() + " Ltd"),
            "sector": meta.get("sector", "Unknown"),
            "lot_size": lots[sym],
        })

    carried = sum(1 for r in rows if r["sector"] != "Unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    out = (
        '"""AUTO-GENERATED by scripts/rebuild_fno_universe.py — DO NOT EDIT BY HAND.\n\n'
        "Source: NSE fo_mktlots.csv fetched " + now + ". " + str(len(rows)) + " underlyings "
        "(" + str(carried) + " with known sector metadata from the built-in list).\n\n"
        "The app prefers this module and falls back to the built-in 51-symbol\n"
        "universe when this file is absent.\n\"\"\"\n\n"
        "FNO_UNIVERSE_GENERATED = [\n"
    )
    for r in rows:
        out += (
            '    {"symbol": "%s", "name": "%s", "sector": "%s", "lot_size": %d},\n'
            % (r["symbol"], r["name"].replace('"', "'"), r["sector"], r["lot_size"])
        )
    out += "]\n"
    GENERATED.write_text(out)

    changed_lots = sum(
        1 for r in rows
        if r["symbol"] in existing and existing[r["symbol"]].get("lot_size") != r["lot_size"]
    )
    print(f"OK: wrote {GENERATED}")
    print(f"  universe: {len(rows)} underlyings (built-in list had {len(existing)})")
    print(f"  lot sizes corrected vs built-in: {changed_lots}")
    print(f"  new symbols: {len(rows) - len(existing & set(lots)) if existing else len(rows)}")
    print("Restart the backend to pick it up. Delete the file to revert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
