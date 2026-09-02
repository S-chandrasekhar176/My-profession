import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Factor weights for signal scoring
_FACTOR_WEIGHTS = {
    "strategy_win_rate": 0.25,
    "regime_alignment": 0.20,
    "volume_confirmation": 0.20,
    "vix_environment": 0.15,
    "trend_alignment": 0.20,
}

# Strategy default win rates (used when no history is available)
_DEFAULT_WIN_RATES = {
    "ORB": 0.58,
    "PTC": 0.55,
    "VC": 0.55,
    "SIC": 0.52,
    "MB": 0.50,
    "MRF": 0.62,
    "TRS": 0.42,
    # Legacy fallbacks
    "Breakout": 0.52,
    "Momentum": 0.50,
    "Supertrend": 0.53,
    "MeanReversion": 0.48,
    "VWAPReversion": 0.50,
    "RSIDivergence": 0.45,
    "GapFill": 0.47,
    "SectorRotation": 0.49,
    "AdaptiveSupertrend": 0.52,
    "ORBVolume": 0.54,
    "MultiTimeframe": 0.51,
    "NewsMomentum": 0.46,
    "TrendExhaustion": 0.44,
}



class SignalScorer:
    """Score trading signals on multiple factors.

    Factors:
    - Strategy historical win rate
    - Regime alignment (bull/bear/sideways/volatile)
    - Volume confirmation
    - VIX environment suitability
    - Trend alignment

    Returns a float between 0.0 and 1.0.
    """

    def __init__(
        self,
        strategy_performance: Optional[Dict[str, Dict[str, Any]]] = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.strategy_performance = strategy_performance or {}
        self.weights = weights or dict(_FACTOR_WEIGHTS)

    def score_signal(
        self,
        signal: Dict[str, Any],
        market_context: Dict[str, Any],
    ) -> float:
        """Score a signal based on multiple factors.

        Args:
            signal: Signal dict with at least:
                - strategy: str (strategy name)
                - direction: str ('LONG' or 'SHORT')
                - symbol: str
                - confidence: float (optional, 0-1)
                - volume_ratio: float (optional, current/avg volume)
            market_context: Market context dict with:
                - regime: str ('Bull', 'Bear', 'Sideways', 'Volatile')
                - vix: float (India VIX value)
                - trend: str ('up', 'down', 'sideways')
                - nifty_change_pct: float (optional)

        Returns:
            Score between 0.0 and 1.0.
        """
        strategy = signal.get("strategy", "")
        direction = signal.get("direction", "LONG")
        regime = market_context.get("regime", "Sideways")
        vix = float(market_context.get("vix", 15))
        trend = market_context.get("trend", "sideways")

        # Calculate individual factor scores
        win_rate_score = self._score_strategy_win_rate(strategy)
        regime_score = self._score_regime_alignment(strategy, direction, regime)
        volume_score = self._score_volume_confirmation(signal)
        vix_score = self._score_vix_environment(strategy, vix)
        trend_score = self._score_trend_alignment(strategy, direction, trend)

        # Weighted combination
        total = (
            win_rate_score * self.weights.get("strategy_win_rate", 0.25)
            + regime_score * self.weights.get("regime_alignment", 0.20)
            + volume_score * self.weights.get("volume_confirmation", 0.20)
            + vix_score * self.weights.get("vix_environment", 0.15)
            + trend_score * self.weights.get("trend_alignment", 0.20)
        )

        # Apply signal's own confidence as a multiplier if available
        confidence = signal.get("confidence", 0.7)
        if confidence > 0:
            total *= (0.5 + 0.5 * min(confidence, 1.0))

        return round(min(max(total, 0.0), 1.0), 3)

    def _score_strategy_win_rate(self, strategy: str) -> float:
        """Score based on strategy's historical win rate."""
        perf = self.strategy_performance.get(strategy)
        if perf is None:
            # Use default assumed win rate
            default_wr = _DEFAULT_WIN_RATES.get(strategy, 0.50)
            # Map 40-65% win rate to 0-1 score
            return self._normalize_wr(default_wr)

        total_trades = perf.get("total_trades", 0)
        if total_trades < 5:
            default_wr = _DEFAULT_WIN_RATES.get(strategy, 0.50)
            return self._normalize_wr(default_wr)

        win_rate = perf.get("win_rate", 50.0) / 100.0  # Convert from %
        return self._normalize_wr(win_rate)

    @staticmethod
    def _normalize_wr(win_rate: float) -> float:
        """Map win rate (0-1) to score (0-1). Best around 55-60%."""
        if win_rate < 0.35:
            return 0.1
        elif win_rate < 0.45:
            return 0.3 + (win_rate - 0.35) / 0.10 * 0.3
        elif win_rate <= 0.65:
            return 0.6 + (win_rate - 0.45) / 0.20 * 0.4
        elif win_rate <= 0.75:
            return 1.0 - (win_rate - 0.65) / 0.10 * 0.1
        else:
            return 0.85  # Suspiciously high, might be overfit

    @staticmethod
    def _score_regime_alignment(strategy: str, direction: str, regime: str) -> float:
        """Score based on how well the strategy fits the current regime."""
        # Best strategies for each regime
        regime_map = {
            "Bull": {
                "long_strategies": [
                    "Breakout", "Momentum", "ORB", "Supertrend", "GapFill",
                    "SectorRotation", "AdaptiveSupertrend", "ORBVolume",
                    "PTC", "PullbackTrendContinuation",
                    "MB", "MomentumBreakout",
                    "SIC", "SectorIntradayContinuity",
                    "VC", "VolatilityContraction",
                    "TRS", "TrendReversalScalp",
                ],
                "short_strategies": ["MRF", "MeanReversionFade"],
            },
            "Bear": {
                "long_strategies": ["MRF", "MeanReversionFade"],
                "short_strategies": [
                    "Breakout", "Momentum", "ORB", "Supertrend",
                    "RSIDivergence", "TrendExhaustion", "AdaptiveSupertrend",
                    "PTC", "PullbackTrendContinuation",
                    "MB", "MomentumBreakout",
                    "SIC", "SectorIntradayContinuity",
                    "VC", "VolatilityContraction",
                    "TRS", "TrendReversalScalp",
                ],
            },
            "Sideways": {
                "long_strategies": [
                    "MeanReversion", "VWAPReversion", "RSIDivergence", "ORB",
                    "AdaptiveSupertrend", "MultiTimeframe",
                    "MRF", "MeanReversionFade",
                    "VC", "VolatilityContraction",
                    "TRS", "TrendReversalScalp",
                ],
                "short_strategies": [
                    "MeanReversion", "VWAPReversion", "RSIDivergence",
                    "MRF", "MeanReversionFade",
                    "VC", "VolatilityContraction",
                    "TRS", "TrendReversalScalp",
                ],
            },
            "Volatile": {
                "long_strategies": [
                    "ORB", "GapFill", "AdaptiveSupertrend", "ORBVolume",
                    "MB", "MomentumBreakout",
                    "TRS", "TrendReversalScalp",
                    "MRF", "MeanReversionFade",
                ],
                "short_strategies": [
                    "ORB", "GapFill", "AdaptiveSupertrend", "ORBVolume",
                    "MB", "MomentumBreakout",
                    "TRS", "TrendReversalScalp",
                    "MRF", "MeanReversionFade",
                ],
            },
        }

        regime_info = regime_map.get(regime, regime_map["Sideways"])
        if direction.upper() == "LONG":
            aligned = strategy in regime_info["long_strategies"]
        else:
            aligned = strategy in regime_info["short_strategies"]

        if aligned:
            return 1.0
        else:
            # Check if it's at least not in the 'paused' category
            if direction.upper() == "LONG":
                paused = strategy in regime_info.get("short_strategies", [])
            else:
                paused = strategy in regime_info.get("long_strategies", [])
            if paused:
                return 0.15
            return 0.4

    @staticmethod
    def _score_volume_confirmation(signal: Dict[str, Any]) -> float:
        """Score based on volume confirmation of the signal."""
        volume_ratio = signal.get("volume_ratio")
        if volume_ratio is None:
            # No volume data, neutral score
            return 0.5

        if volume_ratio >= 2.5:
            return 1.0
        elif volume_ratio >= 1.8:
            normalized = (volume_ratio - 1.8) / (2.5 - 1.8)
            return 0.7 + 0.3 * min(normalized, 1.0)
        elif volume_ratio >= 1.3:
            normalized = (volume_ratio - 1.3) / (1.8 - 1.3)
            return 0.4 + 0.3 * min(normalized, 1.0)
        elif volume_ratio >= 1.0:
            normalized = (volume_ratio - 1.0) / (1.3 - 1.0)
            return 0.2 + 0.2 * min(normalized, 1.0)
        else:
            # Below average volume - weak confirmation
            return max(0.05, volume_ratio * 0.2)

    @staticmethod
    def _score_vix_environment(strategy: str, vix: float) -> float:
        """Score based on whether VIX level suits the strategy."""
        # Low VIX (<14): good for trend strategies, bad for volatility strategies
        # Normal VIX (14-18): good for most strategies
        # High VIX (18-22): increased risk, cautious
        # Very High VIX (>22): high risk, only volatile-specific strategies

        volatility_strategies = ["ORB", "GapFill", "ORBVolume"]
        trend_strategies = ["Momentum", "Breakout", "Supertrend", "SectorRotation",
                            "AdaptiveSupertrend"]
        reversion_strategies = ["MeanReversion", "VWAPReversion", "RSIDivergence",
                               "MultiTimeframe"]

        if vix <= 14:
            if strategy in trend_strategies:
                return 0.9
            elif strategy in reversion_strategies:
                return 0.7
            elif strategy in volatility_strategies:
                return 0.4
            else:
                return 0.6

        elif vix <= 18:
            return 0.8  # Normal environment, most strategies work

        elif vix <= 22:
            if strategy in volatility_strategies:
                return 0.85
            elif strategy in reversion_strategies:
                return 0.4
            else:
                return 0.5

        else:  # VIX > 22
            if strategy in volatility_strategies:
                return 0.7
            else:
                return 0.2

    @staticmethod
    def _score_trend_alignment(strategy: str, direction: str, trend: str) -> float:
        """Score based on signal direction matching market trend."""
        if trend == "up":
            if direction.upper() == "LONG":
                return 0.9
            else:
                return 0.2
        elif trend == "down":
            if direction.upper() == "SHORT":
                return 0.9
            else:
                return 0.2
        else:  # sideways
            return 0.5
