import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Scoring weights for each factor
_WEIGHTS = {
    "volume_surge": 0.25,
    "price_momentum": 0.25,
    "technical_pattern": 0.25,
    "news_catalyst": 0.25,
}

# Volume surge thresholds
_VOLUME_SURGE_THRESHOLD = 1.8  # 1.8x average volume = significant surge
_VOLUME_SURGE_EXTREME = 3.0  # 3x = extreme surge

# Momentum thresholds
_MOMENTUM_STRONG = 2.0  # % change for strong momentum
_MOMENTUM_MODERATE = 1.0


class KronosScanner:
    """Rule-based multi-factor scanner that scores stocks on multiple dimensions.

    Scores stocks based on:
    - Volume surge vs. average volume
    - Price momentum (short-term % change)
    - Technical pattern proximity (breakout, support/resistance)
    - News catalyst presence

    Returns scored results sorted by score (highest first).
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or dict(_WEIGHTS)

    def scan(
        self,
        watchlist_symbols: List[str],
        market_data: Dict[str, Dict[str, Any]],
        news_items: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Scan watchlist symbols and return scored results.

        Args:
            watchlist_symbols: List of NSE symbols to scan.
            market_data: Dict keyed by symbol with market data including:
                - ltp: float (last traded price)
                - close: float (previous close)
                - volume: int (current volume)
                - avg_volume: int (average volume over N periods)
                - high: float (day high)
                - low: float (day low)
                - open: float (day open)
                - rsi: float (optional, RSI value)
                - ema_20: float (optional)
                - ema_50: float (optional)
                - support: float (optional, support level)
                - resistance: float (optional, resistance level)
                - vwap: float (optional)
            news_items: Optional list of news items with 'symbols' field.

        Returns:
            List of dicts sorted by score (desc):
            [{symbol, score, reasons, volume_score, momentum_score, pattern_score, news_score}]
        """
        news_items = news_items or []

        # Build news symbol set for quick lookup
        news_symbols = set()
        for item in news_items:
            syms = item.get("symbols", [])
            if isinstance(syms, list):
                news_symbols.update(s.upper() for s in syms)
            elif isinstance(syms, str):
                news_symbols.add(syms.upper())

        results = []
        for symbol in watchlist_symbols:
            data = market_data.get(symbol.upper())
            if data is None:
                continue

            volume_score, volume_reason = self._score_volume(data)
            momentum_score, momentum_reason = self._score_momentum(data)
            pattern_score, pattern_reason = self._score_technical_pattern(symbol, data)
            news_score, news_reason = self._score_news(symbol, news_items, news_symbols)

            total_score = (
                volume_score * self.weights.get("volume_surge", 0.25)
                + momentum_score * self.weights.get("price_momentum", 0.25)
                + pattern_score * self.weights.get("technical_pattern", 0.25)
                + news_score * self.weights.get("news_catalyst", 0.25)
            )

            reasons = []
            if volume_reason:
                reasons.append(volume_reason)
            if momentum_reason:
                reasons.append(momentum_reason)
            if pattern_reason:
                reasons.append(pattern_reason)
            if news_reason:
                reasons.append(news_reason)

            results.append({
                "symbol": symbol.upper(),
                "score": round(total_score, 3),
                "reasons": reasons,
                "volume_score": round(volume_score, 3),
                "momentum_score": round(momentum_score, 3),
                "pattern_score": round(pattern_score, 3),
                "news_score": round(news_score, 3),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    @staticmethod
    def _score_volume(data: Dict[str, Any]) -> tuple:
        """Score volume surge. Returns (score 0-1, reason or empty string)."""
        volume = data.get("volume", 0)
        avg_volume = data.get("avg_volume", 0)

        if avg_volume <= 0 or volume <= 0:
            return 0.0, ""

        ratio = volume / avg_volume

        if ratio >= _VOLUME_SURGE_EXTREME:
            return 1.0, f"Extreme volume surge: {ratio:.1f}x avg"
        elif ratio >= _VOLUME_SURGE_THRESHOLD:
            normalized = (ratio - _VOLUME_SURGE_THRESHOLD) / (_VOLUME_SURGE_EXTREME - _VOLUME_SURGE_THRESHOLD)
            score = 0.5 + 0.5 * min(normalized, 1.0)
            return round(score, 3), f"Volume surge: {ratio:.1f}x avg"
        elif ratio >= 1.2:
            normalized = (ratio - 1.2) / (_VOLUME_SURGE_THRESHOLD - 1.2)
            score = 0.2 + 0.3 * min(normalized, 1.0)
            return round(score, 3), f"Above-average volume: {ratio:.1f}x"
        else:
            return 0.0, ""

    @staticmethod
    def _score_momentum(data: Dict[str, Any]) -> tuple:
        """Score price momentum (directional magnitude). Returns (score 0-1, reason or empty string)."""
        ltp = data.get("ltp", 0)
        prev_close = data.get("close", 0)

        if ltp <= 0 or prev_close <= 0:
            return 0.0, ""

        change_pct = ((ltp - prev_close) / prev_close) * 100.0
        abs_change = abs(change_pct)
        day_high = data.get("high", 0)
        day_low = data.get("low", 0)

        # Bonus for being near extremes (day high for upward, day low for downward)
        range_bonus = 0.0
        if day_high > day_low > 0:
            position_in_range = (ltp - day_low) / (day_high - day_low)
            if position_in_range > 0.9 or position_in_range < 0.1:
                range_bonus = 0.15

        prefix = "+" if change_pct >= 0 else ""
        if abs_change >= _MOMENTUM_STRONG:
            score = min(1.0, 0.7 + (abs_change - _MOMENTUM_STRONG) / 3.0 + range_bonus)
            return round(score, 3), f"Strong momentum: {prefix}{change_pct:.2f}%"
        elif abs_change >= _MOMENTUM_MODERATE:
            normalized = (abs_change - _MOMENTUM_MODERATE) / (_MOMENTUM_STRONG - _MOMENTUM_MODERATE)
            score = 0.4 + 0.3 * min(normalized, 1.0) + range_bonus
            return round(score, 3), f"Moderate momentum: {prefix}{change_pct:.2f}%"
        elif abs_change >= 0.5:
            normalized = (abs_change - 0.5) / (_MOMENTUM_MODERATE - 0.5)
            score = 0.2 + 0.2 * min(normalized, 1.0) + range_bonus
            return round(score, 3), f"Slight momentum: {prefix}{change_pct:.2f}%"
        else:
            return 0.0, ""

    @staticmethod
    def _score_technical_pattern(symbol: str, data: Dict[str, Any]) -> tuple:
        """Score based on technical pattern proximity. Returns (score 0-1, reason)."""
        score = 0.0
        reasons = []

        ltp = data.get("ltp", 0)
        resistance = data.get("resistance", 0)
        support = data.get("support", 0)
        rsi = data.get("rsi")
        ema_20 = data.get("ema_20")
        ema_50 = data.get("ema_50")
        vwap = data.get("vwap")

        if ltp <= 0:
            return 0.0, ""

        # Near resistance breakout
        if resistance > 0:
            distance_pct = ((resistance - ltp) / ltp) * 100
            if distance_pct < 0:  # Already above resistance
                score += 0.6
                reasons.append("Breakout above resistance")
            elif distance_pct < 1.0:  # Within 1% of resistance
                score += 0.4
                reasons.append(f"Near resistance ({distance_pct:.1f}% away)")
            elif distance_pct < 2.5:
                score += 0.2
                reasons.append(f"Approaching resistance ({distance_pct:.1f}% away)")

        # Near support bounce
        if support > 0:
            distance_pct = ((ltp - support) / ltp) * 100
            if 0 < distance_pct < 1.0:
                score += 0.3
                reasons.append(f"Near support ({distance_pct:.1f}% above)")
            elif 1.0 <= distance_pct < 2.5:
                score += 0.15
                reasons.append(f"Above support ({distance_pct:.1f}%)")

        # EMA crossover signals
        if ema_20 and ema_50:
            if ema_20 > ema_50 and ltp > ema_20:
                ema_distance = ((ltp - ema_20) / ema_20) * 100
                if ema_distance < 1.0:
                    score += 0.25
                    reasons.append("Bullish EMA crossover, price near EMA20")
            elif ema_20 > ema_50:
                score += 0.1
                reasons.append("Above both EMAs (bullish)")

        # RSI oversold bounce potential
        if rsi is not None:
            if 30 < rsi < 45:
                score += 0.2
                reasons.append(f"RSI {rsi:.0f} - potential oversold bounce")
            elif rsi < 30:
                score += 0.3
                reasons.append(f"RSI {rsi:.0f} - oversold zone")
            elif 55 < rsi < 70:
                score += 0.15
                reasons.append(f"RSI {rsi:.0f} - bullish momentum")

        # VWAP support
        if vwap and vwap > 0:
            if abs(ltp - vwap) / vwap < 0.005 and ltp > vwap:
                score += 0.2
                reasons.append("Trading above VWAP with tight spread")

        score = min(score, 1.0)
        reason_str = "; ".join(reasons)
        return round(score, 3), reason_str

    @staticmethod
    def _score_news(
        symbol: str,
        news_items: List[Dict[str, Any]],
        news_symbols: set,
    ) -> tuple:
        """Score based on news catalyst. Returns (score 0-1, reason)."""
        if symbol.upper() not in news_symbols:
            return 0.0, ""

        relevant_news = []
        for item in news_items:
            syms = item.get("symbols", [])
            if isinstance(syms, str):
                syms = [syms]
            if symbol.upper() in [s.upper() for s in syms]:
                relevant_news.append(item)

        if not relevant_news:
            return 0.0, ""

        best_score = 0.0
        best_reason = ""

        for item in relevant_news:
            impact = item.get("impact_level", "low").lower()
            sentiment = item.get("sentiment", "neutral").lower()

            score = 0.0
            if impact == "high":
                score = 0.8
            elif impact == "medium":
                score = 0.5
            else:
                score = 0.2

            # Sentiment alignment (positive is bullish)
            if sentiment == "positive":
                score *= 1.2
            elif sentiment == "negative":
                score *= 0.3  # Can still be relevant for shorting

            if score > best_score:
                best_score = score
                headline = item.get("headline", "")
                if impact == "high":
                    best_reason = f"High-impact news: {headline[:80]}"
                elif impact == "medium":
                    best_reason = f"News mention: {headline[:60]}"
                else:
                    best_reason = f"Minor news: {headline[:50]}"

        best_score = min(best_score, 1.0)
        return round(best_score, 3), best_reason
