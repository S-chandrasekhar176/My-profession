from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class StrategyConfig(BaseModel):
    """Strategy configuration with parameters."""
    name: str
    display_name: str
    description: str = ""
    is_enabled: bool = True
    direction: str = "BOTH"  # LONG, SHORT, BOTH
    timeframe: str = "5min"
    default_stop_loss_pct: float = Field(default=1.0, gt=0)
    default_target_pct: float = Field(default=2.0, gt=0)
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    max_capital_pct: float = Field(default=25.0, gt=0)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class StrategyConfigUpdate(BaseModel):
    """Update fields for a strategy config."""
    display_name: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    direction: Optional[str] = None
    timeframe: Optional[str] = None
    default_stop_loss_pct: Optional[float] = Field(None, gt=0)
    default_target_pct: Optional[float] = Field(None, gt=0)
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_capital_pct: Optional[float] = Field(None, gt=0)
    parameters: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None


class StrategyToggleRequest(BaseModel):
    """Enable/disable a strategy."""
    is_enabled: bool


class StrategyPerformanceResponse(BaseModel):
    """Strategy performance metrics."""
    id: str
    strategy: str
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    avg_win: float
    avg_loss: float
    total_pnl: float
    max_win: float
    max_loss: float
    profit_factor: float
    avg_holding_seconds: float
    sharpe_ratio: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    is_enabled: bool
    daily_stats: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
