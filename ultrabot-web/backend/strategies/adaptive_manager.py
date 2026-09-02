from typing import Dict, List, Optional, Any

from .registry import StrategyRegistry
from .regime_detector import RegimeDetector


# Default activation map: defines which strategies are active / reduced / paused per regime.
# TRS scans in Bull/Bear/Volatile to build its SHADOW track record —
# `strategy_shadow_mode` (defaults to [TRS]) is what keeps it orderless.
DEFAULT_ACTIVATION_MAP: Dict[str, Dict[str, List[str]]] = {
    "Bull": {
        "active": [
            "ORB",
            "PTC",
            "VC",
            "SIC",
            "MB",
            "MRF",
            "TRS",
        ],
        "reduced_size": [],
        "paused": [],
    },
    "Bear": {
        "active": [
            "ORB",
            "PTC",
            "VC",
            "SIC",
            "MB",
            "MRF",
            "TRS",
        ],
        "reduced_size": [],
        "paused": [],
    },
    "Sideways": {
        "active": [
            "ORB",
            "MRF",
            "VC",
            "SIC",
        ],
        "reduced_size": [],
        "paused": [
            "PTC",
            "MB",
            "TRS",
        ],
    },
    "Volatile": {
        "active": [
            "ORB",
            "SIC",
            "TRS",
        ],
        "reduced_size": [],
        "paused": [
            "MB",
            "PTC",
            "VC",
            "MRF",
        ],
    },
}



class AdaptiveManager:
    """Manages strategy activation based on market regime."""

    def __init__(
        self,
        config: Dict[str, Any] = None,
        registry: Optional[StrategyRegistry] = None,
        regime_detector: Optional[RegimeDetector] = None,
    ):
        self.config = config or {}
        self.registry = registry or StrategyRegistry()
        self.regime_detector = regime_detector or RegimeDetector()
        self.current_regime: str = "Sideways"
        self.current_regime_confidence: float = 0.0
        self.activation_map: Dict[str, Dict[str, List[str]]] = self.config.get(
            "activation_map", DEFAULT_ACTIVATION_MAP
        )

    def update_regime(self, market_data: Dict[str, Any]) -> str:
        """Update the current regime from market data and return it.

        Expected market_data keys:
            nifty_price, nifty_day_change_pct, nifty_5day_change_pct,
            vix, ad_ratio, sector_data (optional)
        """
        result = self.regime_detector.classify(
            nifty_price=market_data.get("nifty_price", 0.0),
            nifty_day_change_pct=market_data.get("nifty_day_change_pct", 0.0),
            nifty_5day_change_pct=market_data.get("nifty_5day_change_pct", 0.0),
            vix=market_data.get("vix", 15.0),
            ad_ratio=market_data.get("ad_ratio", 1.0),
            adx=market_data.get("adx"),
            ema_trend=market_data.get("ema_trend"),
            sector_data=market_data.get("sector_data"),
        )
        self.current_regime = result["regime"]
        self.current_regime_confidence = result["confidence"]
        return self.current_regime

    def get_active_strategies(self) -> List[Any]:
        """Return list of active strategy instances for the current regime."""
        results = self.registry.get_active_for_regime(
            regime=self.current_regime,
            activation_map=self.activation_map,
        )
        active = [instance for _name, instance, status in results if status == "active"]
        return active

    def should_reduce_size(self, strategy_name: str) -> bool:
        """Check if a strategy should use reduced position size in current regime."""
        results = self.registry.get_active_for_regime(
            regime=self.current_regime,
            activation_map=self.activation_map,
        )
        for name, _instance, status in results:
            if name == strategy_name:
                return status == "reduced_size"
        return True  # Unknown strategy defaults to reduced
