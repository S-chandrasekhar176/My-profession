"""Pydantic V2 models for candle data."""
from pydantic import BaseModel, Field
from typing import List, Optional


class Candle(BaseModel):
    """A single OHLCV candle."""
    timestamp: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)

    model_config = {"from_attributes": True}


class CandleCreate(BaseModel):
    """Create a single candle."""
    timestamp: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)


class CandleBatch(BaseModel):
    """Batch of candles for bulk insert."""
    symbol: str
    timeframe: str  # 1min, 5min, 15min, 60min, 1D
    candles: List[CandleCreate]
