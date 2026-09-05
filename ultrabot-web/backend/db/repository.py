"""
Complete async repository for all UltraBot Web models.
Provides CRUD operations for every table plus domain-specific queries.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select, update, delete, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from db.migrations import (
    Session as SessionModel,
    Trade,
    Signal,
    Position,
    WatchlistItem,
    StrategyPerformance,
    RiskEvent,
    BrokerCredential,
    ErrorLog,
    BacktestRun,
    DailySummary,
    ShadowOutcome,
)

from utils.market_utils import get_stock_sector

IST = ZoneInfo("Asia/Kolkata")


def _ist_now() -> str:
    return datetime.now(IST).isoformat()


def _today_str() -> str:
    return datetime.now(IST).date().isoformat()


def _to_json(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, default=str)


def _from_json(text: Optional[str]) -> Any:
    if text is None:
        return {}
    if isinstance(text, (dict, list)):
        return text
    return json.loads(text)


class Repository:
    """Async CRUD repository for all UltraBot models."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def close(self) -> None:
        """Close the underlying session and release the database connection."""
        if self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass

    async def __aenter__(self) -> "Repository":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None and self.session is not None:
            try:
                await self.session.rollback()
            except Exception:
                pass
        await self.close()

    # ────────────────────────────────────────
    # Generic helpers
    # ────────────────────────────────────────

    async def _add_and_flush(self, obj) -> Any:
        self.session.add(obj)
        await self.session.flush()
        await self.session.commit()
        return obj

    async def _get_by_id(self, model, obj_id: str) -> Optional[Any]:
        stmt = select(model).where(model.id == obj_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_all(self, model, limit: int = 100, offset: int = 0) -> List[Any]:
        stmt = select(model).order_by(model.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _delete_by_id(self, model, obj_id: str) -> bool:
        stmt = delete(model).where(model.id == obj_id)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def _count(self, model) -> int:
        stmt = select(func.count()).select_from(model)
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    # ────────────────────────────────────────
    # SESSIONS
    # ────────────────────────────────────────

    async def create_session(self, date_str: Optional[str] = None, engine_state: Optional[Dict] = None, metadata_json: Optional[Dict] = None) -> SessionModel:
        obj = SessionModel(
            id=str(uuid.uuid4()),
            date=date_str or _today_str(),
            start_time=_ist_now(),
            status="running",
            engine_state=_to_json(engine_state or {}),
            metadata_json=_to_json(metadata_json or {}),
            created_at=_ist_now(),
            updated_at=_ist_now(),
        )
        return await self._add_and_flush(obj)

    async def get_session(self, session_id: str) -> Optional[SessionModel]:
        return await self._get_by_id(SessionModel, session_id)

    async def get_latest_session(self) -> Optional[SessionModel]:
        stmt = select(SessionModel).order_by(SessionModel.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_sessions(self, limit: int = 50, offset: int = 0) -> List[SessionModel]:
        return await self._get_all(SessionModel, limit, offset)

    async def get_sessions_by_date(self, date_str: str) -> List[SessionModel]:
        stmt = select(SessionModel).where(SessionModel.date == date_str).order_by(SessionModel.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_first_session_by_date(self, date_str: str) -> Optional[SessionModel]:
        stmt = select(SessionModel).where(SessionModel.date == date_str).order_by(SessionModel.created_at.asc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_session_by_date(self, date_str: str) -> Optional[SessionModel]:
        """Newest session created on the date (same-day restart anchor)."""
        stmt = select(SessionModel).where(SessionModel.date == date_str).order_by(SessionModel.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_session(self, session_id: str, **kwargs) -> Optional[SessionModel]:
        obj = await self.get_session(session_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key in ("engine_state", "metadata_json") and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def save_session_state(self, session_id: str, state: Dict[str, Any]) -> Optional[SessionModel]:
        return await self.update_session(session_id, engine_state=state, updated_at=_ist_now())

    async def delete_session(self, session_id: str) -> bool:
        return await self._delete_by_id(SessionModel, session_id)

    # ────────────────────────────────────────
    # TRADES
    # ────────────────────────────────────────

    async def create_trade(self, **kwargs) -> Trade:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "tags" in kwargs and isinstance(kwargs["tags"], (list, dict, tuple, set)):
            kwargs["tags"] = _to_json(kwargs["tags"])
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in Trade.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = Trade(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_trade(self, trade_id: str) -> Optional[Trade]:
        return await self._get_by_id(Trade, trade_id)

    async def get_trades(self, limit: int = 100, offset: int = 0) -> List[Trade]:
        return await self._get_all(Trade, limit, offset)

    async def get_trades_by_date(self, trade_date: str, limit: int = 100) -> List[Trade]:
        stmt = (
            select(Trade)
            .where(Trade.entry_time.startswith(trade_date))
            .order_by(Trade.entry_time.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_trades_by_status(self, status: str) -> List[Trade]:
        stmt = select(Trade).where(Trade.status == status).order_by(Trade.entry_time.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_trades_by_symbol(self, symbol: str, limit: int = 50) -> List[Trade]:
        stmt = select(Trade).where(Trade.symbol == symbol).order_by(Trade.entry_time.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_trades_by_strategy(self, strategy: str, limit: int = 50) -> List[Trade]:
        stmt = select(Trade).where(Trade.strategy == strategy).order_by(Trade.entry_time.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_todays_trades(self) -> List[Trade]:
        today = _today_str()
        return await self.get_trades_by_date(today, limit=500)

    async def update_trade(self, trade_id: str, **kwargs) -> Optional[Trade]:
        obj = await self.get_trade(trade_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key in ("tags", "extra") and isinstance(value, (dict, list)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_trade(self, trade_id: str) -> bool:
        return await self._delete_by_id(Trade, trade_id)

    async def get_todays_pnl(self) -> Dict[str, Any]:
        """Get today's aggregate P&L."""
        today = _today_str()
        trades = await self.get_trades_by_date(today, limit=500)
        closed = [t for t in trades if t.status == "CLOSED"]
        gross_pnl = sum((t.pnl or 0.0) for t in closed)
        total_fees = sum((t.fees or 0.0) for t in closed) + sum((t.brokerage or 0.0) for t in closed)
        net_pnl = sum((t.net_pnl or 0.0) for t in closed)
        wins = sum(1 for t in closed if t.net_pnl > 0)
        losses = sum(1 for t in closed if t.net_pnl < 0)
        total = len(closed)
        return {
            "date": today,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total * 100) if total > 0 else 0.0,
            "gross_pnl": round(gross_pnl, 2),
            "total_fees": round(total_fees, 2),
            "net_pnl": round(net_pnl, 2),
            "best_trade": round(max((t.net_pnl for t in closed), default=0), 2),
            "worst_trade": round(min((t.net_pnl for t in closed), default=0), 2),
        }

    # ────────────────────────────────────────
    # SIGNALS
    # ────────────────────────────────────────

    async def create_signal(self, **kwargs) -> Signal:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "signal_data" in kwargs and isinstance(kwargs["signal_data"], (dict, list, tuple, set)):
            kwargs["signal_data"] = _to_json(kwargs["signal_data"])
        if "risk_gate_results" in kwargs and isinstance(kwargs["risk_gate_results"], (dict, list, tuple, set)):
            kwargs["risk_gate_results"] = _to_json(kwargs["risk_gate_results"])
        data.update(kwargs)
        valid_cols = {c.name for c in Signal.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = Signal(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_signal(self, signal_id: str) -> Optional[Signal]:
        return await self._get_by_id(Signal, signal_id)

    async def get_signals(self, limit: int = 100, offset: int = 0) -> List[Signal]:
        return await self._get_all(Signal, limit, offset)

    async def get_signals_by_status(self, status: str) -> List[Signal]:
        stmt = select(Signal).where(Signal.status == status).order_by(Signal.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_signals_by_symbol(self, symbol: str, limit: int = 50) -> List[Signal]:
        stmt = select(Signal).where(Signal.symbol == symbol).order_by(Signal.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_signals_by_strategy(self, strategy: str, limit: int = 50) -> List[Signal]:
        stmt = select(Signal).where(Signal.strategy == strategy).order_by(Signal.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_todays_signals(self) -> List[Signal]:
        today = _today_str()
        stmt = select(Signal).where(Signal.created_at.startswith(today)).order_by(Signal.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_signal(self, signal_id: str, **kwargs) -> Optional[Signal]:
        obj = await self.get_signal(signal_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key in ("signal_data", "risk_gate_results") and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_signal(self, signal_id: str) -> bool:
        return await self._delete_by_id(Signal, signal_id)

    # ────────────────────────────────────────
    # POSITIONS
    # ────────────────────────────────────────

    async def create_position(self, **kwargs) -> Position:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in Position.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = Position(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_position(self, position_id: str) -> Optional[Position]:
        return await self._get_by_id(Position, position_id)

    async def get_positions(self, limit: int = 100, offset: int = 0) -> List[Position]:
        return await self._get_all(Position, limit, offset)

    async def get_open_positions(self) -> List[Position]:
        stmt = select(Position).where(Position.status.in_(["OPEN", "EXIT_FAILED"])).order_by(Position.entry_time.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        stmt = select(Position).where(Position.symbol == symbol).order_by(Position.entry_time.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_positions_by_strategy(self, strategy: str) -> List[Position]:
        stmt = select(Position).where(Position.strategy == strategy).order_by(Position.entry_time.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_position_count_by_sector(self) -> Dict[str, int]:
        positions = await self.get_open_positions()
        sector_counts: Dict[str, int] = {}
        for pos in positions:
            sector = get_stock_sector(pos.symbol)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        return sector_counts

    async def update_position(self, position_id: str, **kwargs) -> Optional[Position]:
        obj = await self.get_position(position_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key == "extra" and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_position(self, position_id: str) -> bool:
        return await self._delete_by_id(Position, position_id)

    # ────────────────────────────────────────
    # WATCHLIST
    # ────────────────────────────────────────

    async def add_watchlist_item(self, **kwargs) -> WatchlistItem:
        data = {
            "id": str(uuid.uuid4()),
            "added_at": _ist_now(),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in WatchlistItem.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = WatchlistItem(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_watchlist_item(self, item_id: str) -> Optional[WatchlistItem]:
        return await self._get_by_id(WatchlistItem, item_id)

    async def get_watchlist_item_by_symbol(self, symbol: str) -> Optional[WatchlistItem]:
        stmt = select(WatchlistItem).where(WatchlistItem.symbol == symbol)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_watchlist(self) -> List[WatchlistItem]:
        stmt = select(WatchlistItem).where(WatchlistItem.is_active == True).order_by(WatchlistItem.added_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_watchlist_items(self, limit: int = 100, offset: int = 0) -> List[WatchlistItem]:
        return await self._get_all(WatchlistItem, limit, offset)

    async def update_watchlist_item(self, item_id: str, **kwargs) -> Optional[WatchlistItem]:
        obj = await self.get_watchlist_item(item_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key == "extra" and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_watchlist_item(self, item_id: str) -> bool:
        return await self._delete_by_id(WatchlistItem, item_id)

    async def get_watchlist_count(self) -> int:
        return await self._count(WatchlistItem)

    # ────────────────────────────────────────
    # STRATEGY PERFORMANCE
    # ────────────────────────────────────────

    async def create_strategy_performance(self, strategy: str) -> StrategyPerformance:
        obj = StrategyPerformance(
            id=str(uuid.uuid4()),
            strategy=strategy,
            created_at=_ist_now(),
            updated_at=_ist_now(),
        )
        return await self._add_and_flush(obj)

    async def get_strategy_performance(self, strategy: str) -> Optional[StrategyPerformance]:
        stmt = select(StrategyPerformance).where(StrategyPerformance.strategy == strategy)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_strategy_performance(self) -> List[StrategyPerformance]:
        stmt = select(StrategyPerformance).order_by(StrategyPerformance.strategy)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_strategy_performance(self, strategy: str, **kwargs) -> Optional[StrategyPerformance]:
        obj = await self.get_strategy_performance(strategy)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key in ("daily_stats", "extra") and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_strategy_performance(self, strategy: str) -> bool:
        obj = await self.get_strategy_performance(strategy)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        await self.session.commit()
        return True

    async def ensure_strategy_performance(self, strategy: str) -> StrategyPerformance:
        obj = await self.get_strategy_performance(strategy)
        if obj is None:
            obj = await self.create_strategy_performance(strategy)
        return obj

    # ────────────────────────────────────────
    # REAL-TRADES-ONLY PERFORMANCE (Phase 1)
    # ────────────────────────────────────────
    # Win rates and every derived statistic below are computed LIVE from the
    # trades ledger (status == CLOSED). No synthetic/seeded numbers are ever
    # returned: a strategy with no closed trades reports total_trades = 0.

    async def get_closed_trades_by_strategy(self, strategy: str, limit: int = 500) -> List[Trade]:
        """All CLOSED trades for a strategy, oldest first (real executions only)."""
        stmt = (
            select(Trade)
            .where(Trade.strategy == strategy, Trade.status == "CLOSED")
            .order_by(Trade.exit_time.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def compute_strategy_stats(self, strategy: str) -> Dict[str, Any]:
        """Aggregate performance stats for a strategy from REAL closed trades.

        Returns a dict with total_trades / wins / losses / breakeven /
        win_rate (0-100) / avg_win / avg_loss / total_pnl / profit_factor /
        avg_holding_seconds / max_consecutive_wins / max_consecutive_losses.
        Zero-trade strategies return all-zero stats — never fabricated values.
        """
        trades = await self.get_closed_trades_by_strategy(strategy)
        total = len(trades)
        if total == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "total_pnl": 0.0,
                "profit_factor": 0.0,
                "avg_holding_seconds": 0.0,
                "max_consecutive_wins": 0,
                "max_consecutive_losses": 0,
                "source": "trades_ledger",
            }

        wins = [t for t in trades if float(t.net_pnl or 0.0) > 0]
        losses = [t for t in trades if float(t.net_pnl or 0.0) < 0]
        breakeven = total - len(wins) - len(losses)

        gross_win = sum(float(t.net_pnl or 0.0) for t in wins)
        gross_loss = abs(sum(float(t.net_pnl or 0.0) for t in losses))
        total_pnl = sum(float(t.net_pnl or 0.0) for t in trades)

        holdings = [float(t.holding_duration_seconds or 0.0) for t in trades if t.holding_duration_seconds]

        # Consecutive win/loss streaks over the ordered ledger
        max_con_wins = max_con_losses = 0
        cur_wins = cur_losses = 0
        for t in trades:
            pnl = float(t.net_pnl or 0.0)
            if pnl > 0:
                cur_wins += 1
                cur_losses = 0
            elif pnl < 0:
                cur_losses += 1
                cur_wins = 0
            else:
                cur_wins = cur_losses = 0
            max_con_wins = max(max_con_wins, cur_wins)
            max_con_losses = max(max_con_losses, cur_losses)

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": breakeven,
            "win_rate": round(len(wins) / total * 100.0, 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
            "avg_holding_seconds": round(sum(holdings) / len(holdings), 1) if holdings else 0.0,
            "max_consecutive_wins": max_con_wins,
            "max_consecutive_losses": max_con_losses,
            "source": "trades_ledger",
        }

    async def get_today_closed_trades_by_strategy(self, strategy: str) -> List[Trade]:
        """Today's CLOSED trades for one strategy, ordered by exit time."""
        today = _today_str()
        stmt = (
            select(Trade)
            .where(
                Trade.strategy == strategy,
                Trade.status == "CLOSED",
                Trade.entry_time.startswith(today),
            )
            .order_by(Trade.exit_time.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_todays_shadow_signals(self) -> List[Signal]:
        """Today's unresolved shadow signals (status == 'SHADOW')."""
        today = _today_str()
        stmt = (
            select(Signal)
            .where(Signal.status == "SHADOW", Signal.created_at.startswith(today))
            .order_by(Signal.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def compute_shadow_signal_stats(self) -> Dict[str, Dict[str, Any]]:
        """Per-strategy stats of SHADOW signals (signal tracking, NOT trade win rates).

        Shadow signals are recorded by strategies running in shadow mode: they
        pass every risk gate but never place orders. Outcomes are resolved
        against live prices (SHADOW_TARGET / SHADOW_SL / SHADOW_EXPIRED).
        These stats are reported SEPARATELY from real trade win rates and must
        never be mixed into them.
        """
        stmt = select(Signal).where(Signal.status.like("SHADOW%"))
        result = await self.session.execute(stmt)
        signals = list(result.scalars().all())

        stats: Dict[str, Dict[str, Any]] = {}
        for sig in signals:
            name = sig.strategy or "UNKNOWN"
            entry = stats.setdefault(
                name,
                {
                    "total_signals": 0,
                    "resolved": 0,
                    "wins": 0,
                    "losses": 0,
                    "expired": 0,
                    "pending": 0,
                    "signal_win_rate": 0.0,
                    "avg_risk_reward": None,
                    "_rr_sum": 0.0,
                    "_rr_n": 0,
                },
            )
            entry["total_signals"] += 1
            try:
                rr = float(sig.risk_reward) if sig.risk_reward else None
            except (TypeError, ValueError):
                rr = None
            if rr is not None:
                entry["_rr_sum"] += rr
                entry["_rr_n"] += 1
            if sig.status == "SHADOW":
                entry["pending"] += 1
            elif sig.status == "SHADOW_TARGET":
                entry["resolved"] += 1
                entry["wins"] += 1
            elif sig.status == "SHADOW_SL":
                entry["resolved"] += 1
                entry["losses"] += 1
            elif sig.status == "SHADOW_EXPIRED":
                entry["resolved"] += 1
                entry["expired"] += 1

        for entry in stats.values():
            decided = entry["wins"] + entry["losses"]
            entry["signal_win_rate"] = round(entry["wins"] / decided * 100.0, 2) if decided > 0 else 0.0
            rr_sum = entry.pop("_rr_sum", 0.0)
            rr_n = entry.pop("_rr_n", 0)
            entry["avg_risk_reward"] = round(rr_sum / rr_n, 2) if rr_n > 0 else None
        return stats

    async def get_regime_attribution(self) -> List[Dict[str, Any]]:
        """Per-strategy × per-regime attribution from REAL closed trades.

        Regime is read from the trade's extra JSON (persisted by the engine at
        execution time). Trades without a regime are grouped under 'Unknown'.
        """
        stmt = select(Trade).where(Trade.status == "CLOSED").order_by(Trade.exit_time.asc())
        result = await self.session.execute(stmt)
        trades = list(result.scalars().all())

        buckets: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            regime = "Unknown"
            try:
                extra = json.loads(t.extra) if t.extra else {}
                if isinstance(extra, dict) and extra.get("regime"):
                    regime = str(extra["regime"])
            except Exception:
                pass
            key = f"{t.strategy}||{regime}"
            entry = buckets.setdefault(
                key,
                {
                    "strategy": t.strategy,
                    "regime": regime,
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_pnl": 0.0,
                },
            )
            pnl = float(t.net_pnl or 0.0)
            entry["total_trades"] += 1
            entry["total_pnl"] += pnl
            if pnl > 0:
                entry["wins"] += 1
            elif pnl < 0:
                entry["losses"] += 1

        rows = []
        for entry in buckets.values():
            decided = entry["wins"] + entry["losses"]
            rows.append(
                {
                    **entry,
                    "total_pnl": round(entry["total_pnl"], 2),
                    "win_rate": round(entry["wins"] / decided * 100.0, 2) if decided > 0 else 0.0,
                }
            )
        rows.sort(key=lambda r: (r["strategy"], r["regime"]))
        return rows

    # ────────────────────────────────────────
    # RISK EVENTS
    # ────────────────────────────────────────

    async def create_risk_event(self, **kwargs) -> RiskEvent:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
        }
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in RiskEvent.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = RiskEvent(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_risk_events(self, limit: int = 100, offset: int = 0) -> List[RiskEvent]:
        stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_risk_events_by_severity(self, severity: str) -> List[RiskEvent]:
        stmt = select(RiskEvent).where(RiskEvent.severity == severity).order_by(RiskEvent.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_todays_risk_events(self) -> List[RiskEvent]:
        today = _today_str()
        stmt = select(RiskEvent).where(RiskEvent.created_at.startswith(today)).order_by(RiskEvent.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_risk_event(self, event_id: str) -> bool:
        return await self._delete_by_id(RiskEvent, event_id)

    # ────────────────────────────────────────
    # BROKER CREDENTIALS
    # ────────────────────────────────────────

    async def save_broker_credentials(self, broker_name: str, encrypted_creds: str, **kwargs) -> BrokerCredential:
        existing = await self.get_broker_credentials(broker_name)
        if existing:
            existing.encrypted_credentials = encrypted_creds
            existing.updated_at = _ist_now()
            for k, v in kwargs.items():
                if k == "extra" and isinstance(v, (dict, list, tuple, set)):
                    v = _to_json(v)
                if hasattr(existing, k):
                    setattr(existing, k, v)
            await self.session.flush()
            await self.session.commit()
            return existing

        data = {
            "id": str(uuid.uuid4()),
            "broker_name": broker_name,
            "encrypted_credentials": encrypted_creds,
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in BrokerCredential.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = BrokerCredential(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_broker_credentials(self, broker_name: str) -> Optional[BrokerCredential]:
        stmt = select(BrokerCredential).where(BrokerCredential.broker_name == broker_name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_broker_credentials(self) -> List[BrokerCredential]:
        stmt = select(BrokerCredential).order_by(BrokerCredential.broker_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_broker_credentials(self, broker_name: str) -> bool:
        stmt = delete(BrokerCredential).where(BrokerCredential.broker_name == broker_name)
        result = await self.session.execute(stmt)
        await self.session.flush()
        await self.session.commit()
        return result.rowcount > 0

    # ────────────────────────────────────────
    # ERROR LOGS
    # ────────────────────────────────────────

    async def create_error_log(self, **kwargs) -> ErrorLog:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "context" in kwargs and isinstance(kwargs["context"], (dict, list, tuple, set)):
            kwargs["context"] = _to_json(kwargs["context"])
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in ErrorLog.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = ErrorLog(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_error_log(self, error_id: str) -> Optional[ErrorLog]:
        return await self._get_by_id(ErrorLog, error_id)

    async def get_error_by_code(self, error_code: str) -> Optional[ErrorLog]:
        stmt = select(ErrorLog).where(ErrorLog.error_code == error_code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_errors(
        self,
        resolved: Optional[bool] = None,
        severity: Optional[str] = None,
        error_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ErrorLog]:
        stmt = select(ErrorLog)
        if resolved is not None:
            stmt = stmt.where(ErrorLog.is_resolved == resolved)
        if severity:
            stmt = stmt.where(ErrorLog.severity == severity)
        if error_type:
            stmt = stmt.where(ErrorLog.error_type == error_type)
        stmt = stmt.order_by(ErrorLog.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_errors_count(
        self,
        resolved: Optional[bool] = None,
        severity: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> int:
        stmt = select(func.count()).select_from(ErrorLog)
        if resolved is not None:
            stmt = stmt.where(ErrorLog.is_resolved == resolved)
        if severity:
            stmt = stmt.where(ErrorLog.severity == severity)
        if error_type:
            stmt = stmt.where(ErrorLog.error_type == error_type)
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def get_unresolved_errors(self) -> List[ErrorLog]:
        return await self.get_errors(resolved=False, limit=500)

    async def get_error_stats(self) -> Dict[str, Any]:
        total = await self._count(ErrorLog)
        stmt_unresolved = select(func.count()).select_from(ErrorLog).where(ErrorLog.is_resolved == False)
        result_unresolved = await self.session.execute(stmt_unresolved)
        unresolved = result_unresolved.scalar_one() or 0

        stmt_today = select(func.count()).select_from(ErrorLog).where(ErrorLog.created_at.startswith(_today_str()))
        result_today = await self.session.execute(stmt_today)
        today_count = result_today.scalar_one() or 0

        stmt_critical = select(func.count()).select_from(ErrorLog).where(ErrorLog.severity == "critical", ErrorLog.is_resolved == False)
        result_critical = await self.session.execute(stmt_critical)
        critical_unresolved = result_critical.scalar_one() or 0

        # Count by type
        stmt_types = select(ErrorLog.error_type, func.count()).group_by(ErrorLog.error_type)
        result_types = await self.session.execute(stmt_types)
        by_type = dict(result_types.all())

        return {
            "total_errors": total,
            "unresolved": unresolved,
            "today_count": today_count,
            "critical_unresolved": critical_unresolved,
            "by_type": by_type,
        }

    async def resolve_error(self, error_id: str, resolution_note: str = "") -> Optional[ErrorLog]:
        obj = await self.get_error_log(error_id)
        if obj is None:
            return None
        obj.is_resolved = True
        obj.resolved_at = _ist_now()
        obj.resolution_note = resolution_note
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_error_log(self, error_id: str) -> bool:
        return await self._delete_by_id(ErrorLog, error_id)

    # ────────────────────────────────────────
    # BACKTEST RUNS
    # ────────────────────────────────────────

    async def create_backtest_run(
        self,
        id: Optional[str] = None,
        strategy: str = "",
        symbol: Optional[str] = None,
        start_date: str = "",
        end_date: str = "",
        timeframe: str = "5min",
        initial_capital: float = 100000.0,
        status: str = "PENDING",
        parameters: Optional[Dict[str, Any]] = None,
        results: Optional[Dict[str, Any]] = None,
        equity_curve: Optional[List[Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> BacktestRun:
        run_id = id or kwargs.pop("run_id", None) or str(uuid.uuid4())
        data = {
            "id": run_id,
            "strategy": strategy,
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "timeframe": timeframe,
            "initial_capital": initial_capital,
            "status": status,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "parameters": _to_json(parameters or {}),
            "results": _to_json(results or {}),
            "equity_curve": _to_json(equity_curve or []),
            "extra": _to_json(extra or {}),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        for k, v in kwargs.items():
            if k in ("parameters", "results", "equity_curve", "extra") and isinstance(v, (dict, list, tuple, set)):
                v = _to_json(v)
            data[k] = v

        valid_cols = {c.name for c in BacktestRun.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        run = BacktestRun(**filtered_data)
        self.session.add(run)
        await self.session.flush()
        await self.session.commit()
        return run

    async def get_backtest_run(self, run_id: str) -> Optional[BacktestRun]:
        stmt = select(BacktestRun).where(BacktestRun.id == run_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_backtest_runs(
        self, strategy: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[BacktestRun]:
        stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc())
        if strategy:
            stmt = stmt.where(BacktestRun.strategy == strategy)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_backtest_history(self, limit: int = 50) -> List[BacktestRun]:
        """Alias for get_backtest_runs."""
        return await self.get_backtest_runs(limit=limit)

    async def update_backtest_run(self, run_id: str, **kwargs: Any) -> Optional[BacktestRun]:
        obj = await self.get_backtest_run(run_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key in ("parameters", "results", "equity_curve", "extra") and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_backtest_run(self, run_id: str) -> bool:
        stmt = delete(BacktestRun).where(BacktestRun.id == run_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        await self.session.commit()
        return result.rowcount > 0

    # ────────────────────────────────────────
    # DAILY SUMMARY
    # ────────────────────────────────────────

    async def create_daily_summary(self, **kwargs) -> DailySummary:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
            "updated_at": _ist_now(),
        }
        if "strategies_used" in kwargs and isinstance(kwargs["strategies_used"], (list, dict, tuple, set)):
            kwargs["strategies_used"] = _to_json(kwargs["strategies_used"])
        if "sector_pnl" in kwargs and isinstance(kwargs["sector_pnl"], (dict, list, tuple, set)):
            kwargs["sector_pnl"] = _to_json(kwargs["sector_pnl"])
        if "extra" in kwargs and isinstance(kwargs["extra"], (dict, list, tuple, set)):
            kwargs["extra"] = _to_json(kwargs["extra"])
        data.update(kwargs)
        valid_cols = {c.name for c in DailySummary.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = DailySummary(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_daily_summary(self, date_str: str) -> Optional[DailySummary]:
        stmt = select(DailySummary).where(DailySummary.date == date_str)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_daily_summary(self) -> Optional[DailySummary]:
        stmt = select(DailySummary).order_by(DailySummary.date.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_prior_daily_summary(self, before_date: Optional[str] = None) -> Optional[DailySummary]:
        """Fetch the most recent daily summary strictly prior to before_date (default: today).

        Ensures carry-forward always references a prior closed trading session,
        preventing self-referential same-day summary loops.
        """
        target_date = before_date or _today_str()
        stmt = select(DailySummary).where(DailySummary.date < target_date).order_by(DailySummary.date.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_daily_summaries(self, limit: int = 30, offset: int = 0) -> List[DailySummary]:
        stmt = select(DailySummary).order_by(DailySummary.date.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_daily_summary(self, date_str: str, **kwargs) -> Optional[DailySummary]:
        obj = await self.get_daily_summary(date_str)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if key in ("strategies_used", "sector_pnl", "extra") and isinstance(value, (dict, list, tuple, set)):
                value = _to_json(value)
            if hasattr(obj, key):
                setattr(obj, key, value)
        obj.updated_at = _ist_now()
        await self.session.flush()
        await self.session.commit()
        return obj

    async def delete_daily_summary(self, date_str: str) -> bool:
        stmt = delete(DailySummary).where(DailySummary.date == date_str)
        result = await self.session.execute(stmt)
        await self.session.flush()
        await self.session.commit()
        return result.rowcount > 0

    # ────────────────────────────────────────
    # BULK / AGGREGATE HELPERS
    # ────────────────────────────────────────

    async def get_capital_in_use(self) -> float:
        """Sum of invested_amount for all open positions."""
        positions = await self.get_open_positions()
        return sum(p.invested_amount for p in positions)

    async def get_todays_trade_count(self) -> int:
        """Count of all trades (including open) created today."""
        today = _today_str()
        stmt = select(func.count()).select_from(Trade).where(Trade.entry_time.startswith(today))
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def get_todays_closed_trades(self) -> List[Trade]:
        """Get all closed trades from today."""
        today = _today_str()
        stmt = (
            select(Trade)
            .where(Trade.entry_time.startswith(today), Trade.status == "CLOSED")
            .order_by(Trade.exit_time.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_consecutive_losses(self) -> int:
        """Count consecutive losses from the most recent trades."""
        stmt = select(Trade).where(Trade.status == "CLOSED").order_by(Trade.exit_time.desc()).limit(20)
        result = await self.session.execute(stmt)
        trades = list(result.scalars().all())
        count = 0
        for t in trades:
            if t.net_pnl < 0:
                count += 1
            else:
                break
        return count

    async def get_max_drawdown_pct(self, initial_capital: float = 100000.0) -> float:
        """Calculate max drawdown percentage from all closed trades."""
        stmt = select(Trade).where(Trade.status == "CLOSED").order_by(Trade.exit_time.asc())
        result = await self.session.execute(stmt)
        trades = list(result.scalars().all())
        if not trades:
            return 0.0
        peak = float(initial_capital) if initial_capital and initial_capital > 0 else 100000.0
        max_dd = 0.0
        running = peak
        for t in trades:
            running += t.net_pnl
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    async def batch_insert_performance(self, records: List[Dict[str, Any]]) -> None:
        """Batch insert or update strategy performance records."""
        if not records:
            return
        for r in records:
            strategy_name = r.get("strategy", "")
            if not strategy_name:
                continue
            stmt = select(StrategyPerformance).where(StrategyPerformance.strategy == strategy_name)
            res = await self.session.execute(stmt)
            perf = res.scalar_one_or_none()
            pnl = float(r.get("pnl", 0.0))
            is_win = pnl > 0
            is_loss = pnl < 0
            
            if perf is None:
                perf = StrategyPerformance(
                    strategy=strategy_name,
                    total_trades=1,
                    wins=1 if is_win else 0,
                    losses=1 if is_loss else 0,
                    breakeven=0 if (is_win or is_loss) else 1,
                    win_rate=100.0 if is_win else 0.0,
                    avg_win=pnl if is_win else 0.0,
                    avg_loss=pnl if is_loss else 0.0,
                    total_pnl=pnl,
                    max_win=pnl if is_win else 0.0,
                    max_loss=pnl if is_loss else 0.0,
                    profit_factor=1.0,
                    avg_holding_seconds=float(r.get("holding_time_seconds", 0.0)),
                    sharpe_ratio=0.0,
                    max_consecutive_wins=1 if is_win else 0,
                    max_consecutive_losses=1 if is_loss else 0,
                    is_enabled=True,
                    daily_stats="{}",
                    extra="{}",
                )
                self.session.add(perf)
            else:
                perf.total_trades += 1
                if is_win:
                    perf.wins += 1
                elif is_loss:
                    perf.losses += 1
                else:
                    perf.breakeven += 1
                perf.win_rate = (perf.wins / perf.total_trades * 100.0) if perf.total_trades > 0 else 0.0
                perf.total_pnl += pnl
                if is_win and pnl > perf.max_win:
                    perf.max_win = pnl
                if is_loss and pnl < perf.max_loss:
                    perf.max_loss = pnl
                perf.updated_at = _ist_now()

        await self.session.flush()
        await self.session.commit()

    # ────────────────────────────────────────
    # SHADOW OUTCOMES (v0.4.11 — ML clock)
    # ────────────────────────────────────────

    async def create_shadow_outcome(self, **kwargs) -> ShadowOutcome:
        data = {
            "id": str(uuid.uuid4()),
            "created_at": _ist_now(),
        }
        if "blocking_gates" in kwargs and isinstance(kwargs["blocking_gates"], (dict, list, tuple, set)):
            kwargs["blocking_gates"] = _to_json(kwargs["blocking_gates"])
        data.update(kwargs)
        valid_cols = {c.name for c in ShadowOutcome.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in valid_cols}
        obj = ShadowOutcome(**filtered_data)
        return await self._add_and_flush(obj)

    async def get_shadow_outcomes_today(self) -> List[ShadowOutcome]:
        today = _today_str()
        stmt = (
            select(ShadowOutcome)
            .where(ShadowOutcome.created_at.startswith(today))
            .order_by(ShadowOutcome.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_shadow_clock(self) -> Dict[str, Any]:
        """Aggregate today's resolved shadow outcomes into the ML clock.

        Ladder rule: only samples with feed_realtime_registered AND
        feed_realtime_resolved count toward the >=100 resolved-signal
        promotion clock. Backup-feed rows are recorded but flagged out.
        """
        try:
            rows = await self.get_shadow_outcomes_today()
        except Exception:
            return {
                "resolved_today": 0, "realtime_resolved": 0,
                "wins": 0, "losses": 0, "expired": 0, "win_rate_pct": 0.0,
                "per_strategy": {},
            }
        realtime = [r for r in rows if r.feed_realtime_registered and r.feed_realtime_resolved]
        wins = sum(1 for r in realtime if r.outcome == "SHADOW_TARGET")
        losses = sum(1 for r in realtime if r.outcome == "SHADOW_SL")
        expired = sum(1 for r in realtime if r.outcome == "SHADOW_EXPIRED")
        resolved = len(realtime)
        per_strategy: Dict[str, Dict[str, Any]] = {}
        for r in realtime:
            bucket = per_strategy.setdefault(r.strategy, {
                "resolved": 0, "wins": 0, "losses": 0, "expired": 0, "pnl_sum": 0.0,
            })
            bucket["resolved"] += 1
            bucket["pnl_sum"] = round(bucket["pnl_sum"] + float(r.pnl_per_share or 0.0), 2)
            if r.outcome == "SHADOW_TARGET":
                bucket["wins"] += 1
            elif r.outcome == "SHADOW_SL":
                bucket["losses"] += 1
            else:
                bucket["expired"] += 1
        return {
            "resolved_today": len(rows),
            "realtime_resolved": resolved,
            "wins": wins,
            "losses": losses,
            "expired": expired,
            "win_rate_pct": round(wins / resolved * 100.0, 2) if resolved else 0.0,
            "per_strategy": per_strategy,
        }
