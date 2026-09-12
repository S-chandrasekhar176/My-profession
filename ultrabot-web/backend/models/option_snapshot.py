"""Pydantic V2 models for Option Snapshots (Phase 2)."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class OptionStrikeItem(BaseModel):
    symbol: str
    strike: float
    option_type: str  # CE or PE
    ltp: float
    oi: int
    volume: int
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


class OptionSnapshotBase(BaseModel):
    timestamp: str
    underlying_symbol: str
    spot_price: float
    expiry: str
    atm_strike: float
    max_pain: Optional[float] = None
    pcr: float = 1.0
    total_ce_oi: int = 0
    total_pe_oi: int = 0
    tier: str = "tradable"


class OptionSnapshotCreate(OptionSnapshotBase):
    expiry_epoch: Optional[int] = None
    chain_json: str = "[]"


class OptionSnapshotOut(OptionSnapshotBase):
    id: str
    expiry_epoch: Optional[int] = None
    chain_data: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str

    model_config = {"from_attributes": True}
