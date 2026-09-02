import importlib
import logging
from typing import Dict, List, Optional, Tuple, Any, Type

from .base import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Central registry for all trading strategies."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, auto_discover: bool = True):
        self._strategies: Dict[str, BaseStrategy] = {}
        if auto_discover:
            self.discover(config)

    @property
    def strategies(self) -> Dict[str, BaseStrategy]:
        return self._strategies

    def register(self, strategy_class_or_instance: Any, params: Dict[str, Any] = None) -> None:
        """Instantiate and register a strategy class or instance."""
        if isinstance(strategy_class_or_instance, type):
            instance = strategy_class_or_instance(params=params)
        else:
            instance = strategy_class_or_instance
        self._strategies[instance.name] = instance

    def get(self, name: str) -> Optional[BaseStrategy]:
        """Get a strategy instance by name."""
        return self._strategies.get(name)

    def get_all(self) -> Dict[str, BaseStrategy]:
        """Return all registered strategies."""
        return dict(self._strategies)

    def get_active_for_regime(
        self,
        regime: str,
        activation_map: Dict[str, Dict[str, List[str]]],
    ) -> List[Tuple[str, BaseStrategy, str]]:
        """Get strategies with their activation status for a given regime.

        Returns list of (name, instance, status) where status is:
          - "active": strategy is in the active list for this regime
          - "reduced_size": strategy is in the reduced_size list
          - "paused": strategy is not listed (implicitly paused)
        """
        results: List[Tuple[str, BaseStrategy, str]] = []
        regime_config = activation_map.get(regime, {})
        active_names = set(regime_config.get("active", []))
        reduced_names = set(regime_config.get("reduced_size", []))

        for name, instance in self._strategies.items():
            if not instance.enabled:
                status = "paused"
            elif name in active_names:
                status = "active"
            elif name in reduced_names:
                status = "reduced_size"
            else:
                status = "paused"
            results.append((name, instance, status))

        return results

    def discover(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Import and register all V2, core, and advanced strategies."""
        modules_to_discover = [
            # V2 Strategies
            ".v2.orb",
            ".v2.mb",
            ".v2.ptc",
            ".v2.vc",
            ".v2.sic",
            ".v2.mrf",
            ".v2.trs",
            # Core Strategies
            ".core.breakout",
            ".core.mean_reversion",
            ".core.momentum",
            ".core.orb",
            ".core.rsi_divergence",
            ".core.supertrend",
            ".core.vwap_reversion",
            # Advanced Strategies
            ".advanced.adaptive_supertrend",
            ".advanced.gap_fill",
            ".advanced.multi_timeframe",
            ".advanced.news_momentum",
            ".advanced.orb_volume",
            ".advanced.sector_rotation",
            ".advanced.trend_exhaustion",
        ]

        pkg = __package__ or "strategies"
        discovered_count = 0
        strategies_cfg = config.get("strategies", {}) if config else {}

        for module_path in modules_to_discover:
            try:
                mod = importlib.import_module(module_path, package=pkg)
                # Find strategy classes in the module
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseStrategy)
                        and attr is not BaseStrategy
                        and attr.__name__ != "BaseStrategy"
                    ):
                        if attr.name not in self._strategies:
                            strat_params = strategies_cfg.get(attr.name, {})
                            instance = attr(params=strat_params) if strat_params else attr
                            self.register(instance)
                            discovered_count += 1
            except Exception as e:
                logger.warning("Could not load strategy module '%s': %s", module_path, e)

        logger.info("Strategy registry discovered %d strategies (total: %d)", discovered_count, len(self._strategies))
