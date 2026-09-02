"""Gate G14: Strategy Backtest Pre-Validation Gate.

Validates that the strategy has demonstrated a verified historical edge
(minimum win rate and profit factor) on this specific symbol or market regime
before any opportunity is presented or executed.

Data sources (in priority order — NO fabricated fallbacks):
1. Explicit ``backtest_result`` / ``backtest_metrics`` attached to the signal
   or passed in the risk context (real backtest output).
2. ``context["strategy_stats"]`` — live per-strategy performance computed from
   the trades ledger by the engine (db: strategy_performance).

If neither exists (e.g. a fresh install with no trade history), the gate
passes with an explicit "insufficient history" note instead of inventing
statistics.
"""
from typing import Any, Dict, Optional

from models.risk_state import GateResult


class G14StrategyBacktest:
    """Pre-validates strategy statistical edge via backtest before opening opportunities."""

    def __init__(self, config: Dict[str, Any]):
        self.min_win_rate: float = float(config.get("min_backtest_win_rate", 0.55))
        self.min_profit_factor: float = float(config.get("min_backtest_profit_factor", 1.25))
        # Minimum number of real closed trades before stats are considered reliable.
        self.min_samples: int = int(config.get("min_backtest_samples", 10))

    async def check(self, signal: Any, context: Dict[str, Any]) -> GateResult:
        strategy_raw = getattr(signal, "strategy", "") or (signal.get("strategy") if isinstance(signal, dict) else "")
        strategy_key = str(strategy_raw).lower().replace(" ", "_").replace("-", "_")
        symbol = getattr(signal, "symbol", "") or (signal.get("symbol") if isinstance(signal, dict) else "UNKNOWN")

        # 1. Explicit real backtest metrics attached to the signal / context
        # v0.4.3 (audit claim #2): explicit None checks — the same falsy-`or`
        # cleanup as G6/G16. An empty-but-present dict is PRESERVED (it is an
        # explicit "no metrics" value) rather than silently replaced by the
        # next candidate; the truthiness check at the `isinstance` block below
        # then correctly routes to live stats / insufficient-history. No
        # production code currently populates these keys, so this is a
        # consistency/latent-trap fix with zero behavior change on live paths.
        backtest_data = context.get("backtest_result")
        if backtest_data is None:
            backtest_data = getattr(signal, "backtest_result", None)
        if backtest_data is None and isinstance(signal, dict):
            backtest_data = signal.get("backtest_metrics")

        # 2. Real live performance stats from the trades ledger (engine-provided)
        live_stats = context.get("strategy_stats") if isinstance(context, dict) else None
        if not isinstance(live_stats, dict):
            live_stats = None

        source = None
        win_rate: Optional[float] = None
        profit_factor: Optional[float] = None
        sample_count = 0

        if isinstance(backtest_data, dict) and backtest_data:
            source = "backtest_result"
            win_rate = float(backtest_data.get("win_rate", 0) or 0)
            if win_rate > 1.0:
                win_rate = win_rate / 100.0  # normalize percentage
            profit_factor = float(backtest_data.get("profit_factor", 0) or 0)
            sample_count = int(backtest_data.get("total_trades", 0) or 0)
        elif live_stats is not None and int(live_stats.get("total_trades", 0) or 0) > 0:
            source = str(live_stats.get("source", "live_performance"))
            win_rate = float(live_stats.get("win_rate", 0) or 0)
            if win_rate > 1.0:
                win_rate = win_rate / 100.0
            profit_factor = float(live_stats.get("profit_factor", 0) or 0)
            sample_count = int(live_stats.get("total_trades", 0) or 0)

        # No verified data at all — pass with an honest note (never fabricate).
        if win_rate is None or profit_factor is None:
            return GateResult(
                gate_name="G14_StrategyBacktest",
                passed=True,
                message=(
                    f"No verified backtest/live statistics for {strategy_raw or strategy_key} yet — "
                    f"gate not evaluated (insufficient history). Trades will build the real track record."
                ),
                value=None,
                threshold=self.min_win_rate,
                severity="info",
            )

        # Insufficient sample size — pass with an honest low-confidence note.
        if sample_count < self.min_samples:
            return GateResult(
                gate_name="G14_StrategyBacktest",
                passed=True,
                message=(
                    f"{strategy_raw or strategy_key} has only {sample_count} closed trades "
                    f"(< {self.min_samples} required for statistical confidence) — "
                    f"win rate {win_rate * 100:.1f}% shown for information only."
                ),
                value=win_rate,
                threshold=self.min_win_rate,
                severity="info",
            )

        # Evaluation against real numbers
        if win_rate < self.min_win_rate:
            return GateResult(
                gate_name="G14_StrategyBacktest",
                passed=False,
                message=(
                    f"Verified win rate for {strategy_raw} on {symbol} is {win_rate * 100:.1f}% "
                    f"({sample_count} trades, {source}), below minimum requirement of "
                    f"{self.min_win_rate * 100:.1f}% (PF: {profit_factor:.2f})"
                ),
                value=win_rate,
                threshold=self.min_win_rate,
                severity="warning",
            )

        if profit_factor < self.min_profit_factor:
            return GateResult(
                gate_name="G14_StrategyBacktest",
                passed=False,
                message=(
                    f"Verified profit factor for {strategy_raw} on {symbol} is {profit_factor:.2f} "
                    f"({sample_count} trades, {source}), below minimum requirement of "
                    f"{self.min_profit_factor:.2f}"
                ),
                value=profit_factor,
                threshold=self.min_profit_factor,
                severity="warning",
            )

        return GateResult(
            gate_name="G14_StrategyBacktest",
            passed=True,
            message=(
                f"Edge verified: {strategy_raw} win rate {win_rate * 100:.1f}% over "
                f"{sample_count} trades ({source}, PF: {profit_factor:.2f} >= {self.min_profit_factor:.2f})"
            ),
            value=win_rate,
            threshold=self.min_win_rate,
            severity="info",
        )
