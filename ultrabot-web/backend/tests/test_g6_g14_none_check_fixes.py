"""
Permanent regression tests for the falsy-`or` cleanups in G6 and G14
(audit claims #1 and #2).

Both gates used `context.get(K1) or context.get(K2) or default` chains,
which treat legitimately-EMPTY values (empty list / empty dict) as missing
and silently fall through to the next key — the exact bug class fixed in
G16/risk_engine earlier (confidence=0.0, capital=0). These tests pin the
explicit-None-check semantics:

  G6:  open_position_symbols=[] means "no open positions" — it must NOT be
       replaced by open_positions_list (which a future caller could hold
       stale/divergent data in). The engine currently writes both keys to
       the same list in lockstep, so this is a latent-trap fix.
  G14: backtest_result={} (explicit "no metrics") is preserved and routed
       to live-stats / insufficient-history, never silently swapped for
       signal-carried metrics.
"""
from types import SimpleNamespace

import pytest

from risk.gates.g6_correlation_check import G6CorrelationCheck
from risk.gates.g14_strategy_backtest import G14StrategyBacktest


# ---------------------------------------------------------------------------
# G6 — open-position list resolution
# ---------------------------------------------------------------------------
class TestG6NoneCheckResolution:
    def _gate(self):
        return G6CorrelationCheck({"max_pairwise_correlation": 0.85})

    def _signal(self, sym="ICICIBANK"):
        return {"symbol": sym, "direction": "BUY", "strategy": "MB"}

    def _ctx(self, sym="ICICIBANK", **kwargs):
        """Context shaped like the engine's real _build_risk_context output
        (which always carries "symbol" — G6 reads it for dict signals)."""
        ctx = {"symbol": sym}
        ctx.update(kwargs)
        return ctx

    @pytest.mark.asyncio
    async def test_empty_primary_list_does_not_pull_secondary_key(self):
        """THE behavioral fix: [] = 'no open positions' — the stale
        HDFCBANK entry in the secondary key must be IGNORED."""
        res = await self._gate().check(
            self._signal(),
            self._ctx(open_position_symbols=[], open_positions_list=["HDFCBANK"]),
        )
        assert res.passed is True, "empty primary list must win — no correlation check"

    @pytest.mark.asyncio
    async def test_secondary_list_used_when_primary_missing(self):
        res = await self._gate().check(
            self._signal("ICICIBANK"),  # corr(ICICIBANK, HDFCBANK) = 0.88
            self._ctx("ICICIBANK", open_positions_list=["HDFCBANK"]),
        )
        assert res.passed is False
        assert "HDFCBANK" in res.message

    @pytest.mark.asyncio
    async def test_secondary_list_used_when_primary_is_none(self):
        """Explicit None (not just missing) also falls to the secondary key."""
        res = await self._gate().check(
            self._signal("ICICIBANK"),
            self._ctx("ICICIBANK", open_position_symbols=None, open_positions_list=["HDFCBANK"]),
        )
        assert res.passed is False

    @pytest.mark.asyncio
    async def test_primary_list_blocks_when_populated(self):
        res = await self._gate().check(
            self._signal("ICICIBANK"),
            self._ctx("ICICIBANK", open_position_symbols=["HDFCBANK"], open_positions_list=[]),
        )
        assert res.passed is False, "primary key must be authoritative when non-empty"

    @pytest.mark.asyncio
    async def test_both_keys_missing_passes(self):
        res = await self._gate().check(self._signal(), self._ctx())
        assert res.passed is True

    @pytest.mark.asyncio
    async def test_position_objects_and_dicts_extracted(self):
        """Mixed list contents (str / object / dict) still resolve symbols."""
        res = await self._gate().check(
            self._signal("ICICIBANK"),
            self._ctx(
                "ICICIBANK",
                open_position_symbols=[
                    "WIPRO",  # low corr
                    SimpleNamespace(symbol="HDFCBANK"),  # 0.88 → block
                    {"symbol": "TCS"},
                ],
            ),
        )
        assert res.passed is False
        assert "HDFCBANK" in res.message

    @pytest.mark.asyncio
    async def test_same_symbol_position_skipped(self):
        res = await self._gate().check(
            self._signal("ICICIBANK"),
            self._ctx("ICICIBANK", open_position_symbols=["ICICIBANK"]),
        )
        assert res.passed is True, "same-symbol position must not self-block"

    @pytest.mark.asyncio
    async def test_below_threshold_correlation_passes(self):
        res = await self._gate().check(
            self._signal("SBIN"),  # corr(SBIN, HDFCBANK) = 0.79 < 0.85
            self._ctx("SBIN", open_position_symbols=["HDFCBANK"]),
        )
        assert res.passed is True


# ---------------------------------------------------------------------------
# G14 — backtest-metrics resolution
# ---------------------------------------------------------------------------
class TestG14NoneCheckResolution:
    def _gate(self):
        return G14StrategyBacktest({"min_backtest_win_rate": 0.55, "min_backtest_samples": 10})

    @pytest.mark.asyncio
    async def test_explicit_empty_context_dict_is_preserved(self):
        """An explicitly-empty backtest_result means 'no metrics' — it must
        NOT be silently swapped for signal-carried metrics (old behavior:
        {} is falsy → fell through to signal.backtest_metrics)."""
        signal = {
            "symbol": "RELIANCE",
            "strategy": "ORB",
            "confidence": 0.8,
            "backtest_metrics": {"win_rate": 0.90, "profit_factor": 2.5, "total_trades": 50},
        }
        res = await self._gate().check(signal, {"backtest_result": {}})
        # Explicit empty → insufficient history (win_rate None), NOT the
        # fabricated-looking 90% from the signal.
        assert res.passed is True
        assert res.value is None, "must not substitute signal metrics for an explicit empty"
        assert "insufficient history" in res.message

    @pytest.mark.asyncio
    async def test_missing_context_key_uses_signal_metrics(self):
        """When context truly has no key, dict-signals still supply metrics."""
        signal = {
            "symbol": "RELIANCE",
            "strategy": "ORB",
            "backtest_metrics": {
                "win_rate": 40.0,  # percentage form — normalized to 0.40
                "profit_factor": 2.0,
                "total_trades": 50,
            },
        }
        res = await self._gate().check(signal, {})
        assert res.passed is False, "win_rate 40% < 55% must block"
        assert "below minimum requirement" in res.message

    @pytest.mark.asyncio
    async def test_missing_context_key_uses_signal_attribute(self):
        signal = SimpleNamespace(
            symbol="RELIANCE",
            strategy="ORB",
            backtest_result={"win_rate": 0.45, "profit_factor": 2.0, "total_trades": 50},
        )
        res = await self._gate().check(signal, {})
        assert res.passed is False, "win_rate 45% < 55% must block"

    @pytest.mark.asyncio
    async def test_context_result_wins_over_signal_attribute(self):
        """Priority preserved: context beats signal when both carry data."""
        signal = SimpleNamespace(
            symbol="RELIANCE",
            strategy="ORB",
            backtest_result={"win_rate": 0.90, "profit_factor": 3.0, "total_trades": 100},
        )
        res = await self._gate().check(
            signal,
            {"backtest_result": {"win_rate": 0.40, "profit_factor": 2.0, "total_trades": 50}},
        )
        assert res.passed is False, "context's 40% must win over signal's 90%"

    @pytest.mark.asyncio
    async def test_no_data_anywhere_passes_honestly(self):
        res = await self._gate().check(
            {"symbol": "RELIANCE", "strategy": "ORB", "confidence": 0.8}, {}
        )
        assert res.passed is True
        assert res.value is None
        assert "insufficient history" in res.message

    @pytest.mark.asyncio
    async def test_live_stats_still_used_when_no_backtest_data(self):
        res = await self._gate().check(
            {"symbol": "RELIANCE", "strategy": "ORB", "confidence": 0.8},
            {
                "strategy_stats": {
                    "win_rate": 0.40,
                    "profit_factor": 2.0,
                    "total_trades": 50,
                    "source": "trades_ledger",
                }
            },
        )
        assert res.passed is False, "live ledger stats with 40% win rate must block"
        assert "trades_ledger" in res.message
