import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.dependencies import get_current_user, get_engine, get_repository
from db.repository import Repository
from core.engine import UltraBotEngine
from models.strategy_config import (
    StrategyPerformanceResponse,
    StrategyToggleRequest,
    StrategyConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


# Default strategy definitions when registry is not available
_DEFAULT_STRATEGIES = [
    {
        "name": "ORB",
        "display_name": "Opening Range Breakout",
        "description": "Opening Range Breakout with volatility-adaptive range, gap filter, and measured targets.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Bull", "Bear", "Sideways", "Volatile"],
        "worst_regimes": [],
        "tags": ["core", "breakout"],
    },
    {
        "name": "MB",
        "display_name": "Momentum Breakout",
        "description": "Momentum Breakout with Bollinger Band coiling, volume confirmation, and multi-timeframe trend alignment.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Bull", "Bear"],
        "worst_regimes": ["Volatile", "Sideways"],
        "tags": ["momentum", "breakout"],
    },
    {
        "name": "PTC",
        "display_name": "Pullback Trend Continuation",
        "description": "Pullback Trend Continuation entering on EMA pullbacks with reversal candle and RSI confirmation.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Bull", "Bear"],
        "worst_regimes": ["Sideways", "Volatile"],
        "tags": ["trend", "pullback"],
    },
    {
        "name": "VC",
        "display_name": "Volume Climax",
        "description": "Volume Climax strategy capturing institutional volume surges with OBV and VWAP alignment.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Bull", "Bear", "Sideways"],
        "worst_regimes": ["Volatile"],
        "tags": ["volume", "institutional"],
    },
    {
        "name": "SIC",
        "display_name": "Signal Ignition Candle",
        "description": "Signal Ignition Candle capturing explosive directional moves upon breakout of ignition candle.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Bull", "Bear", "Sideways", "Volatile"],
        "worst_regimes": [],
        "tags": ["momentum", "price_action"],
    },
    {
        "name": "MRF",
        "display_name": "Mean Reversion Force",
        "description": "Mean Reversion Force fading 2.0σ+ VWAP deviations back toward the mean.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Sideways", "Bull", "Bear"],
        "worst_regimes": ["Volatile"],
        "tags": ["mean_reversion", "vwap"],
    },
    {
        "name": "TRS",
        "display_name": "Trend Reversal System",
        "description": "Trend Reversal System with 3-of-4 multi-indicator divergence confirmation and halved sizing.",
        "is_enabled": True,
        "direction": "BOTH",
        "timeframe": "5min",
        "best_regimes": ["Bull", "Bear", "Volatile"],
        "worst_regimes": ["Sideways"],
        "tags": ["reversal", "divergence"],
    },
]



# In-memory strategy configs (mirrors registry state for API updates)
_strategy_configs: Dict[str, Dict[str, Any]] = {}
for _s in _DEFAULT_STRATEGIES:
    _strategy_configs[_s["name"]] = {
        "name": _s["name"],
        "display_name": _s["display_name"],
        "description": _s["description"],
        "is_enabled": _s["is_enabled"],
        "direction": _s["direction"],
        "timeframe": _s["timeframe"],
        "best_regimes": _s.get("best_regimes", []),
        "worst_regimes": _s.get("worst_regimes", []),
        "tags": _s.get("tags", []),
        "parameters": {},
    }


def _sync_from_registry(engine: UltraBotEngine) -> None:
    """Sync in-memory configs from the strategy registry if available."""
    try:
        from strategies.registry import StrategyRegistry
        # Access registry from engine if it exists
        if hasattr(engine, "_registry") and engine._registry is not None:
            for name, instance in engine._registry.get_all().items():
                if name not in _strategy_configs:
                    _strategy_configs[name] = {}
                _strategy_configs[name]["is_enabled"] = instance.enabled
                _strategy_configs[name]["parameters"] = dict(instance.params or {})
                _strategy_configs[name]["name"] = instance.name
                if hasattr(instance, "description"):
                    _strategy_configs[name]["description"] = instance.description
    except (ImportError, AttributeError):
        pass


@router.get("")
async def list_strategies(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
    repo: Repository = Depends(get_repository),
) -> List[Dict[str, Any]]:
    """List all strategies with their current status and performance.

    Performance is computed LIVE from the trades ledger (real closed trades
    only). Strategies without executed trades honestly report empty stats —
    nothing is seeded or synthesized. Shadow-mode strategies additionally
    report signal-level tracking stats (separate from trade win rates).
    """
    try:
        # Sync from registry if available
        _sync_from_registry(engine)

        # Real per-strategy stats from the trades ledger + shadow signal stats
        perf_map: Dict[str, Any] = {}
        shadow_map: Dict[str, Any] = {}
        try:
            for name in _strategy_configs:
                computed = await repo.compute_strategy_stats(name)
                if computed:
                    perf_map[name] = computed
            shadow_map = await repo.compute_shadow_signal_stats()
        except Exception:
            pass

        # Shadow strategies from config (engine falls back to the same list)
        try:
            shadow_set = set(engine.shadow_strategies) if engine and hasattr(engine, "shadow_strategies") else set()
        except Exception:
            shadow_set = set()

        # Determine which are active in engine
        active_set = set(engine.active_strategies) if engine else set()

        result = []
        for name, config in _strategy_configs.items():
            perf = perf_map.get(name, {})
            is_active = name in active_set

            result.append({
                **config,
                "is_active_in_engine": is_active,
                "is_shadow": name.upper() in shadow_set,
                "performance": perf,
                "shadow_performance": shadow_map.get(name),
            })

        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to list strategies: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list strategies: {str(exc)}",
        )


@router.get("/verdicts")
async def get_strategy_verdicts(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """P3 decision layer: evidence-based promote/retire verdicts for every
    shadow-tracked strategy, computed from the accumulated SHADOW signal
    ledger against fee-adjusted breakeven win-rates.

    Verdicts unlock at 100 resolved shadow signals — until then every
    strategy honestly reports KEEP_COLLECTING. This endpoint is the single
    input the eventual promote/retire decision needs.
    """
    from core.strategy_verdict import MIN_SAMPLE, evaluate_strategy_verdicts

    try:
        shadow_map = await repo.compute_shadow_signal_stats()
        live = list(engine.active_strategies) if engine else []
        verdicts = evaluate_strategy_verdicts(shadow_map, live_strategies=live)
        return {
            "verdicts": verdicts,
            "decision_rule": {
                "min_resolved_signals": MIN_SAMPLE,
                "note": (
                    "PROMOTE_CANDIDATE: win-rate ≥ fee-adjusted breakeven + 3pp "
                    "over ≥100 resolved shadow signals. RETIRE_CANDIDATE: below "
                    "breakeven − 3pp. Otherwise keep collecting."
                ),
            },
            "shadow_strategies_tracked": sorted(
                (engine.shadow_strategies if engine and hasattr(engine, "shadow_strategies") else set())
            ),
        }
    except Exception as exc:
        logger.error("Failed to compute strategy verdicts: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute verdicts: {str(exc)}",
        )


@router.get("/attribution")
async def get_regime_attribution(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Per-strategy × per-regime attribution computed from REAL closed trades.

    Empty list means no closed trades yet — no synthetic attribution exists.
    """
    try:
        rows = await repo.get_regime_attribution()
        return {
            "source": "trades_ledger",
            "rows": rows,
            "total_closed_trades": sum(r["total_trades"] for r in rows),
        }
    except Exception as exc:
        logger.error("Failed to compute regime attribution: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute regime attribution: {str(exc)}",
        )


@router.put("/{name}/toggle")
async def toggle_strategy(
    name: str,
    body: StrategyToggleRequest,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Enable or disable a strategy."""
    try:
        if name not in _strategy_configs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Strategy '{name}' not found",
            )

        _strategy_configs[name]["is_enabled"] = body.is_enabled

        # Also update registry if available
        try:
            from strategies.registry import StrategyRegistry
            if hasattr(engine, "_registry") and engine._registry is not None:
                instance = engine._registry.get(name)
                if instance is not None:
                    instance.set_enabled(body.is_enabled)
        except (ImportError, AttributeError):
            pass

        # Update engine's active strategies list
        if engine and body.is_enabled and name not in engine.active_strategies:
            engine.active_strategies.append(name)
        elif engine and not body.is_enabled and name in engine.active_strategies:
            engine.active_strategies.remove(name)

        return {
            "message": f"Strategy '{name}' {'enabled' if body.is_enabled else 'disabled'}",
            "name": name,
            "is_enabled": body.is_enabled,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to toggle strategy: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle strategy: {str(exc)}",
        )


@router.put("/{name}/params")
async def update_strategy_params(
    name: str,
    body: StrategyConfigUpdate,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Update strategy configuration parameters."""
    try:
        if name not in _strategy_configs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Strategy '{name}' not found",
            )

        config = _strategy_configs[name]
        update_data = body.model_dump(exclude_none=True)

        # Update in-memory config
        for key, value in update_data.items():
            if key in config or key == "parameters":
                config[key] = value

        # Update registry instance if available
        try:
            if hasattr(engine, "_registry") and engine._registry is not None:
                instance = engine._registry.get(name)
                if instance is not None:
                    if update_data.get("is_enabled") is not None:
                        instance.set_enabled(update_data["is_enabled"])
                    if update_data.get("parameters"):
                        instance.update_params(update_data["parameters"])
        except (AttributeError, Exception):
            pass

        return {
            "message": f"Strategy '{name}' parameters updated",
            "name": name,
            **config,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update strategy params: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update strategy params: {str(exc)}",
        )


@router.get("/{name}/performance")
async def get_strategy_performance(
    name: str,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Optional[Dict[str, Any]]:
    """Get performance statistics for a specific strategy.

    Computed LIVE from the trades ledger (real closed trades only). Returns
    ``total_trades: 0`` when the strategy has no executed trades — never
    seeded or synthetic numbers.
    """
    try:
        computed = await repo.compute_strategy_stats(name)
        if computed is None:
            return None
        return computed
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get strategy performance: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get strategy performance: {str(exc)}",
        )
