import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.dependencies import get_current_user, get_engine, get_repository
from core.engine import UltraBotEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/engine", tags=["engine"])


class EngineStartRequest(BaseModel):
    mode: str = Field("paper", pattern=r"^(paper|live)$")
    broker: str = Field("paper", pattern=r"^(paper|yahoofinance|angel_one|angelone|shoonya|dhan|fyers|zerodha|upstox)$")
    strategies: Optional[List[str]] = None
    initial_capital: Optional[float] = None


# Maps broker constructor kwargs from the stored credential dict (what the
# Settings UI saves, keys matching api/routes/brokers.py).
_BROKER_KWARG_MAP: Dict[str, Dict[str, str]] = {
    "angel_one": {
        "api_key": "api_key",
        "client_code": "client_id",
        "pin": "pin",
        "totp_secret": "totp_secret",
        "jwt_token": "jwt_token",
        "refresh_token": "refresh_token",
        "feed_token": "feed_token",
    },
    "shoonya": {
        "user_id": "user_id",
        "password": "password",
        "vendor_code": "vendor_code",
        "app_key": "app_key",
        "totp_secret": "totp_secret",
        "factor2_pin": "factor2_pin",
    },
    "dhan": {
        "client_id": "client_id",
        "access_token": "access_token",
        "pin": "pin",
        "totp_secret": "totp_secret",
    },
    "fyers": {
        "app_id": "app_id",
        "access_token": "access_token",
        "secret_key": "secret_key",
        "redirect_uri": "redirect_uri",
        "pin": "pin",
    },
    "zerodha": {
        "api_key": "api_key",
        "api_secret": "api_secret",
        "access_token": "access_token",
        "user_id": "user_id",
    },
}


async def _load_broker_config(broker_name: str, repo) -> Dict[str, Any]:
    """Decrypt the broker's stored credentials (Settings UI → DB) and map
    them to broker constructor kwargs. Returns {} for paper / unknown /
    missing credentials so the engine falls back to config-file values."""
    kwarg_map = _BROKER_KWARG_MAP.get((broker_name or "").lower())
    if not kwarg_map:
        return {}
    try:
        cred_record = await repo.get_broker_credentials(broker_name)
        if cred_record is None or not cred_record.encrypted_credentials:
            return {}
        from utils.encryption import decrypt_credentials
        cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        return {
            ctor_kwarg: cred_data.get(cred_key, "")
            for ctor_kwarg, cred_key in kwarg_map.items()
            if cred_data.get(cred_key)
        }
    except Exception as exc:
        logger.warning(
            "Could not load stored credentials for %s (%s); falling back to config file",
            broker_name, exc,
        )
        return {}


@router.post("/start")
async def start_engine(
    body: EngineStartRequest,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
    repo=Depends(get_repository),
) -> Dict[str, Any]:
    """Start the trading engine with the given mode, broker, and strategies.

    Live brokers use the encrypted credentials saved via Settings (DB),
    NOT the (usually empty) config-file `brokers:` section."""
    try:
        broker_config = await _load_broker_config(body.broker, repo)
        result = await engine.start(
            mode=body.mode,
            broker_name=body.broker,
            strategy_names=body.strategies,
            initial_capital=body.initial_capital,
            broker_config=broker_config,
        )
        return result
    except Exception as exc:
        logger.error("Engine start failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Engine start failed: {str(exc)}",
        )


@router.post("/stop")
async def stop_engine(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Gracefully stop the trading engine."""
    try:
        result = await engine.stop()
        return result
    except Exception as exc:
        logger.error("Engine stop failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Engine stop failed: {str(exc)}",
        )


@router.post("/pause")
async def pause_engine(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Pause the engine scanning loop. Position management continues."""
    try:
        result = await engine.pause()
        if result.get("status") == "not_running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Engine is not running (current state: {result.get('state', 'unknown')})",
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Engine pause failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Engine pause failed: {str(exc)}",
        )


@router.post("/resume")
async def resume_engine(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Resume the engine scanning loop."""
    try:
        result = await engine.resume()
        if result.get("status") == "not_paused":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Engine is not paused (current state: {result.get('state', 'unknown')})",
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Engine resume failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Engine resume failed: {str(exc)}",
        )


@router.get("/status")
async def engine_status(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Get full engine status."""
    try:
        status_data = await engine.get_status()
        return status_data
    except Exception as exc:
        logger.error("Engine status failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get engine status: {str(exc)}",
        )


@router.get("/scan-telemetry")
async def engine_scan_telemetry(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Get real-time scan and strategy rejection telemetry."""
    try:
        telemetry = engine.get_scan_telemetry()
        return telemetry
    except Exception as exc:
        logger.error("Engine scan telemetry failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get scan telemetry: {str(exc)}",
        )
