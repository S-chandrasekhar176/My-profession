"""Pydantic V2 models for backtest requests/responses."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class BacktestRequest(BaseModel):
    """Request to start a backtest run."""
    strategy: str
    symbol: Optional[str] = None
    start_date: str
    end_date: str
    timeframe: str = "5min"
    initial_capital: float = Field(default=100000.0, gt=0)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class BacktestResponse(BaseModel):
    """Detailed backtest result."""
    id: str
    strategy: str
    symbol: Optional[str] = None
    start_date: str
    end_date: str
    timeframe: str
    initial_capital: float
    status: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    parameters: Dict[str, Any] = Field(default_factory=dict)
    results: Dict[str, Any] = Field(default_factory=dict)
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class BacktestStatusResponse(BaseModel):
    """Brief status of a backtest run."""
    id: str
    strategy: str
    status: str
    progress_pct: float = 0.0
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    model_config = {"from_attributes": True}


class BacktestHistoryResponse(BaseModel):
    """List of past backtest runs with summary."""
    runs: List[BacktestResponse] = Field(default_factory=list)
    total: int = 0
