import pandas as pd
from typing import List, Dict, Any, Optional


def resample_candles(
    candles: List[Dict[str, Any]],
    target_timeframe: str,
    source_timeframe: str = "1min",
) -> List[Dict[str, Any]]:
    """Resample a list of candles to a higher timeframe.

    Args:
        candles: List of candle dicts, each with keys:
                 timestamp, open, high, low, close, volume.
        target_timeframe: Target timeframe (e.g., "5min", "15min", "60min", "1D").
        source_timeframe: Source timeframe (default "1min").

    Returns:
        List of resampled candle dicts with the same keys.
    """
    if not candles:
        return []

    df = pd.DataFrame(candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")

    # Determine resampling rule
    rule = _timeframe_to_pandas_rule(target_timeframe)

    # Resample OHLCV
    resampled = df.resample(rule).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )

    # Drop rows with NaN (empty periods)
    resampled = resampled.dropna(subset=["open"])

    # Convert back to list of dicts
    result = []
    for idx, row in resampled.iterrows():
        result.append({
            "timestamp": idx.isoformat(),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
        })

    return result


def aggregate_candles(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of candles into a single summary candle.

    The open is the first candle's open, close is the last candle's close,
    high is the max high, low is the min low, volume is the sum.

    Args:
        candles: List of candle dicts.

    Returns:
        Single aggregated candle dict, or empty dict if no candles.
    """
    if not candles:
        return {}

    return {
        "timestamp": candles[0]["timestamp"],
        "open": candles[0]["open"],
        "high": max(c["high"] for c in candles),
        "low": min(c["low"] for c in candles),
        "close": candles[-1]["close"],
        "volume": sum(c["volume"] for c in candles),
    }


def _timeframe_to_pandas_rule(timeframe: str) -> str:
    """Convert a timeframe string to a pandas resampling rule.

    Args:
        timeframe: e.g., "1min", "5min", "15min", "30min", "60min", "1D".

    Returns:
        Pandas offset alias string.
    """
    mapping = {
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "30min": "30min",
        "60min": "1h",
        "1h": "1h",
        "4h": "4h",
        "1D": "1D",
        "daily": "1D",
        "1W": "1W",
        "weekly": "1W",
        "1ME": "1ME",
        "monthly": "1ME",
    }
    return mapping.get(timeframe, timeframe)


def candles_to_dataframe(candles: Any) -> pd.DataFrame:
    """Convert raw candle records to a normalized pandas DataFrame with a DatetimeIndex.

    Ensures:
    1. Columns are lowercase ('open', 'high', 'low', 'close', 'volume').
    2. DatetimeIndex is assigned from 'timestamp', 'datetime', 'date', or 'time'.
    3. Non-empty DataFrame guarantees DatetimeIndex for strategies/indicators.
    """
    if candles is None:
        return pd.DataFrame()

    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    elif isinstance(candles, (list, tuple)):
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
    elif isinstance(candles, dict):
        df = pd.DataFrame([candles])
    else:
        return pd.DataFrame()

    if df.empty:
        return df

    # Normalize column names to lowercase
    rename_map = {}
    for col in df.columns:
        c_str = str(col).strip().lower()
        if c_str in ["open", "high", "low", "close", "volume", "timestamp", "datetime", "date", "time"]:
            rename_map[col] = c_str
    if rename_map:
        df.rename(columns=rename_map, inplace=True)

    # Set DatetimeIndex if not already a DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        for ts_col in ("timestamp", "datetime", "date", "time"):
            if ts_col in df.columns:
                try:
                    df[ts_col] = pd.to_datetime(df[ts_col])
                    df.set_index(ts_col, inplace=True)
                    break
                except Exception:
                    pass

    return df
