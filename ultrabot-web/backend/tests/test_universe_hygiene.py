"""Phase 5 regression guards — instrument / universe hygiene.

These tests lock in the fixes from the 2026-08-27 instrument-hygiene audit
(evidence/p5_universe_liveness.json + live NSE/Zerodha reference data):

1. No dead/delisted symbols anywhere they can reach the trading path
   (core universe, broker token maps, API universe route, G6 pairs).
2. The Tata Motors demerger successors (TMPV + TMCV) are present.
3. F&O lot sizes match the verified reference snapshot — NSE revises lot
   sizes at semi-annual reviews; when that happens this tripwire fails on
   purpose and FNO_UNIVERSE must be refreshed (that is the feature, not a
   bug: silent lot-size drift corrupts options capital math).
"""
import re
from pathlib import Path

from utils.market_utils import (
    DEAD_SYMBOLS,
    FNO_UNIVERSE,
    get_all_fno_symbols,
    get_lot_size,
)
from risk.gates import g6_correlation_check

BACKEND_DIR = Path(__file__).resolve().parent.parent

# NSE F&O market lots as verified against live NSE/Zerodha reference data on
# 2026-08-27 (nearest-expiry FUT contracts; e.g. TMPV26SEPFUT lot 1600).
_REFERENCE_LOT_SIZES = {
    "RELIANCE": 500, "TCS": 225, "HDFCBANK": 650, "INFY": 400,
    "ICICIBANK": 700, "HINDUNILVR": 300, "SBIN": 750, "BHARTIARTL": 475,
    "ITC": 1725, "KOTAKBANK": 2000, "LT": 175, "AXISBANK": 625,
    "BAJFINANCE": 750, "MARUTI": 50, "TITAN": 175, "SUNPHARMA": 350,
    "TMPV": 1600, "WIPRO": 3000, "ULTRACEMCO": 50, "ADANIENT": 309,
    "TATASTEEL": 2750, "POWERGRID": 1900, "NTPC": 1500, "HCLTECH": 400,
    "ASIANPAINT": 250, "BAJAJFINSV": 300, "DRREDDY": 625, "CIPLA": 425,
    "ONGC": 2250, "COALINDIA": 1350, "JSWSTEEL": 675, "INDUSINDBK": 700,
    "HINDALCO": 700, "GRASIM": 250, "TECHM": 600, "EICHERMOT": 100,
    "M&M": 200, "DIVISLAB": 100, "BPCL": 1975, "HEROMOTOCO": 150,
    "APOLLOHOSP": 125, "BRITANNIA": 125, "HINDPETRO": 2025, "SBILIFE": 375,
    "TATACONSUM": 550, "DABUR": 1250, "PIDILITIND": 500, "VEDL": 1150,
    "ADANIPORTS": 475, "AMBUJACEM": 1200,
}


def _token_map_sources():
    for name in ("shoonya", "angel_one", "dhan"):
        path = BACKEND_DIR / "brokers" / f"{name}.py"
        if path.exists():
            yield name, path.read_text()


class TestCoreUniverseHygiene:
    def test_no_dead_symbols_in_core_universe(self):
        symbols = {s["symbol"] for s in FNO_UNIVERSE}
        dead = symbols & DEAD_SYMBOLS
        assert not dead, f"Dead/delisted symbols reintroduced into FNO_UNIVERSE: {dead}"

    def test_tata_demerger_successors_present(self):
        by_symbol = {s["symbol"]: s for s in FNO_UNIVERSE}
        assert "TMPV" in by_symbol, "TMPV (Tata Motors PV successor) missing from universe"
        assert "TMCV" in by_symbol, "TMCV (Tata Motors CV successor) missing from universe"
        assert by_symbol["TMPV"]["lot_size"] == 1600
        # TMCV is a cash-only listing (no F&O yet) — lot_size 1 by convention.
        assert by_symbol["TMCV"]["lot_size"] == 1

    def test_symbols_unique_and_lots_valid(self):
        symbols = [s["symbol"] for s in FNO_UNIVERSE]
        assert len(symbols) == len(set(symbols)), "Duplicate symbols in FNO_UNIVERSE"
        for s in FNO_UNIVERSE:
            assert isinstance(s["lot_size"], int) and s["lot_size"] >= 1, s
            assert s.get("name") and s.get("sector"), s

    def test_lot_sizes_match_reference_snapshot(self):
        """Tripwire: fails when NSE revises lot sizes — refresh FNO_UNIVERSE then."""
        for s in FNO_UNIVERSE:
            expected = _REFERENCE_LOT_SIZES.get(s["symbol"])
            if expected is not None:
                assert s["lot_size"] == expected, (
                    f"{s['symbol']} lot_size {s['lot_size']} != verified {expected}. "
                    "NSE may have revised market lots — re-verify against live "
                    "reference data and update FNO_UNIVERSE + this snapshot."
                )

    def test_get_all_fno_symbols_consistent(self):
        syms = get_all_fno_symbols()
        assert set(syms) == {s["symbol"] for s in FNO_UNIVERSE}
        assert "TATAMOTORS" not in syms


class TestBrokerTokenMapHygiene:
    def test_no_dead_symbols_in_token_maps(self):
        for name, src in _token_map_sources():
            for dead in DEAD_SYMBOLS:
                assert f'"{dead}":' not in src, (
                    f"brokers/{name}.py still maps dead symbol {dead}"
                )

    def test_successor_tokens_present(self):
        # Verified NSE scrip codes (2026-08-27): TMPV inherited TATAMOTORS'
        # token 3456; TMCV listed under new token 759782.
        for name, src in _token_map_sources():
            assert '"TMPV": "3456"' in src, f"brokers/{name}.py missing TMPV token"
            assert '"TMCV": "759782"' in src, f"brokers/{name}.py missing TMCV token"

    def test_token_values_are_numeric(self):
        for name, src in _token_map_sources():
            for sym, tok in re.findall(r'"([A-Z&\-^_]+)":\s*"(\d+)"', src):
                assert tok.isdigit(), f"{name}: non-numeric token for {sym}"


class TestApiUniverseRouteHygiene:
    def test_extra_picks_have_no_dead_symbols(self):
        from api.routes.watchlist import _EXTRA_PICKS
        for sym, _name in _EXTRA_PICKS:
            assert sym not in DEAD_SYMBOLS, f"Dead symbol {sym} in /universe extras"

    def test_extra_picks_do_not_overlap_core(self):
        from api.routes.watchlist import _EXTRA_PICKS
        core = {s["symbol"] for s in FNO_UNIVERSE}
        extras = [sym for sym, _ in _EXTRA_PICKS]
        assert not (set(extras) & core), "Extras duplicate core universe symbols"
        assert len(extras) == len(set(extras)), "Duplicate extras"

    def test_renamed_symbols_mapped_to_new_tickers(self):
        from api.routes.watchlist import _EXTRA_PICKS
        extras = {sym for sym, _ in _EXTRA_PICKS}
        assert "ETERNAL" in extras, "ETERNAL (renamed ZOMATO) should be a pick"
        assert "UNITDSPR" in extras, "UNITDSPR (renamed MCDOWELL-N) should be a pick"


class TestG6CorrelationPairsHygiene:
    def test_no_dead_symbols_in_correlation_pairs(self):
        for pair in g6_correlation_check._PAIR_CORRELATIONS:
            dead = set(pair) & DEAD_SYMBOLS
            assert not dead, f"Dead symbols {dead} in G6 correlation pairs"

    def test_successor_pairs_present(self):
        pairs = set(g6_correlation_check._PAIR_CORRELATIONS)
        assert frozenset({"TMPV", "MARUTI"}) in pairs
        assert frozenset({"M&M", "TMPV"}) in pairs
