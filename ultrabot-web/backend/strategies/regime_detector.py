"""Market Regime Detector with Multi-Timeframe ADX and Volatility Bands.

Classifies the market into 4 primary regimes:
  - Bull: Strong upward momentum, positive market breadth, healthy VIX.
  - Bear: Downward pressure, negative breadth, elevated VIX.
  - Sideways / Choppy: ADX < 20, tight Nifty range, balanced breadth.
  - Volatile: VIX > 22, sharp intraday swings, wider ATRs.
"""
from typing import Dict, Any, Optional, List


class RegimeDetector:
    """Classifies the current market regime based on Nifty, VIX, ADX, and breadth data."""

    def classify(
        self,
        nifty_price: float,
        nifty_day_change_pct: float,
        nifty_5day_change_pct: float = 0.0,
        vix: float = 14.0,
        ad_ratio: float = 1.0,
        adx: Optional[float] = None,
        ema_trend: Optional[str] = None,
        sector_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Classify the current market regime with multi-timeframe ADX metrics.

        Args:
            nifty_price: Current Nifty 50 index price.
            nifty_day_change_pct: Nifty percentage change today.
            nifty_5day_change_pct: Nifty percentage change over 5 days.
            vix: India VIX value.
            ad_ratio: Advance/Decline ratio (advances / declines).
            adx: Optional 14-period ADX trend strength (0-100).
            ema_trend: Optional 'BULLISH', 'BEARISH', or 'NEUTRAL' from 50/200 EMA slope.
            sector_data: Optional dict of sector-level momentum.

        Returns:
            {"regime": str, "confidence": float, "details": dict}
        """
        # Determine Volatility Tier
        if vix < 13.0:
            vix_tier = "Calm"
        elif vix <= 18.0:
            vix_tier = "Normal"
        elif vix <= 24.0:
            vix_tier = "Elevated"
        else:
            vix_tier = "Panic / Extreme Volatility"

        # Determine ADX Trend Strength
        trend_strength = "Moderate"
        if adx is not None:
            if adx >= 30:
                trend_strength = "Very Strong Trend"
            elif adx >= 25:
                trend_strength = "Strong Trend"
            elif adx < 20:
                trend_strength = "Weak / Choppy"

        details: Dict[str, Any] = {
            "nifty_price": nifty_price,
            "nifty_day_change_pct": nifty_day_change_pct,
            "nifty_5day_change_pct": nifty_5day_change_pct,
            "vix": vix,
            "vix_tier": vix_tier,
            "ad_ratio": ad_ratio,
            "adx": adx,
            "trend_strength": trend_strength,
            "ema_trend": ema_trend or "NEUTRAL",
        }

        # ── Bull Score Conditions ──
        bull_conditions = 0
        bull_total = 5
        if nifty_day_change_pct > 0.3:
            bull_conditions += 1
        if nifty_5day_change_pct > 0.8:
            bull_conditions += 1
        if ad_ratio >= 1.3:
            bull_conditions += 1
        if vix < 18.0:
            bull_conditions += 1
        if ema_trend == "BULLISH" or (adx is not None and adx > 25 and nifty_day_change_pct > 0.2):
            bull_conditions += 1

        # ── Bear Score Conditions ──
        bear_conditions = 0
        bear_total = 5
        if nifty_day_change_pct < -0.3:
            bear_conditions += 1
        if nifty_5day_change_pct < -0.8:
            bear_conditions += 1
        if ad_ratio <= 0.75:
            bear_conditions += 1
        if vix > 17.5:
            bear_conditions += 1
        if ema_trend == "BEARISH" or (adx is not None and adx > 25 and nifty_day_change_pct < -0.2):
            bear_conditions += 1

        # ── Volatile Score Conditions ──
        volatile_conditions = 0
        volatile_total = 3
        if vix > 20.0:
            volatile_conditions += 1
        if abs(nifty_day_change_pct) >= 0.7:
            volatile_conditions += 1
        if vix > 23.0 or (adx is not None and adx > 35):
            volatile_conditions += 1

        # ── Sideways Score Conditions ──
        sideways_conditions = 0
        sideways_total = 4
        if -0.3 <= nifty_day_change_pct <= 0.3:
            sideways_conditions += 1
        if 11.5 <= vix <= 17.5:
            sideways_conditions += 1
        if 0.8 <= ad_ratio <= 1.25:
            sideways_conditions += 1
        if adx is not None and adx < 20:
            sideways_conditions += 1

        # Calculate scores
        scores: Dict[str, float] = {
            "Bull": bull_conditions / bull_total,
            "Bear": bear_conditions / bear_total,
            "Volatile": volatile_conditions / volatile_total,
            "Sideways": sideways_conditions / sideways_total,
        }

        # Select highest confidence regime
        regime = max(scores, key=scores.get)
        confidence = scores[regime]

        # Prioritize Volatile when VIX exceeds high volatility threshold (22.0)
        if vix >= 22.0:
            regime = "Volatile"
            confidence = max(confidence, 0.85)
        elif adx is not None and adx < 18.0 and abs(nifty_day_change_pct) < 0.25:
            regime = "Sideways"
            confidence = max(confidence, 0.80)

        details["bull_score"] = round(scores["Bull"], 2)
        details["bear_score"] = round(scores["Bear"], 2)
        details["volatile_score"] = round(scores["Volatile"], 2)
        details["sideways_score"] = round(scores["Sideways"], 2)

        if sector_data:
            details["sector_data"] = sector_data

        return {
            "regime": regime,
            "confidence": round(confidence, 2),
            "details": details,
        }
