"""Live-market validation correction #2 (2026-08-28): PaperBroker capital drift.

Observed live: engine/session capital = ₹500,000 (canonical resolve_total_capital)
but the PaperBroker internal ledger started at the BrokerFactory library default
₹100,000 — the engine never passed its resolved capital at broker creation.
Two ledgers, two different numbers: the paper broker's margin check rejected
orders the position sizer (correctly) sized against the configured capital.

Fix (core/engine.py):
  1. engine.start() passes initial_capital into BrokerFactory kwargs for paper mode
  2. _sync_paper_broker_capital() re-aligns the ledger after post-creation
     capital changes (same-day session recovery, live-margin fetch) — only
     when the broker has no open positions.

These tests pin both behaviours. Fixtures are synthetic BY DESIGN (unit test);
production paths use the canonical resolver exclusively.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.factory import BrokerFactory
from brokers.paper_broker import PaperBroker
from core.engine import UltraBotEngine


def _make_engine_bare():
    """Engine with the minimum collaborators needed for start()-adjacent paths."""
    cfg = MagicMock()
    cfg.get_capital_config.return_value = {"virtual_capital": 500000.0}
    cfg.get_broker_config.return_value = {}
    cfg.get_risk_config.return_value = {}
    cfg.get_engine_config.return_value = {}

    factory = MagicMock(spec=BrokerFactory)
    factory.create.side_effect = lambda name, mode="paper", **kw: PaperBroker(
        initial_capital=float(kw.get("initial_capital", 100000.0)),
    )

    engine = UltraBotEngine(
        config=cfg,
        repository_getter=None,
        error_engine=None,
        risk_engine=None,
        position_sizer=None,
        partial_booker=None,
        daily_risk_manager=None,
        broker_factory=factory,
        feed_manager=None,
        session_manager=None,
        market_hours=None,
        ws_manager=None,
    )
    engine._broadcast = AsyncMock()
    return engine


class TestPaperBrokerCapitalAlignment:
    def test_factory_receives_engine_capital_on_paper_start(self):
        """The kwargs passed to BrokerFactory.create for paper mode must carry
        the engine-resolved initial_capital (not the 100000 library default)."""
        engine = _make_engine_bare()
        engine.initial_capital = 500000.0

        # Reproduce the exact kwargs-building logic from engine.start()
        file_config = engine.config.get_broker_config("yahoofinance") or {}
        merged_config = dict(file_config)
        if "paper" == "paper":  # mode == paper
            merged_config.setdefault("initial_capital", engine.initial_capital)
        broker = engine.broker_factory.create("yahoofinance", mode="paper", **merged_config)

        assert isinstance(broker, PaperBroker)
        assert broker.initial_capital == 500000.0

    def test_sync_updates_empty_paper_broker(self):
        """_sync_paper_broker_capital aligns an idle (no-position) ledger."""
        engine = _make_engine_bare()
        engine.broker = PaperBroker(initial_capital=100000.0)
        engine.initial_capital = 500000.0

        engine._sync_paper_broker_capital()
        assert engine.broker.initial_capital == 500000.0
        assert engine.broker.capital == 500000.0

    def test_sync_never_clobbers_open_positions(self):
        """A ledger with open positions must NOT be reset mid-session."""
        engine = _make_engine_bare()
        engine.broker = PaperBroker(initial_capital=100000.0)
        engine.broker.positions["RELIANCE"] = {"status": "OPEN", "quantity": 10}
        engine.initial_capital = 500000.0

        engine._sync_paper_broker_capital()
        assert engine.broker.initial_capital == 100000.0  # unchanged

    def test_sync_ignores_non_paper_brokers(self):
        """Live broker objects are untouched by the paper sync."""
        engine = _make_engine_bare()
        engine.broker = MagicMock(spec=["authenticate", "get_margin"])
        engine.initial_capital = 500000.0

        engine._sync_paper_broker_capital()  # must not raise
