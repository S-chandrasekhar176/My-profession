"""
SQLAlchemy 2.0 ORM models for UltraBot Web.
All tables use TEXT primary keys with UUID values.
JSON fields are stored as TEXT and use json.dumps/json.loads.
"""
import uuid
from datetime import datetime, date
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Column, Text, Integer, Float, Boolean, DateTime, Date, String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

IST = ZoneInfo("Asia/Kolkata")


class Base(DeclarativeBase):
    pass


def _generate_uuid() -> str:
    return str(uuid.uuid4())


def _ist_now() -> datetime:
    return datetime.now(IST)


# ──────────────────────────────────────────────
# 1. sessions
# ──────────────────────────────────────────────
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    date: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[str] = mapped_column(Text, nullable=False)
    end_time: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")  # running, completed, error
    engine_state: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 2. trades
# ──────────────────────────────────────────────
class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    signal_id: Mapped[str] = mapped_column(Text, nullable=True)
    position_id: Mapped[str] = mapped_column(Text, nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # LONG, SHORT
    strategy: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=True)
    target: Mapped[float] = mapped_column(Float, nullable=True)
    actual_sl: Mapped[float] = mapped_column(Float, nullable=True)
    actual_target: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OPEN", index=True)  # OPEN, CLOSED, CANCELLED
    exit_reason: Mapped[str] = mapped_column(Text, nullable=True)  # TARGET, SL, MANUAL, PARTIAL_BOOK
    entry_time: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat(), index=True)
    exit_time: Mapped[str] = mapped_column(Text, nullable=True)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    brokerage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    holding_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 3. signals
# ──────────────────────────────────────────────
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # LONG, SHORT
    strategy: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=True)
    target: Mapped[float] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING", index=True)  # PENDING, ACCEPTED, REJECTED, EXPIRED, ACTED_UPON
    rejection_reason: Mapped[str] = mapped_column(Text, nullable=True)
    kronos_score: Mapped[float] = mapped_column(Float, nullable=True)
    vix_at_signal: Mapped[float] = mapped_column(Float, nullable=True)
    regime_at_signal: Mapped[str] = mapped_column(Text, nullable=True)
    sector: Mapped[str] = mapped_column(Text, nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=True)
    signal_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    risk_gate_results: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 4. positions
# ──────────────────────────────────────────────
class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    trade_id: Mapped[str] = mapped_column(Text, nullable=True)
    signal_id: Mapped[str] = mapped_column(Text, nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # LONG, SHORT
    strategy: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    invested_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=True)
    target: Mapped[float] = mapped_column(Float, nullable=True)
    initial_sl: Mapped[float] = mapped_column(Float, nullable=True)
    initial_target: Mapped[float] = mapped_column(Float, nullable=True)
    booked_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    booked_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    remaining_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="OPEN", index=True)  # OPEN, CLOSED, PARTIAL
    entry_time: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    exit_time: Mapped[str] = mapped_column(Text, nullable=True)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_favorable_excursion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_adverse_excursion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trailing_sl_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_trailing_sl: Mapped[float] = mapped_column(Float, nullable=True)
    partial_book_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 5. watchlist
# ──────────────────────────────────────────────
class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=True)
    is_fno: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    added_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    last_scanned_at: Mapped[str] = mapped_column(Text, nullable=True)
    last_signal_at: Mapped[str] = mapped_column(Text, nullable=True)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 6. strategy_performance
# ──────────────────────────────────────────────
class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    strategy: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    breakeven: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_win: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_win: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_holding_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_consecutive_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_stats: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 7. risk_events
# ──────────────────────────────────────────────
class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    session_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # DAILY_LIMIT_HIT, DRAWDOWN_ALERT, etc.
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="info")  # info, warning, critical
    symbol: Mapped[str] = mapped_column(Text, nullable=True)
    strategy: Mapped[str] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=True)
    threshold: Mapped[float] = mapped_column(Float, nullable=True)
    action_taken: Mapped[str] = mapped_column(Text, nullable=True)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 8. broker_credentials
# ──────────────────────────────────────────────
class BrokerCredential(Base):
    __tablename__ = "broker_credentials"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    broker_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_connected_at: Mapped[str] = mapped_column(Text, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 9. error_logs
# ──────────────────────────────────────────────
class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    error_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # ERR-YYYY-MMDD-NNNN (non-unique to allow recurrence)
    error_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="error", index=True)  # info, warning, error, critical
    what_happened: Mapped[str] = mapped_column(Text, nullable=False)
    why_happened: Mapped[str] = mapped_column(Text, nullable=True)
    how_to_fix: Mapped[str] = mapped_column(Text, nullable=True)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    stack_trace: Mapped[str] = mapped_column(Text, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    resolved_at: Mapped[str] = mapped_column(Text, nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=True)
    auto_recovery_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_recovery_result: Mapped[str] = mapped_column(Text, nullable=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=True, index=True)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 10. backtest_runs
# ──────────────────────────────────────────────
class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=True)
    start_date: Mapped[str] = mapped_column(Text, nullable=False)
    end_date: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False, default="5min")
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False, default=100000.0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_win: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    parameters: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    results: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    equity_curve: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=True)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())


# ──────────────────────────────────────────────
# 11. daily_summary
# ──────────────────────────────────────────────
class DailySummary(Base):
    __tablename__ = "daily_summary"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_generate_uuid)
    date: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    breakeven: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gross_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_brokerage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_win: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    best_trade: Mapped[str] = mapped_column(Text, nullable=True)
    worst_trade: Mapped[str] = mapped_column(Text, nullable=True)
    strategies_used: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    sector_pnl: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    starting_capital: Mapped[float] = mapped_column(Float, nullable=False, default=100000.0)
    ending_capital: Mapped[float] = mapped_column(Float, nullable=False, default=100000.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    regime: Mapped[str] = mapped_column(Text, nullable=True)
    vix_close: Mapped[float] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    extra: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=lambda: _ist_now().isoformat())
