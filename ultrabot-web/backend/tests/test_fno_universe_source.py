"""v0.4.8 regression tests — F&O universe sourcing (hotfix #6 support).

The built-in 51-symbol FNO_UNIVERSE under-covers the real NSE F&O segment
(~180-220 underlyings) and carried stale lot sizes. scripts/
rebuild_fno_universe.py regenerates utils/fno_universe_generated.py from
NSE's official fo_mktlots.csv; market_utils prefers that module and falls
back to the built-in list. These tests pin both paths' integrity invariants.
"""

import importlib


def _load_market_utils():
    import utils.market_utils as mu
    importlib.reload(mu)
    return mu


def test_builtin_universe_integrity():
    mu = _load_market_utils()
    symbols = [s["symbol"] for s in mu.FNO_UNIVERSE]
    assert len(symbols) == len(set(symbols)), "duplicate symbols in universe"
    assert all(s["lot_size"] and int(s["lot_size"]) > 0 for s in mu.FNO_UNIVERSE)
    assert all(s["symbol"] == s["symbol"].upper() for s in mu.FNO_UNIVERSE)
    # Core liquid names must always resolve
    for core in ("RELIANCE", "HDFCBANK", "TCS", "INFY", "SBIN"):
        assert core in symbols
    # Lookup maps stay in sync with the list
    assert set(mu._SYMBOL_MAP.keys()) == set(symbols)
    assert len(mu._LOT_SIZE_MAP) == len(symbols)


def test_generated_universe_merges_metadata_when_present():
    """If the generated module exists, it must be preferred and merged:
    NSE lot sizes win, built-in name/sector metadata is preserved."""
    try:
        from utils import fno_universe_generated  # noqa: F401
    except ImportError:
        return  # generated file absent → built-in list active; nothing to test

    mu = _load_market_utils()
    gen_syms = {g["symbol"] for g in fno_universe_generated.FNO_UNIVERSE_GENERATED}
    active_syms = {s["symbol"] for s in mu.FNO_UNIVERSE}
    assert active_syms == gen_syms, "active universe must equal generated universe"

    # NSE lot sizes must win for symbols that exist in both
    gen_lots = {g["symbol"]: g["lot_size"] for g in fno_universe_generated.FNO_UNIVERSE_GENERATED}
    for s in mu.FNO_UNIVERSE:
        if s["symbol"] in gen_lots:
            assert s["lot_size"] == gen_lots[s["symbol"]]

    # Rich metadata preserved for previously-known symbols
    known = [s for s in mu.FNO_UNIVERSE if s["symbol"] == "RELIANCE"]
    if known:
        assert known[0]["sector"] != "Unknown"


def test_get_all_fno_symbols_matches_universe():
    mu = _load_market_utils()
    assert set(mu.get_all_fno_symbols()) == {s["symbol"] for s in mu.FNO_UNIVERSE}
