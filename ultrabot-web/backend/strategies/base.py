from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
import pandas as pd


class BaseStrategy(ABC):
    name: str = "base"
    description: str = ""
    preferred_timeframes: List[str] = ["5m", "15m", "5min", "15min"]
    best_regimes: List[str] = ["Bull", "Bear"]
    worst_regimes: List[str] = ["Sideways"]
    params: Dict[str, Any] = {}
    enabled: bool = True

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = dict(self.__class__.params) if hasattr(self.__class__, "params") and self.__class__.params else {}
        if params:
            self.params.update(params)

    @abstractmethod
    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        """Return signal dict or None.

        Signal dict has:
        {
            symbol: str,
            direction: "BUY" | "SELL",
            entry_price: float,
            sl_price: float,
            target_price: float,
            confidence: float (0-1),
            strategy: str (self.name),
            risk_reward: float,
            extra_details: dict,
        }
        """

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def update_params(self, params: Dict[str, Any]):
        self.params.update(params)

    def is_suitable_for_regime(self, regime: str) -> bool:
        return regime in self.best_regimes
