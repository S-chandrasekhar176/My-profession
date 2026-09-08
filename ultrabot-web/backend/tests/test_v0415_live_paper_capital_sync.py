"""v0.4.15 hotfix regression: (mode=live, broker=paper) capital sync.

Incident 2026-09-08 ~09:13 IST: engine started with mode=live + broker=paper.
BrokerFactory returned PaperBroker (paper-mapped name) built WITHOUT
initial_capital (the v0.4.13 kwargs fix only fired for mode == "paper"), so:
  1. PaperBroker ledger started at the ₹100k factory default while the
     engine/session resolved the configured ₹500k — two-ledger drift again;
  2. the mode=live "fetch real margin" branch then called
     PaperBroker.get_margin() and ADOPTED the ₹100k internal cash as
     engine/session capital (status showed initial_capital: 100000.0);
  3. G12_MarginCheck then compared full F&O LOT margins against ₹100k and
     rejected 68/74 signals (G12 wall, zero trades by 10:24 IST).

Pins:
  - _will_run_paper() mirrors BrokerFactory resolution exactly
  - engine.start() passes initial_capital for (live, paper) too
  - the live-margin fetch is skipped for paper-resolved brokers
"""
from unittest.mock import AsyncMock, MagicMock

from brokers.factory import BrokerFactory
from brokers.paper_broker import PaperBroker
from core.engine import UltraBotEngine


class TestWillRunPaper:
    def test_paper_name_any_mode(self):
        assert UltraBotEngine._will_run_paper("paper", "live", BrokerFactory) is True

    def test_paper_mapped_aliases_in_live_mode(self):
        for name in ("yahoofinance", "yahoo", "demo", "virtual", "simulation", "upstox"):
            assert UltraBotEngine._will_run_paper(name, "live", BrokerFactory) is True, name

    def test_real_brokers_in_live_mode(self):
        for name in ("fyers", "angel_one", "angelone", "shoonya", "dhan", "zerodha", "kite"):
            assert UltraBotEngine._will_run_paper(name, "live", BrokerFactory) is False, name

    def test_paper_mode_forces_everything(self):
        for name in ("fyers", "paper", "yahoofinance", "angel_one"):
            assert UltraBotEngine._will_run_paper(name, "paper", BrokerFactory) is True, name

    def test_normalization(self):
        assert UltraBotEngine._will_run_paper("Angel-One", "live", BrokerFactory) is False
        assert UltraBotEngine._will_run_paper("YAHOO FINANCE", "live", BrokerFactory) is True
        assert UltraBotEngine._will_run_paper(None, "live", BrokerFactory) is True


class TestLivePaperStartWiring:
    """Source-level pin: start() must use will_run_paper (not mode == "paper")
    for both the factory kwargs and the live-margin branch."""

    def _start_source(self) -> str:
        import inspect

        return inspect.getsource(UltraBotEngine.start)

    def test_factory_kwargs_cover_live_paper(self):
        src = self._start_source()
        assert "will_run_paper = UltraBotEngine._will_run_paper(" in src
        assert "if will_run_paper:\n                merged_config.setdefault" in src

    def test_live_margin_fetch_skips_paper_resolved(self):
        src = self._start_source()
        assert 'elif self.mode == "live" and not will_run_paper:' in src

    def test_capital_sync_covers_live_paper(self):
        src = self._start_source()
        assert "if will_run_paper and self.broker:" in src

    def test_paper_broker_get_margin_is_internal_cash(self):
        """The reason the live-margin branch must skip PaperBroker: its margin
        report is its OWN ledger cash, not an external account balance."""
        b = PaperBroker(initial_capital=100000.0)
        assert hasattr(b, "get_margin")

        import asyncio

        margins = asyncio.run(b.get_margin())
        # keys per PaperBroker.get_margin: total/available/used — note the
        # engine's live-margin key scan checks "available" FIRST, which is
        # exactly how the factory-default 100k got adopted as session capital
        assert float(margins.get("available", 0.0)) == 100000.0
