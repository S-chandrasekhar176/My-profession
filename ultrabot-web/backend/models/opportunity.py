"""Pydantic V2 model for Opportunity responses – the enriched signal card."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class OpportunityResponse(BaseModel):
    """Full opportunity card returned by the scanner/engine.

    Contains signal data, strategy info, Kronos score, win rate,
    equity/options details, 13 risk gate results, market context,
    capital info, and expiry.
    """
    # Identity
    id: str
    signal_id: str
    created_at: str

    # Core signal
    symbol: str
    name: Optional[str] = None
    direction: str  # LONG / SHORT
    strategy: str
    confidence: float = Field(ge=0.0, le=1.0)

    # Prices
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    target: float = Field(gt=0)
    risk_reward: float
    sl_distance_pct: float
    target_pct: float

    # Strategy stats
    kronos_score: Optional[float] = None
    win_rate: Optional[float] = None
    avg_rr: Optional[float] = None
    total_trades: Optional[int] = None
    avg_holding_seconds: Optional[float] = None

    # Equity / Options detail
    is_equity: bool = True
    segment: Optional[str] = "EQ"
    lot_size: Optional[int] = None
    expiry_date: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    option_symbol: Optional[str] = None
    option_premium: Optional[float] = None
    premium: Optional[float] = None
    iv: Optional[float] = None
    iv_rank: Optional[float] = None
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None


    # 13 risk gate results
    risk_gate_passed: bool = True
    risk_gates: Dict[str, Any] = Field(default_factory=dict)
    # Expected gate keys:
    #   1. trade_window_gate
    #   2. daily_trade_limit_gate
    #   3. daily_loss_limit_gate
    #   4. max_open_positions_gate
    #   5. sector_concentration_gate
    #   6. consecutive_loss_gate
    #   7. vix_gate
    #   8. signal_confidence_gate
    #   9. drawdown_gate
    #   10. capital_usage_gate
    #   11. position_size_gate
    #   12. price_mismatch_gate
    #   13. regime_compatibility_gate

    # Market context
    vix: Optional[float] = None
    regime: Optional[str] = None
    nifty_change_pct: Optional[float] = None
    sector: Optional[str] = None
    ad_ratio: Optional[float] = None
    market_trend: Optional[str] = None  # BULL, BEAR, SIDEWAYS, VOLATILE

    # Capital info
    capital_required: Optional[float] = None
    position_size: Optional[float] = None
    position_size_pct: Optional[float] = None
    risk_amount: Optional[float] = None
    risk_pct: Optional[float] = None

    # Partial booking levels
    booking_levels: Optional[List[Dict[str, Any]]] = None

    # Sizing result
    sizing_method: Optional[str] = None
    kelly_fraction: Optional[float] = None
    volatility_tier: Optional[str] = None
    drawdown_tier: Optional[str] = None
    confidence_tier: Optional[str] = None

    # Signal-specific data from strategy
    signal_data: Dict[str, Any] = Field(default_factory=dict)

    # Expiry countdown
    days_to_expiry: Optional[int] = None
    is_expiry_week: bool = False

    # Flags
    is_reduced_size: bool = False
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
