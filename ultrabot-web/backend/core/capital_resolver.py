"""
Capital Resolution Module for UltraBot Web.

Provides a single canonical resolver for starting/total capital across
engine, risk gates, position sizing, database, and API routes.
"""
from typing import Any, Dict, Optional


def resolve_total_capital(
    engine: Optional[Any] = None,
    context: Optional[Dict[str, Any]] = None,
    config: Optional[Any] = None,
    default_fallback: float = 500000.0,
) -> float:
    """Canonical single source of truth for total/configured capital resolution.

    Resolution Priority:
    1. Explicit 'total_capital' or 'capital' key in context dict (if present, preserving explicit 0/negative).
    2. Active engine instance running capital (engine.initial_capital if present, preserving explicit 0/negative).
    3. Configured settings 'capital.virtual_capital' (if present, preserving explicit 0/negative).
    4. Canonical default fallback: default_fallback (500000.0, matching defaults.yaml).
    """
    # 1. Check Context Dictionary
    if isinstance(context, dict):
        if "total_capital" in context and context["total_capital"] is not None:
            try:
                return float(context["total_capital"])
            except (ValueError, TypeError):
                pass
        if "capital" in context and context["capital"] is not None:
            try:
                return float(context["capital"])
            except (ValueError, TypeError):
                pass

    # 2. Check Passed Engine Instance
    if engine is not None and hasattr(engine, "initial_capital") and engine.initial_capital is not None:
        try:
            return float(engine.initial_capital)
        except (ValueError, TypeError):
            pass

    # 3. Check Configuration (passed config dict/object or global settings)
    try:
        if config is not None:
            cfg = config
            if isinstance(cfg, dict):
                if "virtual_capital" in cfg and cfg["virtual_capital"] is not None:
                    return float(cfg["virtual_capital"])
                if "capital" in cfg and isinstance(cfg["capital"], dict):
                    if "virtual_capital" in cfg["capital"] and cfg["capital"]["virtual_capital"] is not None:
                        return float(cfg["capital"]["virtual_capital"])
            cap_cfg = cfg.get_capital_config() if hasattr(cfg, "get_capital_config") else {}
            if isinstance(cap_cfg, dict) and "virtual_capital" in cap_cfg and cap_cfg["virtual_capital"] is not None:
                return float(cap_cfg["virtual_capital"])
        else:
            from config.settings import settings
            cap_cfg = settings.get_capital_config() if hasattr(settings, "get_capital_config") else {}
            if isinstance(cap_cfg, dict) and "virtual_capital" in cap_cfg and cap_cfg["virtual_capital"] is not None:
                return float(cap_cfg["virtual_capital"])
    except Exception:
        pass

    # 4. Canonical Default
    return float(default_fallback)
