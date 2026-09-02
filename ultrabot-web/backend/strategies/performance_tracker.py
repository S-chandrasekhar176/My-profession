import asyncio
import inspect
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Tracks per-strategy and per-regime performance metrics.

    Stores trade results in memory and periodically persists to a repository.
    """

    def __init__(self, repository=None, persist_interval: int = 50):
        self.repository = repository
        self.persist_interval = persist_interval
        self._trade_count_since_persist = 0
        self._unpersisted_trades: List[Dict[str, Any]] = []

        # Primary storage: keyed by (strategy_name, regime)
        self._trades: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def record_trade(
        self,
        strategy_name: str,
        regime: str,
        pnl: float,
        pnl_pct: float = 0.0,
        holding_time_seconds: float = 0.0,
        trade_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> None:
        """Record a single completed trade."""
        record = {
            "strategy": strategy_name,
            "regime": regime,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "holding_time_seconds": holding_time_seconds,
            "trade_id": trade_id,
            "symbol": symbol,
            "timestamp": time.time(),
        }
        key = f"{strategy_name}||{regime}"
        self._trades[key].append(record)

        # Also record under strategy-only key for aggregate stats
        agg_key = f"{strategy_name}||__all__"
        self._trades[agg_key].append(record)

        self._unpersisted_trades.append(record)
        self._trade_count_since_persist += 1
        if self._trade_count_since_persist >= self.persist_interval:
            self._persist()

    def _persist(self) -> None:
        """Persist newly accumulated trades to the repository."""
        if self.repository is None or not self._unpersisted_trades:
            return
        try:
            if hasattr(self.repository, "batch_insert_performance"):
                res = self.repository.batch_insert_performance(list(self._unpersisted_trades))
                if inspect.isawaitable(res) or asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        asyncio.run(res)
                self._unpersisted_trades.clear()
                self._trade_count_since_persist = 0
        except Exception as exc:
            logger.debug("Strategy performance persist failed: %s", exc)

    def _get_trades(self, strategy_name: str, regime: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get trades for a strategy, optionally filtered by regime."""
        if regime is not None:
            key = f"{strategy_name}||{regime}"
        else:
            key = f"{strategy_name}||__all__"
        return self._trades.get(key, [])

    def get_win_rate(self, strategy_name: str, regime: Optional[str] = None) -> float:
        """Return win rate (0.0 to 1.0) for a strategy."""
        trades = self._get_trades(strategy_name, regime)
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t["pnl"] > 0)
        return wins / len(trades)

    def get_avg_pnl(self, strategy_name: str, regime: Optional[str] = None) -> float:
        """Return average PnL for a strategy."""
        trades = self._get_trades(strategy_name, regime)
        if not trades:
            return 0.0
        return sum(t["pnl"] for t in trades) / len(trades)

    def get_stats(self, strategy_name: str, regime: Optional[str] = None) -> Dict[str, Any]:
        """Return comprehensive stats for a strategy."""
        trades = self._get_trades(strategy_name, regime)
        total_trades = len(trades)
        if total_trades == 0:
            return {
                "strategy_name": strategy_name,
                "regime": regime,
                "total_signals": 0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "total_pnl": 0.0,
            }

        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)
        total_pnl = sum(t["pnl"] for t in trades)
        avg_pnl = total_pnl / total_trades

        # total_signals is approximated as total_trades for now;
        # a full implementation would track signals that never became trades.
        return {
            "strategy_name": strategy_name,
            "regime": regime,
            "total_signals": total_trades,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total_trades,
            "avg_pnl": round(avg_pnl, 4),
            "total_pnl": round(total_pnl, 4),
        }

    def force_persist(self) -> None:
        """Force an immediate persistence of all recorded trades."""
        self._persist()
