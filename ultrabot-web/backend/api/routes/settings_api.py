import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from api.dependencies import get_current_user
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Keys that contain secrets and should be excluded from GET responses
_SECRET_KEYS = {"secret", "password", "token", "key", "credentials", "encrypted"}


def _strip_secrets(data: Any, parent_key: str = "") -> Any:
    """Recursively remove secret values from a dict."""
    if isinstance(data, dict):
        return {
            k: _strip_secrets(v, k)
            for k, v in data.items()
            if not any(sk in k.lower() for sk in _SECRET_KEYS)
        }
    if isinstance(data, list):
        return [_strip_secrets(item, parent_key) for item in data]
    return data


def _redact_value(key: str, value: Any) -> Any:
    """Redact a value if its key looks like a secret."""
    if any(sk in key.lower() for sk in _SECRET_KEYS):
        if isinstance(value, str) and len(value) > 4:
            return f"{value[:2]}...{value[-2:]}"
        return "***REDACTED***"
    return value


def _strip_and_redact(data: Dict[str, Any]) -> Dict[str, Any]:
    """Strip secret keys and redact sensitive values."""
    result = {}
    for k, v in data.items():
        if any(sk in k.lower() for sk in _SECRET_KEYS):
            continue
        if isinstance(v, dict):
            result[k] = _strip_and_redact(v)
        else:
            result[k] = _redact_value(k, v)
    return result


@router.get("")
async def get_settings(
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get all settings, excluding secrets."""
    try:
        cleaned = _strip_and_redact(settings._raw_config)
        return {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "config": cleaned,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get settings: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get settings: {str(exc)}",
        )


class SettingsUpdate(BaseModel):
    """Flat or nested settings update. Supports dot-notation keys.

    Example:
        {"risk.max_daily_trades": 15, "engine.scan_interval_seconds": 60}
    """
    # Allow arbitrary keys
    model_config = ConfigDict(extra="allow")

    def get_updates(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True)


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Update settings. Supports dot-notation for nested keys."""
    try:
        updates = body.get_updates()
        if not updates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )

        updated_keys = []
        for key, value in updates.items():
            if isinstance(value, dict):
                # Nested dict: merge into existing section
                section = settings._raw_config.setdefault(key, {})
                section.update(value)
                updated_keys.append(key)
            else:
                # Support dot notation: "risk.max_daily_trades" -> {"risk": {"max_daily_trades": value}}
                parts = key.split(".")
                target = settings._raw_config
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
                updated_keys.append(key)

        # Persist to disk
        settings.save()

        return {
            "message": "Settings updated successfully",
            "updated_keys": updated_keys,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update settings: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update settings: {str(exc)}",
        )


@router.get("/capital")
async def get_capital_info(
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get capital configuration."""
    try:
        capital_config = settings.get_capital_config()
        return {
            "virtual_capital": capital_config.get("virtual_capital", 100000),
            "max_capital_per_trade_pct": capital_config.get("max_capital_per_trade_pct", 20.0),
            "max_total_capital_usage_pct": capital_config.get("max_total_capital_usage_pct", 80.0),
            "reserve_capital_pct": capital_config.get("reserve_capital_pct", 20.0),
            "currency": capital_config.get("currency", "INR"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get capital info: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get capital info: {str(exc)}",
        )


class _CapitalUpdate(BaseModel):
    virtual_capital: float = Field(..., gt=0)


@router.put("/capital")
async def update_capital(
    body: _CapitalUpdate,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Update virtual capital."""
    try:
        capital_config = settings._raw_config.setdefault("capital", {})
        old_capital = capital_config.get("virtual_capital", 100000)
        capital_config["virtual_capital"] = body.virtual_capital

        # Persist to disk
        settings.save()

        return {
            "message": "Capital updated successfully",
            "old_capital": old_capital,
            "new_capital": body.virtual_capital,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update capital: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update capital: {str(exc)}",
        )
