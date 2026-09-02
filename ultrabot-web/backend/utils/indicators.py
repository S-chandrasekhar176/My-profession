import numpy as np
import pandas as pd
from typing import Optional, Tuple


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average.

    Args:
        series: Price series (typically close prices).
        period: Lookback window.

    Returns:
        Series of SMA values. NaN where insufficient data.
    """
    return series.rolling(window=period, min_periods=period).mean()


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average.

    Args:
        series: Price series.
        period: EMA span.

    Returns:
        Series of EMA values.
    """
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing.

    Args:
        series: Price series (close prices).
        period: RSI lookback (default 14).

    Returns:
        Series of RSI values between 0 and 100.
    """
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rs = rs.replace([np.inf, -np.inf], 100.0)

    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def calculate_macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD indicator.

    Args:
        series: Close price series.
        fast_period: Fast EMA period.
        slow_period: Slow EMA period.
        signal_period: Signal line period.

    Returns:
        Tuple of (macd_line, signal_line, histogram).
    """
    fast_ema = series.ewm(span=fast_period, adjust=False).mean()
    slow_ema = series.ewm(span=slow_period, adjust=False).mean()

    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram



def calculate_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """Supertrend indicator.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ATR period.
        multiplier: ATR multiplier for bands.

    Returns:
        Tuple of (supertrend_value, direction).
        direction: +1 for bullish, -1 for bearish.
    """
    atr = calculate_atr(high, low, close, period)

    hl2 = (high + low) / 2.0

    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)

    supertrend = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(index=close.index, dtype=int)

    # Initialize first valid row
    first_valid = period  # First row where ATR is available
    if first_valid >= len(close):
        return supertrend, direction

    supertrend.iloc[first_valid] = upper_band.iloc[first_valid]
    direction.iloc[first_valid] = -1

    for i in range(first_valid + 1, len(close)):
        # Lower band
        if lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
            new_lower = lower_band.iloc[i]
        else:
            new_lower = lower_band.iloc[i - 1]

        # Upper band
        if upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
            new_upper = upper_band.iloc[i]
        else:
            new_upper = upper_band.iloc[i - 1]

        prev_dir = direction.iloc[i - 1]

        if prev_dir == -1:
            if close.iloc[i] > new_upper:
                direction.iloc[i] = 1
                supertrend.iloc[i] = new_lower
            else:
                direction.iloc[i] = -1
                supertrend.iloc[i] = new_upper
        else:
            if close.iloc[i] < new_lower:
                direction.iloc[i] = -1
                supertrend.iloc[i] = new_upper
            else:
                direction.iloc[i] = 1
                supertrend.iloc[i] = new_lower

    return supertrend, direction


def calculate_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """Volume Weighted Average Price (intraday).

    Resets at the start of each day (assumes index is datetime).

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        volume: Volume series.

    Returns:
        Series of VWAP values.
    """
    typical_price = (high + low + close) / 3.0
    if hasattr(typical_price.index, "date"):
        group_key = typical_price.index.date
    elif isinstance(typical_price.index, pd.DatetimeIndex):
        group_key = typical_price.index.date
    else:
        group_key = np.zeros(len(typical_price), dtype=int)

    cumulative_tp_vol = (typical_price * volume).groupby(group_key).cumsum()
    cumulative_vol = volume.groupby(group_key).cumsum()

    # Prevent division by zero if volume is 0
    cumulative_vol = cumulative_vol.replace(0, np.nan)
    vwap = cumulative_tp_vol / cumulative_vol
    return vwap.fillna(typical_price)


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands.

    Args:
        series: Price series.
        period: SMA period.
        num_std: Number of standard deviations.

    Returns:
        Tuple of (upper_band, middle_band, lower_band).
    """
    middle = calculate_sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    return upper, middle, lower


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: ATR lookback.

    Returns:
        Series of ATR values.
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return atr


def calculate_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average Directional Index (Wilder).

    ADX measures trend STRENGTH (not direction). Values below ~20 indicate a
    choppy/range-bound market where breakout strategies get whipsawed.

    Args:
        high: High prices.
        low: Low prices.
        close: Close prices.
        period: DX smoothing period (standard 14).

    Returns:
        Series of ADX values (NaN until enough bars exist).
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # Directional movement
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

    # True Range
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder smoothing (same EWM formulation as calculate_atr)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx


def calculate_z_score(series: pd.Series, period: int = 20) -> pd.Series:
    """Z-Score (standardized) of a series.

    Args:
        series: Price series.
        period: Lookback for mean/std.

    Returns:
        Series of z-score values.
    """
    mean = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std()
    std = std.replace(0, np.nan)  # Avoid division by zero
    z = (series - mean) / std
    return z


def calculate_rate_of_change(series: pd.Series, period: int = 10) -> pd.Series:
    """Rate of Change (ROC) indicator.

    Args:
        series: Price series.
        period: Lookback period.

    Returns:
        Series of ROC percentage values.
    """
    prev = series.shift(period)
    roc = ((series - prev) / prev) * 100.0
    return roc


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume (OBV).

    Args:
        close: Close price series.
        volume: Volume series.

    Returns:
        Series of OBV values.
    """
    diff = close.diff()
    direction = pd.Series(0, index=close.index, dtype=float)
    direction[diff > 0] = 1.0
    direction[diff < 0] = -1.0
    obv = (direction * volume).cumsum()
    return obv

