"""v0.4.11 — dynamic sector map + cash-only universe tier tests.

Locks in the G2 flow-recovery fix: the full ~210-symbol F&O universe must
carry real sector attribution (from scripts/build_sector_map.py — Dhan master
universe + TradingView sector scan), and cash-only listings (TMCV) must be
flagged out of F&O tradeability while staying visible to the scanner.
"""
import json
from pathlib import Path

import pytest

from utils.market_utils import (
    CASH_ONLY_UNIVERSE,
    FNO_UNIVERSE,
    get_all_fno_symbols,
    get_stock_industry,
    get_stock_sector,
    is_cash_only,
    is_fno_stock,
    is_fno_tradeable,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
SECTOR_MAP_JSON = BACKEND_DIR / "config" / "sector_map.json"


class TestSectorAttribution:
    def test_sector_map_artifact_exists_and_is_dated(self):
        assert SECTOR_MAP_JSON.exists(), (
            "config/sector_map.json missing — run scripts/build_sector_map.py"
        )
        data = json.loads(SECTOR_MAP_JSON.read_text(encoding="utf-8"))
        meta = data.get("meta") or {}
        assert meta.get("generated_at"), "sector map manifest missing generated_at"
        assert meta.get("sector_source"), "sector map manifest missing sector_source"

    def test_full_universe_sector_coverage(self):
        """The 2026-09-04 live bug: COLPAL/TRENT/KALYANKJIL all resolved to
        'Unknown' and G2 counted them as one false concentration bucket.
        Unknown rate over the whole universe must stay tiny (<2%)."""
        symbols = get_all_fno_symbols()
        assert len(symbols) >= 200, f"universe shrank to {len(symbols)}"
        unknown = [s for s in symbols if get_stock_sector(s) == "Unknown"]
        assert len(unknown) / len(symbols) < 0.02, (
            f"too many Unknown-sector symbols: {unknown}"
        )

    def test_friday_live_symbols_have_real_sectors(self):
        for sym in ("COLPAL", "TRENT", "KALYANKJIL", "CIPLA", "AMBUJACEM"):
            sector = get_stock_sector(sym)
            assert sector != "Unknown", f"{sym} lost sector attribution"
            assert sector, f"{sym} empty sector"

    def test_sector_override_matches_manifest(self):
        data = json.loads(SECTOR_MAP_JSON.read_text(encoding="utf-8"))
        for sym, sector in list(data.get("sectors", {}).items())[:20]:
            assert get_stock_sector(sym) == sector, f"{sym} override not applied"

    def test_industry_available_for_most_symbols(self):
        symbols = get_all_fno_symbols()
        with_industry = sum(1 for s in symbols if get_stock_industry(s))
        assert with_industry / len(symbols) > 0.95


class TestCashOnlyTier:
    def test_tmcv_present_and_flagged(self):
        by_symbol = {s["symbol"]: s for s in FNO_UNIVERSE}
        assert "TMCV" in by_symbol
        assert by_symbol["TMCV"].get("cash_only") is True
        assert by_symbol["TMCV"]["lot_size"] == 1

    def test_tmcv_cash_only_and_not_fno_tradeable(self):
        assert is_cash_only("TMCV")
        assert not is_fno_tradeable("TMCV")
        # Still part of the universe for scanning / news / G2 attribution
        assert is_fno_stock("TMCV")
        assert get_stock_sector("TMCV") != "Unknown"

    def test_regular_fno_symbols_remain_tradeable(self):
        for sym in ("RELIANCE", "CIPLA", "TMPV"):
            assert is_fno_tradeable(sym)
            assert not is_cash_only(sym)

    def test_cash_only_tier_built_in_consistent(self):
        """market_utils and the builder must agree on the tier membership."""
        try:
            sys_path_hack = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
            import sys
            sys.path.insert(0, str(sys_path_hack))
            from build_sector_map import CASH_ONLY_UNIVERSE as BUILDER_TIER
        except Exception:
            pytest.skip("builder not importable in this environment")
        builder_syms = {e["symbol"] for e in BUILDER_TIER}
        runtime_syms = {e["symbol"] for e in CASH_ONLY_UNIVERSE}
        assert builder_syms == runtime_syms
