import json
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class TradeCreate(BaseModel):
    session_id: Optional[str] = None
    signal_id: Optional[str] = None
    position_id: Optional[str] = None
    symbol: str
    direction: str = Field(..., pattern=r"^(LONG|SHORT)$")
    strategy: str
    entry_price: float = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    stop_loss: Optional[float] = Field(None, gt=0)
    target: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class TradeResponse(BaseModel):
    id: str
    session_id: Optional[str] = None
    signal_id: Optional[str] = None
    position_id: Optional[str] = None
    symbol: str
    direction: str
    strategy: str
    entry_price: float
    exit_price: Optional[float] = None
    quantity: int
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    actual_sl: Optional[float] = None
    actual_target: Optional[float] = None
    status: str
    exit_reason: Optional[str] = None
    entry_time: str
    exit_time: Optional[str] = None
    pnl: float
    pnl_pct: float
    brokerage: float
    fees: float
    net_pnl: float
    holding_duration_seconds: Optional[int] = None
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v or []

    @field_validator("extra", mode="before")
    @classmethod
    def parse_extra_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v or {}

    model_config = {"from_attributes": True}


class TradeDetailResponse(TradeResponse):
    """Extended trade response with computed fields."""
    holding_duration: Optional[str] = None
    sl_distance: Optional[float] = None
    sl_distance_pct: Optional[float] = None
    target_distance: Optional[float] = None
    target_distance_pct: Optional[float] = None
    risk_reward: Optional[float] = None
    invested_amount: Optional[float] = None


class PositionModifySL(BaseModel):
    new_sl: float = Field(..., gt=0)


class PositionModifyTarget(BaseModel):
    new_target: float = Field(..., gt=0)


class PositionClose(BaseModel):
    exit_price: float = Field(..., gt=0)
    exit_reason: str = Field(default="MANUAL", pattern=r"^(TARGET|SL|MANUAL|PARTIAL_BOOK|EOD)$")
    notes: Optional[str] = None


# Re-export PositionResponse from models.position to prevent duplicate schema definitions
from models.position import PositionResponse
