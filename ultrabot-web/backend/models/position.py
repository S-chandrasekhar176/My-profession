import json
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List


class PositionCreate(BaseModel):
    session_id: Optional[str] = None
    trade_id: Optional[str] = None
    signal_id: Optional[str] = None
    symbol: str
    direction: str = Field(..., pattern=r"^(LONG|SHORT)$")
    strategy: str
    entry_price: float = Field(..., gt=0)
    current_price: Optional[float] = Field(None, gt=0)
    quantity: int = Field(..., gt=0)
    invested_amount: float = Field(default=0.0, ge=0)
    stop_loss: Optional[float] = Field(None, gt=0)
    target: Optional[float] = Field(None, gt=0)
    initial_sl: Optional[float] = Field(None, gt=0)
    initial_target: Optional[float] = Field(None, gt=0)
    extra: Dict[str, Any] = Field(default_factory=dict)


class PositionResponse(BaseModel):
    id: str
    session_id: Optional[str] = None
    trade_id: Optional[str] = None
    signal_id: Optional[str] = None
    symbol: str
    direction: str
    strategy: str
    entry_price: float
    current_price: Optional[float] = None
    quantity: int
    invested_amount: float
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    initial_sl: Optional[float] = None
    initial_target: Optional[float] = None
    booked_qty: int
    booked_pnl: float
    remaining_qty: int
    status: str
    entry_time: str
    exit_time: Optional[str] = None
    unrealized_pnl: float
    realized_pnl: float
    max_favorable_excursion: float
    max_adverse_excursion: float
    trailing_sl_active: bool
    current_trailing_sl: Optional[float] = None
    partial_book_level: int
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

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
