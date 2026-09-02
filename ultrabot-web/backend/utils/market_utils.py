"""F&O stock universe, sector mapping, and market utility functions for NSE.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional


# ────────────────────────────────────────────────────────────────
# F&O Stock Universe (major NSE F&O stocks)
#
# Instrument hygiene (Phase 5, verified 2026-08-27 against live NSE /
# Zerodha reference data — see evidence/p5_universe_liveness.json):
#   * TATAMOTORS removed — delisted after the Oct-2025 demerger.
#     Successors added: TMPV (F&O, lot 1600, inherited NSE token 3456)
#     and TMCV (NSE cash listing only, no F&O yet — lot_size 1).
#   * All lot sizes refreshed to the current NSE F&O market lots
#     (41 of 43 had drifted after NSE's periodic lot-size revisions).
# ────────────────────────────────────────────────────────────────

FNO_UNIVERSE: List[Dict[str, str | int]] = [
    {"symbol": "RELIANCE", "name": "Reliance Industries Ltd", "sector": "Energy", "lot_size": 500},
    {"symbol": "TCS", "name": "Tata Consultancy Services Ltd", "sector": "IT", "lot_size": 225},
    {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "sector": "Banking", "lot_size": 650},
    {"symbol": "INFY", "name": "Infosys Ltd", "sector": "IT", "lot_size": 400},
    {"symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "sector": "Banking", "lot_size": 700},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever Ltd", "sector": "FMCG", "lot_size": 300},
    {"symbol": "SBIN", "name": "State Bank of India", "sector": "Banking", "lot_size": 750},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel Ltd", "sector": "Telecom", "lot_size": 475},
    {"symbol": "ITC", "name": "ITC Ltd", "sector": "FMCG", "lot_size": 1725},
    {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank Ltd", "sector": "Banking", "lot_size": 2000},
    {"symbol": "LT", "name": "Larsen & Toubro Ltd", "sector": "Infrastructure", "lot_size": 175},
    {"symbol": "AXISBANK", "name": "Axis Bank Ltd", "sector": "Banking", "lot_size": 625},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance Ltd", "sector": "Finance", "lot_size": 750},
    {"symbol": "MARUTI", "name": "Maruti Suzuki India Ltd", "sector": "Auto", "lot_size": 50},
    {"symbol": "TITAN", "name": "Titan Company Ltd", "sector": "Consumer", "lot_size": 175},
    {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical Industries Ltd", "sector": "Pharma", "lot_size": 350},
    {"symbol": "TMPV", "name": "Tata Motors Passenger Vehicles Ltd", "sector": "Auto", "lot_size": 1600},
    {"symbol": "TMCV", "name": "Tata Motors Commercial Vehicles Ltd", "sector": "Auto", "lot_size": 1},
    {"symbol": "WIPRO", "name": "Wipro Ltd", "sector": "IT", "lot_size": 3000},
    {"symbol": "ULTRACEMCO", "name": "UltraTech Cement Ltd", "sector": "Cement", "lot_size": 50},
    {"symbol": "ADANIENT", "name": "Adani Enterprises Ltd", "sector": "Conglomerate", "lot_size": 309},
    {"symbol": "TATASTEEL", "name": "Tata Steel Ltd", "sector": "Metals", "lot_size": 2750},
    {"symbol": "POWERGRID", "name": "Power Grid Corporation of India Ltd", "sector": "Power", "lot_size": 1900},
    {"symbol": "NTPC", "name": "NTPC Ltd", "sector": "Power", "lot_size": 1500},
    {"symbol": "HCLTECH", "name": "HCL Technologies Ltd", "sector": "IT", "lot_size": 400},
    {"symbol": "ASIANPAINT", "name": "Asian Paints Ltd", "sector": "Consumer", "lot_size": 250},
    {"symbol": "BAJAJFINSV", "name": "Bajaj Finserv Ltd", "sector": "Finance", "lot_size": 300},
    {"symbol": "DRREDDY", "name": "Dr Reddy's Laboratories Ltd", "sector": "Pharma", "lot_size": 625},
    {"symbol": "CIPLA", "name": "Cipla Ltd", "sector": "Pharma", "lot_size": 425},
    {"symbol": "ONGC", "name": "Oil & Natural Gas Corporation Ltd", "sector": "Energy", "lot_size": 2250},
    {"symbol": "COALINDIA", "name": "Coal India Ltd", "sector": "Mining", "lot_size": 1350},
    {"symbol": "JSWSTEEL", "name": "JSW Steel Ltd", "sector": "Metals", "lot_size": 675},
    {"symbol": "INDUSINDBK", "name": "IndusInd Bank Ltd", "sector": "Banking", "lot_size": 700},
    {"symbol": "HINDALCO", "name": "Hindalco Industries Ltd", "sector": "Metals", "lot_size": 700},
    {"symbol": "GRASIM", "name": "Grasim Industries Ltd", "sector": "Cement", "lot_size": 250},
    {"symbol": "TECHM", "name": "Tech Mahindra Ltd", "sector": "IT", "lot_size": 600},
    {"symbol": "EICHERMOT", "name": "Eicher Motors Ltd", "sector": "Auto", "lot_size": 100},
    {"symbol": "M&M", "name": "Mahindra & Mahindra Ltd", "sector": "Auto", "lot_size": 200},
    {"symbol": "DIVISLAB", "name": "Divi's Laboratories Ltd", "sector": "Pharma", "lot_size": 100},
    {"symbol": "BPCL", "name": "Bharat Petroleum Corporation Ltd", "sector": "Energy", "lot_size": 1975},
    {"symbol": "HEROMOTOCO", "name": "Hero Motocorp Ltd", "sector": "Auto", "lot_size": 150},
    {"symbol": "APOLLOHOSP", "name": "Apollo Hospitals Enterprise Ltd", "sector": "Healthcare", "lot_size": 125},
    {"symbol": "BRITANNIA", "name": "Britannia Industries Ltd", "sector": "FMCG", "lot_size": 125},
    {"symbol": "HINDPETRO", "name": "HPCL", "sector": "Energy", "lot_size": 2025},
    {"symbol": "SBILIFE", "name": "SBI Life Insurance Company Ltd", "sector": "Insurance", "lot_size": 375},
    {"symbol": "TATACONSUM", "name": "Tata Consumer Products Ltd", "sector": "FMCG", "lot_size": 550},
    {"symbol": "DABUR", "name": "Dabur India Ltd", "sector": "FMCG", "lot_size": 1250},
    {"symbol": "PIDILITIND", "name": "Pidilite Industries Ltd", "sector": "Chemicals", "lot_size": 500},
    {"symbol": "VEDL", "name": "Vedanta Ltd", "sector": "Metals", "lot_size": 1150},
    {"symbol": "ADANIPORTS", "name": "Adani Ports & SEZ Ltd", "sector": "Infrastructure", "lot_size": 475},
    {"symbol": "AMBUJACEM", "name": "Ambuja Cements Ltd", "sector": "Cement", "lot_size": 1200},
]

# v0.4.8: prefer an NSE-verified generated universe (scripts/rebuild_fno_universe.py)
# over the curated built-in list. The generated module carries the full F&O
# underlyings with current lot sizes from NSE's fo_mktlots.csv; the built-in
# list above remains the safe fallback when the generated file is absent.
try:  # pragma: no cover - depends on optional generated file
    from utils.fno_universe_generated import FNO_UNIVERSE_GENERATED as _GEN_UNIVERSE
    if _GEN_UNIVERSE and len(_GEN_UNIVERSE) >= 120:
        _BUILTIN_UNIVERSE = list(FNO_UNIVERSE)
        _BUILTIN_META = {s["symbol"]: s for s in _BUILTIN_UNIVERSE}
        FNO_UNIVERSE: List[Dict[str, str | int]] = []
        for _entry in _GEN_UNIVERSE:
            _meta = _BUILTIN_META.get(_entry["symbol"])
            if _meta:
                # keep rich name/sector metadata, trust NSE lot size
                FNO_UNIVERSE.append({
                    "symbol": _entry["symbol"],
                    "name": _meta["name"],
                    "sector": _meta["sector"],
                    "lot_size": _entry["lot_size"],
                })
            else:
                FNO_UNIVERSE.append(dict(_entry))
    else:
        raise ValueError("generated universe too small")
except Exception:
    pass  # no generated file (or unusable) → built-in list stands

# Build lookup dicts
_SYMBOL_MAP: Dict[str, Dict] = {s["symbol"]: s for s in FNO_UNIVERSE}
_SECTOR_MAP: Dict[str, str] = {s["symbol"]: s["sector"] for s in FNO_UNIVERSE}
_LOT_SIZE_MAP: Dict[str, int] = {s["symbol"]: s["lot_size"] for s in FNO_UNIVERSE}
_FNO_SYMBOLS: set = set(_SYMBOL_MAP.keys())


# ────────────────────────────────────────────────────────────────
# Sector grouping
# ────────────────────────────────────────────────────────────────

def get_sectors() -> Dict[str, List[str]]:
    """Return a dict mapping sector name to list of symbols."""
    sectors: Dict[str, List[str]] = {}
    for stock in FNO_UNIVERSE:
        sec = stock["sector"]
        sectors.setdefault(sec, []).append(stock["symbol"])
    return sectors


# ────────────────────────────────────────────────────────────────
# Public helpers
# ────────────────────────────────────────────────────────────────

def is_fno_stock(symbol: str) -> bool:
    """Check if a symbol is part of the F&O universe."""
    return symbol.upper() in _FNO_SYMBOLS


def get_stock_sector(symbol: str) -> str:
    """Get the sector for a stock. Returns 'Unknown' if not found."""
    return _SECTOR_MAP.get(symbol.upper(), "Unknown")


def get_lot_size(symbol: str) -> int:
    """Get the F&O lot size for a stock. Returns 1 if not found."""
    return _LOT_SIZE_MAP.get(symbol.upper(), 1)


def get_stock_info(symbol: str) -> Optional[Dict]:
    """Get full stock info dict for a symbol. Returns None if not found."""
    return _SYMBOL_MAP.get(symbol.upper())


def get_symbols_by_sector(sector: str) -> List[str]:
    """Get all F&O symbols in a given sector."""
    return [s["symbol"] for s in FNO_UNIVERSE if s["sector"] == sector]


def get_all_fno_symbols() -> List[str]:
    """Get all F&O symbols as a sorted list."""
    return sorted(_FNO_SYMBOLS)


# ────────────────────────────────────────────────────────────────
# Instrument hygiene (Phase 5)
# ────────────────────────────────────────────────────────────────

# Symbols verified dead / delisted / renamed on 2026-08-27 against live
# NSE / Zerodha reference data. Do NOT reintroduce any of these into the
# universe, broker token maps, or UI pick-lists without re-verifying:
#   TATAMOTORS — delisted Oct-2025 demerger  -> successors TMPV + TMCV
#   ZOMATO     — renamed ETERNAL (2025)
#   MCDOWELL-N — renamed UNITDSPR
#   M_M        — invalid symbol form (correct NSE symbol is M&M)
#   HDBANK     — delisted (legacy HDFC Bank alias)
#   TATAMETALI — delisted (merged into Tata Steel group)
#   LTIM       — no longer a listed symbol
DEAD_SYMBOLS: set = {
    "TATAMOTORS", "ZOMATO", "MCDOWELL-N", "M_M", "HDBANK", "TATAMETALI", "LTIM",
}


def get_last_candle_age_minutes(candles: List[Dict[str, Any]]) -> Optional[float]:
    """Return the age (in minutes) of the NEWEST candle in a candle list.

    Used by data-freshness guards to detect delisted/suspended symbols that
    still serve OLD history through the feed. Returns None when the list is
    empty or the newest timestamp is missing/unparseable — callers must
    treat None as 'unknown' and must NOT block trading on it.
    """
    if not candles:
        return None
    last = candles[-1]
    ts_raw = last.get("timestamp") if isinstance(last, dict) else getattr(last, "timestamp", None)
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(str(ts_raw))
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        return max((datetime.now() - ts).total_seconds() / 60.0, 0.0)
    except Exception:
        return None
