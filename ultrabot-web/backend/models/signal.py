"""Pydantic V2 models for Signal-related requests/responses."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class SignalCreate(BaseModel):
    session_id: Optional[str] = None
    symbol: str
    direction: str = Field(..., pattern=r"^(LONG|SHORT)$")
    strategy: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    entry_price: Optional[float] = Field(None, gt=0)
    stop_loss: Optional[float] = Field(None, gt=0)
    target: Optional[float] = Field(None, gt=0)
    risk_reward: Optional[float] = None
    kronos_score: Optional[float] = None
    vix_at_signal: Optional[float] = None
    regime_at_signal: Optional[str] = None
    sector: Optional[str] = None
    lot_size: Optional[int] = None
    signal_data: Any = Field(default_factory=dict)
    risk_gate_results: Any = Field(default_factory=dict)


class SignalResponse(BaseModel):
    id: str
    session_id: Optional[str] = None
    symbol: str
    direction: str
    strategy: str
    confidence: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    risk_reward: Optional[float] = None
    status: str
    rejection_reason: Optional[str] = None
    kronos_score: Optional[float] = None
    vix_at_signal: Optional[float] = None
    regime_at_signal: Optional[str] = None
    sector: Optional[str] = None
    lot_size: Optional[int] = None
    signal_data: Any = Field(default_factory=dict)
    risk_gate_results: Any = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class SignalWithOpportunity(SignalResponse):
    """Signal enriched with opportunity data for the opportunity card."""
    opportunity_id: Optional[str] = None
    win_rate: Optional[float] = None
    avg_rr: Optional[float] = None
    capital_required: Optional[float] = None
    position_size: Optional[float] = None
    is_equity: bool = True
    lot_size: Optional[int] = None
    expiry_date: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    risk_gate_passed: bool = True
    risk_gate_details: Dict[str, Any] = Field(default_factory=dict)
