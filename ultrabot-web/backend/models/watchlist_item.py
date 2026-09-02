"""Pydantic V2 models for Watchlist items."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class WatchlistItemCreate(BaseModel):
    symbol: str
    name: str
    sector: Optional[str] = None
    lot_size: Optional[int] = None
    is_fno: bool = True
    is_active: bool = True
    extra: Dict[str, Any] = Field(default_factory=dict)


class WatchlistItemResponse(BaseModel):
    id: str
    symbol: str
    name: str
    sector: Optional[str] = None
    lot_size: Optional[int] = None
    is_fno: bool
    is_active: bool
    added_at: str
    last_scanned_at: Optional[str] = None
    last_signal_at: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
