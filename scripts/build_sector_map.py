#!/usr/bin/env python3
"""Build the dynamic sector map + regenerate the F&O universe (v0.4.11).

Fixes the G2 sector-attribution flow break: the runtime universe grew to
~210 F&O underlyings but only ~50 carried sector metadata, so get_stock_sector()
returned "Unknown" for most live signals (COLPAL/TRENT/KALYANKJIL on Fri
2026-09-04) and G2 lumped all Unknowns into one false concentration bucket.

Data sources (all public, no auth, fetched fresh at build time — nothing
hardcoded, nothing stale):
  * Universe + lot sizes : Dhan public broker symbol master
                           (images.dhan.co/api-data/api-scrip-master.csv),
                           NSE FUTSTK underlyings, lot = mode across expiries.
  * Sector + industry    : TradingView India scanner (bulk POST, one call per
                           ~50 symbols) — current sector taxonomy per symbol.
  * Cash-only listings   : preserved separately (TMCV — listed on NSE cash,
                           no F&O series yet) so universe hygiene tests stay
                           green and options paths (v0.5.0) can exclude them.

Outputs:
  * ultrabot-web/backend/utils/fno_universe_generated.py  (universe incl. sectors)
  * ultrabot-web/backend/config/sector_map.json           (manifest + overrides)

Run:  python scripts/build_sector_map.py [--skip-universe] [--tv-timeout 20]
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rebuild_fno_universe import fetch_dhan_master, load_existing_metadata  # noqa: E402

BACKEND_DIR = REPO_ROOT / "ultrabot-web" / "backend"
GENERATED_PY = BACKEND_DIR / "utils" / "fno_universe_generated.py"
SECTOR_MAP_JSON = BACKEND_DIR / "config" / "sector_map.json"

TV_URL = "https://scanner.tradingview.com/india/scan"
TV_CHUNK = 50
TV_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Cash-only NSE listings kept in the universe for scanning/news/G2 attribution
# but excluded from F&O tradeability (no derivative series exists yet).
CASH_ONLY_UNIVERSE = [
    {"symbol": "TMCV", "name": "Tata Motors Commercial Vehicles Ltd",
     "sector": "Auto", "industry": "Automobile Manufacturers", "lot_size": 1},
]

# Dhan-master underlying -> TradingView ticker fixes (symbol-form mismatches
# observed 2026-09-05: Dhan strips suffixes (BAJAJ-AUTO -> BAJAJ) while TV
# uses underscores (BAJAJ_AUTO)).
TV_ALIASES = {
    "BAJAJ": "BAJAJ_AUTO",
}


def fetch_tv_sectors(symbols: list, timeout: float = 20.0) -> dict:
    """Bulk-fetch {symbol: (sector, industry)} from TradingView India scanner.

    Unknown/unmatched symbols are simply absent from the result — caller
    decides the fallback. Never raises on individual chunk failure; failed
    chunks are retried once and then reported as missing.
    """
    out: dict = {}
    tickers = {f"NSE:{TV_ALIASES.get(s, s)}": s for s in symbols}
    for i in range(0, len(symbols), TV_CHUNK):
        chunk = symbols[i:i + TV_CHUNK]
        body = json.dumps({
            "symbols": {"tickers": [f"NSE:{TV_ALIASES.get(s, s)}" for s in chunk], "query": {"types": []}},
            "columns": ["name", "sector", "industry"],
        }).encode()
        rows = []
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(
                    TV_URL, data=body,
                    headers={"Content-Type": "application/json", "User-Agent": TV_UA},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    rows = json.loads(resp.read()).get("data", [])
                break
            except Exception as exc:
                if attempt == 2:
                    print(f"  TV chunk {i // TV_CHUNK + 1}: FAILED twice ({exc})", file=sys.stderr)
                else:
                    time.sleep(1.5)
        for row in rows:
            try:
                ticker = str(row.get("s", ""))  # e.g. "NSE:COLPAL"
                raw = ticker.split(":", 1)[1] if ":" in ticker else ticker
                sym = next((s for t, s in tickers.items() if t.endswith(":" + raw)), raw)
                d = row.get("d") or []
                sector = d[1] if len(d) > 1 and d[1] else None
                industry = d[2] if len(d) > 2 and d[2] else None
                if sym and sector:
                    out[sym] = (str(sector), str(industry) if industry else None)
            except Exception:
                continue
        time.sleep(0.6)
    return out


def build(lot_sizes: dict, existing_meta: dict, tv: dict) -> dict:
    """Merge universe + sector data into the generated-universe structure."""
    entries = []
    unknown = []
    for sym in sorted(lot_sizes):
        meta = existing_meta.get(sym, {})
        tv_sector, tv_industry = tv.get(sym, (None, None))
        sector = tv_sector or meta.get("sector") or "Unknown"
        industry = tv_industry or meta.get("industry") or None
        if sector == "Unknown":
            unknown.append(sym)
        entries.append({
            "symbol": sym,
            "name": meta.get("name") or sym.title(),
            "sector": sector,
            "industry": industry,
            "lot_size": int(lot_sizes[sym]),
        })

    # Cash-only listings: never in FUTSTK, always preserved.
    present = {e["symbol"] for e in entries}
    for co in CASH_ONLY_UNIVERSE:
        if co["symbol"] not in present:
            entry = dict(co)
            entry["cash_only"] = True
            entries.append(entry)
    entries.sort(key=lambda e: e["symbol"])
    return {"entries": entries, "unknown": unknown}


def emit(entries: list, source_note: str) -> None:
    lines = [
        '"""AUTO-GENERATED by scripts/build_sector_map.py — DO NOT EDIT BY HAND.',
        "",
        f"Source: {source_note}",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "Full NSE F&O underlying universe with per-symbol sector + industry",
        "(v0.4.11 dynamic sector map) plus preserved cash-only listings",
        '(flagged "cash_only": True — no F&O series exists for them yet).',
        "The app prefers this module; the built-in 51-symbol list in",
        "market_utils.py remains the fallback when this file is absent.",
        '"""',
        "",
        "FNO_UNIVERSE_GENERATED = [",
    ]
    for e in entries:
        industry = e.get("industry")
        parts = [
            f'"symbol": {json.dumps(e["symbol"])}',
            f'"name": {json.dumps(e["name"])}',
            f'"sector": {json.dumps(e["sector"])}',
            f'"industry": {json.dumps(industry) if industry is not None else "None"}',
            f'"lot_size": {e["lot_size"]}',
        ]
        if e.get("cash_only"):
            parts.append('"cash_only": True')
        lines.append("    {" + ", ".join(parts) + "},")
    lines.append("]")
    GENERATED_PY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def emit_manifest(entries: list, unknown: list, lot_source: str, tv_count: int) -> None:
    sectors = {e["symbol"]: e["sector"] for e in entries}
    industries = {e["symbol"]: e.get("industry") for e in entries if e.get("industry")}
    cash_only = [e["symbol"] for e in entries if e.get("cash_only")]
    manifest = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "universe_source": lot_source,
            "sector_source": "tradingview_india_scan",
            "counts": {
                "total": len(entries),
                "with_sector": len(entries) - len(unknown) - len(cash_only),
                "unknown": len(unknown),
                "cash_only": len(cash_only),
                "tv_sector_hits": tv_count,
            },
            "unknown_symbols": unknown,
            "cash_only_symbols": cash_only,
            "staleness_days_warn": 45,
        },
        "sectors": sectors,
        "industries": industries,
    }
    SECTOR_MAP_JSON.parent.mkdir(parents=True, exist_ok=True)
    SECTOR_MAP_JSON.write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-universe", action="store_true",
                    help="keep existing generated lot sizes, refresh sectors only")
    args = ap.parse_args()

    # 1. Universe (symbol -> lot) fresh from Dhan public master
    existing_meta = load_existing_metadata()
    if args.skip_universe:
        sys.path.insert(0, str(BACKEND_DIR))
        try:
            from utils.fno_universe_generated import FNO_UNIVERSE_GENERATED as cur
            lot_sizes = {e["symbol"]: e["lot_size"] for e in cur
                         if not e.get("cash_only")}
            lot_source = "existing_generated_file"
        except Exception:
            print("No existing generated universe to --skip-universe from", file=sys.stderr)
            return 2
    else:
        lot_sizes = fetch_dhan_master()
        lot_source = "dhan_public_master (fresh fetch)"
    if not lot_sizes:
        print("FATAL: no universe data fetched", file=sys.stderr)
        return 2
    print(f"Universe: {len(lot_sizes)} F&O underlyings from {lot_source}")

    # 2. Sectors from TradingView bulk scan
    tv = fetch_tv_sectors(sorted(lot_sizes))
    print(f"Sectors: {len(tv)}/{len(lot_sizes)} resolved from TradingView")

    # 3. Merge + emit
    merged = build(lot_sizes, existing_meta, tv)
    entries, unknown = merged["entries"], merged["unknown"]
    cash_only = [e["symbol"] for e in entries if e.get("cash_only")]
    total = len(entries)
    known = total - len(unknown) - len(cash_only)
    print(f"Merged: {total} entries | sector known {known} | unknown {len(unknown)} | cash-only {cash_only}")

    emit(entries, source_note=f"{lot_source} + tradingview_india_scan ({len(tv)} sector hits)")
    emit_manifest(entries, unknown, lot_source, len(tv))
    print(f"Wrote {GENERATED_PY.name} + {SECTOR_MAP_JSON.name}")

    if len(unknown) > total * 0.10:
        print(f"WARNING: unknown-sector rate {len(unknown)}/{total} > 10% — inspect unknown_symbols", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
