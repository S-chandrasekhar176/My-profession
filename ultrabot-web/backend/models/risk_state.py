from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class GateResult(BaseModel):
    """Result of a single risk gate check."""
    gate_name: str
    passed: bool
    message: str
    value: Optional[float] = None
    threshold: Optional[float] = None
    severity: str = "info"  # info, warning, critical


class DailyRiskStatus(BaseModel):
    """Aggregated daily risk status."""
    date: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    net_pnl: float = 0.0
    net_pnl_pct: float = 0.0
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses_hit: bool = False
    daily_trade_limit_hit: bool = False
    daily_loss_limit_hit: bool = False
    max_drawdown_pct: float = 0.0
    drawdown_limit_hit: bool = False
    capital_in_use: float = 0.0
    capital_usage_pct: float = 0.0
    open_positions: int = 0
    max_positions_hit: bool = False
    in_cooloff: bool = False
    cooloff_until: Optional[str] = None
    vix: Optional[float] = None
    vix_above_threshold: bool = False
    regime: Optional[str] = None
    can_take_new_trades: bool = True
    block_reason: Optional[str] = None


class RiskResult(BaseModel):
    """Result of running all risk gates for a signal."""
    passed: bool
    all_gates: List[GateResult] = Field(default_factory=list)
    blocked_by: Optional[str] = None
    block_reason: Optional[str] = None
    severity: str = "info"  # info, warning, critical
    reduced_size: bool = False
    notes: Optional[str] = None


class SizingResult(BaseModel):
    """Position sizing result."""
    method: str
    raw_fraction: float
    adjusted_fraction: float
    confidence_multiplier: float
    volatility_multiplier: float
    drawdown_multiplier: float
    capital_available: float
    position_size: float
    position_size_pct: float
    risk_amount: float
    risk_pct: float
    confidence_tier: str
    volatility_tier: str
    drawdown_tier: str
    quantity: int = 0
    lot_size: Optional[int] = None
    is_equity: bool = True
    notes: Optional[str] = None


class BookingLevels(BaseModel):
    """Partial booking levels for a position."""
    level: int
    rr_ratio: float = 0.0
    book_pct: float = 0.0
    trigger_price: float = 0.0
    trigger_pct: Optional[float] = None
    stage_name: Optional[str] = None
    booked: bool = False
    booked_at: Optional[str] = None
    booked_price: Optional[float] = None
    booked_qty: Optional[int] = None
    trail_pct: Optional[float] = None


class BookingResult(BaseModel):
    """Result of partial booking logic."""
    enabled: bool
    current_level: int
    levels: List[BookingLevels] = Field(default_factory=list)
    trailing_sl_active: bool = False
    current_trailing_sl: Optional[float] = None
    trailing_method: Optional[str] = None
    trailing_step_pct: Optional[float] = None
    triggered_level: Optional[int] = None
    stage_name: Optional[str] = None
    book_pct: Optional[float] = None
    book_qty: Optional[int] = None
    remaining_qty: Optional[int] = None
    stages_fired: List[int] = Field(default_factory=list)
    peak_price: Optional[float] = None
    move_pct: Optional[float] = None



class RiskStateResponse(BaseModel):
    """Full risk state for the dashboard."""
    daily_risk: DailyRiskStatus
    open_positions_by_sector: Dict[str, int] = Field(default_factory=dict)
    capital_in_use: float = 0.0
    capital_available: float = 0.0
    capital_usage_pct: float = 0.0
    vix: Optional[float] = None
    regime: Optional[str] = None
    last_updated: str
