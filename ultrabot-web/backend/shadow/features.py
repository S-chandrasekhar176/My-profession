"""Point-in-time feature snapshots for shadow signals (v0.4.12).

Computed ONCE at scan time — from the exact candle DataFrame the strategy
saw — attached to the signal, and copied IMMUTABLY into shadow_outcomes at
registration. Nothing here may consult data that was not visible at signal
time: that is the leakage guarantee the ML dataset is built on.

Honesty rules (same spirit as shadow_utils.py):
  - Missing inputs (no volume column, too few candles, garbage values)
    yield None for that feature — never a guess, never an exception.
  - Every snapshot carries ``schema_version`` so a future feature change
    can never silently mix incompatible vectors inside one training set.
  - Rounding to 6 decimal places keeps json.dumps stable across runs.

All functions are pure and unit-testable without the engine or a database.
"""
from typing import Any, Dict, Optional

import pandas as pd

FEATURES_SCHEMA_VERSION = "v1"

# IST intraday session buckets (market hours 09:15–15:30 IST).
SESSION_OPENING_DRIVE = "OPENING_DRIVE"   # 09:15 - 10:00
SESSION_MORNING = "MORNING"               # 10:00 - 11:30
SESSION_LUNCH = "LUNCH"                   # 11:30 - 13:30
SESSION_AFTERNOON = "AFTERNOON"           # 13:30 - 14:45
SESSION_POWER_CLOSE = "POWER_CLOSE"       # 14:45 - 15:30

HTF_FLAT_BAND_PCT = 0.15  # |close - ema20_htf| / ema20_htf within +-0.15% -> flat


def classify_session(ist_dt: Any) -> Optional[str]:
    """IST time-of-day bucket for a signal timestamp. None outside hours."""
    try:
        minutes = int(ist_dt.hour) * 60 + int(ist_dt.minute)
    except (AttributeError, TypeError, ValueError):
        return None
    if 9 * 60 + 15 <= minutes < 10 * 60:
        return SESSION_OPENING_DRIVE
    if 10 * 60 <= minutes < 11 * 60 + 30:
        return SESSION_MORNING
    if 11 * 60 + 30 <= minutes < 13 * 60 + 30:
        return SESSION_LUNCH
    if 13 * 60 + 30 <= minutes < 14 * 60 + 45:
        return SESSION_AFTERNOON
    if 14 * 60 + 45 <= minutes <= 15 * 60 + 30:
        return SESSION_POWER_CLOSE
    return None


def _ohlcv(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Validate the frame has the columns we need; None otherwise."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    needed = {"high", "low", "close"}
    if not needed.issubset(set(map(str, df.columns))):
        return None
    return df


def compute_atr(df: Optional[pd.DataFrame], period: int = 14) -> Optional[float]:
    """Average True Range (simple rolling mean of true range) — last value."""
    d = _ohlcv(df)
    if d is None or len(d) < period + 1:
        return None
    try:
        prev_close = d["close"].shift(1)
        tr = pd.concat(
            [
                d["high"] - d["low"],
                (d["high"] - prev_close).abs(),
                (d["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = float(tr.rolling(period).mean().iloc[-1])
        if not pd.notna(atr) or atr <= 0:
            return None
        return round(atr, 6)
    except Exception:
        return None


def compute_atr_pct(df: Optional[pd.DataFrame], period: int = 14) -> Optional[float]:
    """ATR as a percentage of the last close (volatility, scale-free)."""
    atr = compute_atr(df, period)
    if atr is None or df is None or df.empty:
        return None
    try:
        close = float(df["close"].iloc[-1])
        if close <= 0:
            return None
        return round(atr / close * 100.0, 6)
    except Exception:
        return None


def compute_vwap_distance_pct(df: Optional[pd.DataFrame]) -> Optional[float]:
    """% distance of the last close from the session VWAP (typical-price).

    Needs a usable volume column; None when volume is missing or zero —
    feeds without volume must not silently produce a fake VWAP.
    """
    d = _ohlcv(df)
    if d is None or "volume" not in set(map(str, d.columns)):
        return None
    try:
        vol = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
        total_vol = float(vol.sum())
        if total_vol <= 0:
            return None
        typical = (d["high"] + d["low"] + d["close"]) / 3.0
        vwap = float((typical * vol).sum() / total_vol)
        if vwap <= 0:
            return None
        close = float(d["close"].iloc[-1])
        return round((close - vwap) / vwap * 100.0, 6)
    except Exception:
        return None


def compute_trend_strength(df: Optional[pd.DataFrame]) -> Optional[float]:
    """Normalized EMA-slope proxy: (ema9 - ema21) / ATR14.

    Sign = direction of the short trend; magnitude = strength in ATR units.
    Scale-free by construction, so it is comparable across symbols.
    """
    d = _ohlcv(df)
    if d is None or len(d) < 22:
        return None
    try:
        ema9 = float(d["close"].ewm(span=9, adjust=False).mean().iloc[-1])
        ema21 = float(d["close"].ewm(span=21, adjust=False).mean().iloc[-1])
        atr = compute_atr(d, 14)
        if atr is None or atr <= 0:
            return None
        return round((ema9 - ema21) / atr, 6)
    except Exception:
        return None


def compute_htf_trend(df: Optional[pd.DataFrame]) -> Optional[str]:
    """Higher-timeframe (15-minute) trend: close vs EMA20 of resampled bars.

    "up" | "down" | "flat" — direction-agnostic on purpose: the ML consumer
    combines this with the signal's own direction. None when the window is
    too short for an honest 15m read (needs >= 5 resampled bars).
    """
    d = _ohlcv(df)
    if d is None or len(d) < 30:
        return None
    try:
        htf_close = d["close"].resample("15min").last().dropna()
        if len(htf_close) < 5:
            return None
        ema20 = float(htf_close.ewm(span=20, adjust=False, min_periods=2).mean().iloc[-1])
        if ema20 <= 0:
            return None
        close = float(d["close"].iloc[-1])
        dev_pct = (close - ema20) / ema20 * 100.0
        if dev_pct > HTF_FLAT_BAND_PCT:
            return "up"
        if dev_pct < -HTF_FLAT_BAND_PCT:
            return "down"
        return "flat"
    except Exception:
        return None


def compute_liquidity_ratio(df: Optional[pd.DataFrame]) -> Optional[float]:
    """Relative volume: last bar volume / mean volume over the window.

    >1 = trading heavier than the recent norm. Needs a volume column with
    a positive mean and at least 5 observations.
    """
    d = _ohlcv(df)
    if d is None or "volume" not in set(map(str, d.columns)) or len(d) < 5:
        return None
    try:
        vol = pd.to_numeric(d["volume"], errors="coerce").dropna()
        if len(vol) < 5:
            return None
        mean_vol = float(vol.mean())
        if mean_vol <= 0:
            return None
        return round(float(vol.iloc[-1]) / mean_vol, 6)
    except Exception:
        return None


def compute_feature_snapshot(df: Optional[pd.DataFrame], now: Any = None) -> Dict[str, Any]:
    """Assemble the full point-in-time snapshot. Never raises.

    Returns a JSON-safe dict; every numeric feature is None when its inputs
    were missing/insufficient — the consumer (and the ML stage later) must
    treat None as "not observed", never as zero.
    """
    snapshot: Dict[str, Any] = {
        "schema_version": FEATURES_SCHEMA_VERSION,
        "computed_at": None,
        "session_class": None,
        "atr": None,
        "atr_pct": None,
        "vwap_distance_pct": None,
        "trend_strength": None,
        "htf_trend": None,
        "liquidity_ratio": None,
        "n_candles": 0,
        "has_volume": False,
    }
    try:
        if now is not None:
            snapshot["computed_at"] = getattr(now, "isoformat", lambda: str(now))()
            snapshot["session_class"] = classify_session(now)
    except Exception:
        pass
    d = _ohlcv(df)
    if d is not None:
        snapshot["n_candles"] = int(len(d))
        snapshot["has_volume"] = "volume" in set(map(str, d.columns))
    errors = []
    for key, fn in (
        ("atr", compute_atr),
        ("atr_pct", compute_atr_pct),
        ("vwap_distance_pct", compute_vwap_distance_pct),
        ("trend_strength", compute_trend_strength),
        ("htf_trend", compute_htf_trend),
        ("liquidity_ratio", compute_liquidity_ratio),
    ):
        try:
            snapshot[key] = fn(df)
        except Exception as exc:  # defensive: one broken feature never
            errors.append(f"{key}: {type(exc).__name__}")  # kills the rest
    if errors:
        snapshot["snapshot_error"] = ",".join(errors)
    return snapshot
