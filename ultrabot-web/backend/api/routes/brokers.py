import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from api.dependencies import get_current_user, get_repository
from db.repository import Repository
from utils.encryption import encrypt_credentials, decrypt_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

# Where to send the browser after the OAuth callback completes. Configurable
# via env since the frontend may run on a different host/port in some setups.
_FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://127.0.0.1:3000")


class BrokerCredentialInput(BaseModel):
    """Generic broker credential input."""
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    # Angel One specific
    api_key: Optional[str] = None
    pin: Optional[str] = None
    # Shoonya specific
    user_id: Optional[str] = None
    password: Optional[str] = None
    vendor_code: Optional[str] = None
    app_key: Optional[str] = None
    totp_secret: Optional[str] = None
    # Dhan / Fyers specific
    access_token: Optional[str] = None
    app_id: Optional[str] = None
    secret_key: Optional[str] = None
    redirect_uri: Optional[str] = None
    # Optional account type
    account_type: Optional[str] = None


class ActiveBrokerRequest(BaseModel):
    broker: str = Field(..., pattern=r"^(paper|yahoofinance|angel_one|shoonya|dhan|fyers|zerodha|upstox)$")


@router.get("")
async def get_broker_status(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Get status of all configured brokers."""
    try:
        creds = await repo.get_all_broker_credentials()
        brokers_status = []
        for cred in creds:
            # `extra` is a JSON-text column — parse it defensively.
            extra: Dict[str, Any] = {}
            if cred.extra:
                if isinstance(cred.extra, dict):
                    extra = cred.extra
                else:
                    try:
                        parsed = json.loads(cred.extra)
                        extra = parsed if isinstance(parsed, dict) else {}
                    except (json.JSONDecodeError, TypeError):
                        extra = {}

            if cred.last_error:
                auth_status = "error"
            elif cred.last_connected_at:
                auth_status = "connected"
            else:
                auth_status = "never_connected"

            brokers_status.append({
                "broker": cred.broker_name,
                "is_active": cred.is_enabled,
                "account_type": extra.get("account_type", "paper"),
                "last_auth": cred.last_connected_at,
                "auth_status": auth_status,
                "last_error": cred.last_error,
                "has_credentials": bool(cred.encrypted_credentials),
            })
        return {"brokers": brokers_status}
    except Exception as exc:
        logger.error("Failed to get broker status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get broker status: {str(exc)}",
        )


@router.post("/angel-one/credentials")
async def save_angel_one_credentials(
    body: BrokerCredentialInput,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Save (encrypted) Angel One broker credentials."""
    try:
        cred_data = {
            "client_id": body.client_id,
            "client_secret": body.client_secret,
            "api_key": body.api_key,
            "pin": body.pin,
            "totp_secret": body.totp_secret,
        }
        encrypted = encrypt_credentials(cred_data)
        await repo.save_broker_credentials(
            broker_name="angel_one",
            encrypted_creds=encrypted,
            extra={"account_type": body.account_type},
        )
        logger.info("Angel One credentials saved/updated")
        return {"message": "Angel One credentials saved successfully"}
    except Exception as exc:
        logger.error("Failed to save Angel One credentials: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save credentials: {str(exc)}",
        )


@router.post("/shoonya/credentials")
async def save_shoonya_credentials(
    body: BrokerCredentialInput,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Save (encrypted) Shoonya broker credentials."""
    try:
        cred_data = {
            "user_id": body.user_id or body.client_id,
            "password": body.password or body.client_secret,
            "vendor_code": body.vendor_code,
            "app_key": body.app_key or body.api_key,
            "totp_secret": body.totp_secret,
        }
        encrypted = encrypt_credentials(cred_data)
        await repo.save_broker_credentials(
            broker_name="shoonya",
            encrypted_creds=encrypted,
            extra={"account_type": body.account_type},
        )
        logger.info("Shoonya credentials saved/updated")
        return {"message": "Shoonya credentials saved successfully"}
    except Exception as exc:
        logger.error("Failed to save Shoonya credentials: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save credentials: {str(exc)}",
        )


@router.post("/angel-one/test")
async def test_angel_one(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Test Angel One broker connection."""
    try:
        cred_record = await repo.get_broker_credentials("angel_one")
        if cred_record is None or not cred_record.encrypted_credentials:
            return {
                "broker": "angel_one",
                "connected": False,
                "message": "Angel One credentials not found in database. Please click 'Save Credentials' first.",
            }

        # Decrypt and attempt a test connection
        try:
            cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        except Exception as dec_exc:
            return {
                "broker": "angel_one",
                "connected": False,
                "message": f"Failed to decrypt credentials: {str(dec_exc)}. Please re-save your credentials.",
            }

        try:
            from brokers.angel_one import AngelOneBroker
            broker = AngelOneBroker(
                api_key=cred_data.get("api_key", ""),
                client_code=cred_data.get("client_id", ""),
                pin=cred_data.get("pin", ""),
                totp_secret=cred_data.get("totp_secret", ""),
            )
            auth_res = await broker.authenticate()
            if auth_res.get("success"):
                connected = True
                message = "Connection successful"
            else:
                connected = False
                message = auth_res.get("message", "Authentication failed")
            
            # Disconnect/close after test
            if hasattr(broker, "close"):
                await broker.close()
            elif hasattr(broker, "disconnect"):
                await broker.disconnect()
        except Exception as conn_exc:
            connected = False
            message = f"Connection failed: {str(conn_exc)}"
            logger.warning("Angel One test connection failed: %s", conn_exc)

        return {
            "broker": "angel_one",
            "connected": connected,
            "message": message,
        }
    except Exception as exc:
        logger.error("Angel One test error: %s", exc, exc_info=True)
        return {
            "broker": "angel_one",
            "connected": False,
            "message": f"Test failed: {str(exc)}",
        }


@router.post("/shoonya/test")
async def test_shoonya(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Test Shoonya broker connection."""
    try:
        cred_record = await repo.get_broker_credentials("shoonya")
        if cred_record is None or not cred_record.encrypted_credentials:
            return {
                "broker": "shoonya",
                "connected": False,
                "message": "Shoonya credentials not found in database. Please click 'Save Credentials' first.",
            }

        try:
            cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        except Exception as dec_exc:
            return {
                "broker": "shoonya",
                "connected": False,
                "message": f"Failed to decrypt credentials: {str(dec_exc)}. Please re-save your credentials.",
            }

        try:
            from brokers.shoonya import ShoonyaBroker
            broker = ShoonyaBroker(
                user_id=cred_data.get("user_id", ""),
                password=cred_data.get("password", ""),
                totp_secret=cred_data.get("totp_secret", ""),
                vendor_code=cred_data.get("vendor_code", ""),
                app_key=cred_data.get("app_key", ""),
                factor2_pin=cred_data.get("factor2_pin", cred_data.get("pin", "")),
            )
            await broker.authenticate()
            connected = True
            message = "Connection successful"
            if hasattr(broker, "disconnect"):
                await broker.disconnect()
        except Exception as conn_exc:
            connected = False
            message = f"Connection failed: {str(conn_exc)}"
            logger.warning("Shoonya test connection failed: %s", conn_exc)

        return {
            "broker": "shoonya",
            "connected": connected,
            "message": message,
        }
    except Exception as exc:
        logger.error("Shoonya test error: %s", exc, exc_info=True)
        return {
            "broker": "shoonya",
            "connected": False,
            "message": f"Test failed: {str(exc)}",
        }


@router.post("/dhan/credentials")
async def save_dhan_credentials(
    body: BrokerCredentialInput,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Save (encrypted) Dhan broker credentials.

    PIN + TOTP secret are optional but enable the fully automatic daily
    re-login (auth.dhan.co generateAccessToken).
    """
    try:
        cred_data = {
            "client_id": body.client_id or "",
            "access_token": body.access_token or body.client_secret or "",
            "pin": body.pin or "",
            "totp_secret": body.totp_secret or "",
        }
        encrypted = encrypt_credentials(cred_data)
        await repo.save_broker_credentials(
            broker_name="dhan",
            encrypted_creds=encrypted,
            extra={"account_type": body.account_type or "live"},
        )
        logger.info("Dhan credentials saved/updated")
        return {"message": "Dhan credentials saved successfully"}
    except Exception as exc:
        logger.error("Failed to save Dhan credentials: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save credentials: {str(exc)}",
        )


@router.post("/dhan/test")
async def test_dhan(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Test Dhan broker connection."""
    try:
        cred_record = await repo.get_broker_credentials("dhan")
        if cred_record is None or not cred_record.encrypted_credentials:
            return {
                "broker": "dhan",
                "connected": False,
                "message": "Dhan credentials not found in database. Please click 'Save Credentials' first.",
            }

        try:
            cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        except Exception as dec_exc:
            return {
                "broker": "dhan",
                "connected": False,
                "message": f"Failed to decrypt credentials: {str(dec_exc)}. Please re-save your credentials.",
            }

        try:
            from brokers.dhan import DhanBroker
            broker = DhanBroker(
                client_id=cred_data.get("client_id", ""),
                access_token=cred_data.get("access_token", ""),
                pin=cred_data.get("pin", ""),
                totp_secret=cred_data.get("totp_secret", ""),
            )
            auth_res = await broker.authenticate()
            connected = auth_res.get("success", False)
            message = auth_res.get("message", "Authentication check complete")
            if connected:
                # Store a 24h expiry estimate so Settings can show countdown.
                try:
                    import time as _time
                    extra = {}
                    if cred_record.extra:
                        try:
                            extra = json.loads(cred_record.extra) if isinstance(cred_record.extra, str) else (cred_record.extra or {})
                        except (json.JSONDecodeError, TypeError):
                            extra = {}
                    extra["token_expires_at"] = _time.time() + 24 * 3600
                    await repo.save_broker_credentials(
                        broker_name="dhan",
                        encrypted_creds=cred_record.encrypted_credentials,
                        extra=extra,
                        last_connected_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                        last_error=None,
                    )
                except Exception:
                    pass
        except Exception as conn_exc:
            connected = False
            message = f"Connection failed: {str(conn_exc)}"

        return {
            "broker": "dhan",
            "connected": connected,
            "message": message,
        }
    except Exception as exc:
        return {"broker": "dhan", "connected": False, "message": str(exc)}


@router.post("/fyers/credentials")
async def save_fyers_credentials(
    body: BrokerCredentialInput,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Save (encrypted) Fyers broker credentials."""
    try:
        cred_data = {
            "app_id": body.app_id or body.client_id or "",
            "access_token": body.access_token or body.client_secret or "",
            "secret_key": body.secret_key or "",
            "pin": body.pin or "",
            "redirect_uri": body.redirect_uri or "",
        }
        encrypted = encrypt_credentials(cred_data)
        await repo.save_broker_credentials(
            broker_name="fyers",
            encrypted_creds=encrypted,
            extra={"account_type": body.account_type or "live"},
        )
        logger.info("Fyers credentials saved/updated")
        return {"message": "Fyers credentials saved successfully"}
    except Exception as exc:
        logger.error("Failed to save Fyers credentials: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save credentials: {str(exc)}",
        )


@router.post("/fyers/test")
async def test_fyers(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Test Fyers broker connection."""
    try:
        cred_record = await repo.get_broker_credentials("fyers")
        if cred_record is None or not cred_record.encrypted_credentials:
            return {
                "broker": "fyers",
                "connected": False,
                "message": "Fyers credentials not found in database. Please click 'Save Credentials' first.",
            }

        try:
            cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        except Exception as dec_exc:
            return {
                "broker": "fyers",
                "connected": False,
                "message": f"Failed to decrypt credentials: {str(dec_exc)}. Please re-save your credentials.",
            }

        try:
            from brokers.fyers import FyersBroker
            broker = FyersBroker(
                app_id=cred_data.get("app_id", ""),
                access_token=cred_data.get("access_token", ""),
                secret_key=cred_data.get("secret_key", ""),
                pin=cred_data.get("pin", ""),
            )
            auth_res = await broker.authenticate()
            connected = auth_res.get("success", False)
            message = auth_res.get("message", "Authentication check complete")
        except Exception as conn_exc:
            connected = False
            message = f"Connection failed: {str(conn_exc)}"

        return {
            "broker": "fyers",
            "connected": connected,
            "message": message,
        }
    except Exception as exc:
        return {"broker": "fyers", "connected": False, "message": str(exc)}


@router.get("/fyers/authorize")
async def fyers_authorize(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Return the Fyers login URL. The frontend opens this so the user can
    complete login + 2FA — required daily, cannot be automated.
    """
    try:
        cred_record = await repo.get_broker_credentials("fyers")
        if cred_record is None or not cred_record.encrypted_credentials:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Save Fyers app_id, secret_key and redirect_uri first.",
            )
        cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        app_id = cred_data.get("app_id", "")
        redirect_uri = cred_data.get("redirect_uri", "")
        if not app_id or not redirect_uri:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Fyers app_id and redirect_uri must both be set before connecting.",
            )

        from brokers.fyers import FyersBroker
        auth_url = FyersBroker.build_auth_url(app_id=app_id, redirect_uri=redirect_uri)
        return {"auth_url": auth_url}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to build Fyers auth URL: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build Fyers auth URL: {str(exc)}",
        )


@router.get("/fyers/callback")
async def fyers_callback(
    auth_code: Optional[str] = Query(default=None),
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    repo: Repository = Depends(get_repository),
):
    """Fyers redirects the browser here after login. Exchanges the auth_code
    for an access_token and stores it, then redirects back to Settings.

    NOTE: this exact URL must be registered as the app's Redirect URI in the
    Fyers developer dashboard, matching what's saved via /fyers/credentials.
    """
    resolved_code = auth_code or code
    try:
        cred_record = await repo.get_broker_credentials("fyers")
        if cred_record is None or not cred_record.encrypted_credentials:
            return RedirectResponse(f"{_FRONTEND_URL}/settings?broker=fyers&auth=error&message=no_credentials")

        cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        app_id = cred_data.get("app_id", "")
        secret_key = cred_data.get("secret_key", "")
        redirect_uri = cred_data.get("redirect_uri", "")

        if not resolved_code:
            return RedirectResponse(f"{_FRONTEND_URL}/settings?broker=fyers&auth=error&message=missing_auth_code")

        from brokers.fyers import FyersBroker
        result = await FyersBroker.exchange_auth_code(
            app_id=app_id, secret_key=secret_key, redirect_uri=redirect_uri, auth_code=resolved_code
        )

        if not result.get("success"):
            logger.warning("Fyers token exchange failed: %s", result.get("message"))
            return RedirectResponse(f"{_FRONTEND_URL}/settings?broker=fyers&auth=error&message=exchange_failed")

        cred_data["access_token"] = result["access_token"]
        encrypted = encrypt_credentials(cred_data)
        # Fyers tokens are valid for the current trading day only — store a
        # best-effort expiry so Settings can show "re-auth needed" rather
        # than silently using a dead token. Always re-auth each morning
        # regardless of this estimate.
        expires_at = _next_ist_early_morning_epoch()
        await repo.save_broker_credentials(
            broker_name="fyers",
            encrypted_creds=encrypted,
            extra={"account_type": cred_data.get("account_type", "live"), "token_expires_at": expires_at},
        )
        logger.info("Fyers access token refreshed via OAuth callback")

        # P1: hot-apply the fresh token to a RUNNING engine's realtime feed
        # (data source) even when execution stays on paper — no restart.
        try:
            from brokers.relogin import apply_tokens_to_engine
            from api.dependencies import get_engine as _get_engine

            engine = _get_engine()
            apply_tokens_to_engine(engine, "fyers", {"kind": "fyers", "access_token": result["access_token"]})
        except Exception as apply_exc:
            logger.debug("Fyers feed hot-apply skipped: %s", apply_exc)

        return RedirectResponse(f"{_FRONTEND_URL}/settings?broker=fyers&auth=success")
    except Exception as exc:
        logger.error("Fyers OAuth callback error: %s", exc, exc_info=True)
        return RedirectResponse(f"{_FRONTEND_URL}/settings?broker=fyers&auth=error&message=server_error")


@router.get("/fyers/token-status")
async def fyers_token_status(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Token expiry info for the Settings 'Connected — expires in Xh' display."""
    cred_record = await repo.get_broker_credentials("fyers")
    if cred_record is None or not cred_record.encrypted_credentials:
        return {"connected": False, "needs_reauth": True, "seconds_until_expiry": 0}

    try:
        cred_data = decrypt_credentials(cred_record.encrypted_credentials)
    except Exception:
        return {"connected": False, "needs_reauth": True, "seconds_until_expiry": 0}

    has_token = bool(cred_data.get("access_token"))
    expires_at = None
    try:
        import json as _json
        extra = _json.loads(cred_record.extra) if isinstance(cred_record.extra, str) else (cred_record.extra or {})
        expires_at = extra.get("token_expires_at")
    except Exception:
        pass

    now = time.time()
    seconds_left = max(0, (expires_at - now)) if expires_at else 0
    needs_reauth = (not has_token) or seconds_left <= 0

    return {
        "connected": has_token and not needs_reauth,
        "needs_reauth": needs_reauth,
        "seconds_until_expiry": round(seconds_left),
    }


def _next_ist_early_morning_epoch(hour_ist: int = 6) -> float:
    """Best-effort estimate of when a Fyers token stops being valid: the
    next occurrence of `hour_ist`:00 IST. Fyers does not publicly document
    an exact per-token TTL, so this is a conservative estimate meant to
    prompt re-auth each morning, not a guarantee the token is valid until
    exactly this time.
    """
    import datetime as _dt

    ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
    now_ist = _dt.datetime.now(ist)
    target = now_ist.replace(hour=hour_ist, minute=0, second=0, microsecond=0)
    if target <= now_ist:
        target += _dt.timedelta(days=1)
    return target.timestamp()


@router.post("/zerodha/credentials")
@router.post("/kite/credentials")
async def save_zerodha_credentials(
    body: BrokerCredentialInput,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Save (encrypted) Zerodha Kite Connect credentials."""
    try:
        cred_data = {
            "api_key": body.api_key or body.client_id or "",
            "api_secret": body.client_secret or body.secret_key or "",
            "access_token": body.access_token or "",
            "user_id": body.user_id or body.client_id or "",
        }
        encrypted = encrypt_credentials(cred_data)
        await repo.save_broker_credentials(
            broker_name="zerodha",
            encrypted_creds=encrypted,
            extra={"account_type": body.account_type or "live"},
        )
        logger.info("Zerodha Kite credentials saved/updated")
        return {"message": "Zerodha Kite Connect credentials saved successfully"}
    except Exception as exc:
        logger.error("Failed to save Zerodha credentials: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save credentials: {str(exc)}",
        )


@router.post("/zerodha/test")
@router.post("/kite/test")
async def test_zerodha(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Test Zerodha Kite Connect connection."""
    try:
        cred_record = await repo.get_broker_credentials("zerodha")
        if cred_record is None or not cred_record.encrypted_credentials:
            return {
                "broker": "zerodha",
                "connected": False,
                "message": "Zerodha credentials not found in database. Please click 'Save Credentials' first.",
            }

        try:
            cred_data = decrypt_credentials(cred_record.encrypted_credentials)
        except Exception as dec_exc:
            return {
                "broker": "zerodha",
                "connected": False,
                "message": f"Failed to decrypt credentials: {str(dec_exc)}. Please re-save your credentials.",
            }

        try:
            from brokers.kite import KiteBroker
            broker = KiteBroker(
                api_key=cred_data.get("api_key", ""),
                api_secret=cred_data.get("api_secret", ""),
                access_token=cred_data.get("access_token", ""),
                user_id=cred_data.get("user_id", ""),
            )
            auth_res = await broker.authenticate()
            connected = auth_res.get("success", False)
            message = auth_res.get("message", "Authentication check complete")
            if hasattr(broker, "disconnect"):
                await broker.disconnect()
        except Exception as conn_exc:
            connected = False
            message = f"Connection failed: {str(conn_exc)}"

        return {
            "broker": "zerodha",
            "connected": connected,
            "message": message,
        }
    except Exception as exc:
        return {"broker": "zerodha", "connected": False, "message": str(exc)}


@router.put("/active")
async def set_active_broker(
    body: ActiveBrokerRequest,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Set the active broker for the next engine session."""
    try:
        broker_name = body.broker
        valid_brokers = ("paper", "angel_one", "shoonya", "dhan", "fyers", "zerodha", "upstox")
        if broker_name not in valid_brokers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid broker: {broker_name}. Must be one of {valid_brokers}.",
            )

        # Store in settings for next engine start
        from config.settings import settings
        engine_config = settings._raw_config.setdefault("engine", {})
        engine_config["default_broker"] = broker_name

        return {
            "message": f"Active broker set to '{broker_name}'",
            "active_broker": broker_name,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to set active broker: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set active broker: {str(exc)}",
        )


@router.delete("/{broker_name}/credentials")
async def delete_broker_credentials(
    broker_name: str,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Delete a broker's stored (encrypted) credentials from the database."""
    valid_brokers = ("angel_one", "shoonya", "dhan", "fyers", "zerodha", "upstox")
    if broker_name not in valid_brokers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid broker: {broker_name}. Must be one of {valid_brokers}.",
        )
    try:
        deleted = await repo.delete_broker_credentials(broker_name)
        if not deleted:
            return {
                "success": False,
                "message": f"No stored credentials found for '{broker_name}'",
                "broker": broker_name,
            }
        logger.info("Deleted stored credentials for broker '%s'", broker_name)
        return {
            "success": True,
            "message": f"Credentials for '{broker_name}' deleted from the backend",
            "broker": broker_name,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to delete broker credentials: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete credentials: {str(exc)}",
        )


# ════════════════════════════════════════════════════════════════
# Daily re-login / session token management
# ════════════════════════════════════════════════════════════════

# IMPORTANT: this route must be declared BEFORE the parameterised
# DELETE /{broker_name}/credentials route above would shadow it — FastAPI
# matches in declaration order, so we declare relogin/token-status here
# explicitly. (POST vs DELETE methods differ, so no conflict arises.)


@router.post("/{broker_name}/relogin")
async def broker_relogin(
    broker_name: str,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
    request: Optional[dict] = None,
) -> Dict[str, Any]:
    """One-click daily re-login for a broker.

    * angel_one / shoonya / dhan — automatic TOTP login using stored
      credentials; the fresh token is persisted encrypted and hot-applied
      to a running engine's broker instance (no engine restart needed).
    * fyers — returns the browser login URL (SEBI-mandated daily 2FA);
      completing login in the browser saves the token via the redirect.
    """
    from brokers.relogin import perform_relogin, apply_tokens_to_engine

    try:
        result = await perform_relogin(broker_name, repo)
    except Exception as exc:
        logger.error("Re-login failed for %s: %s", broker_name, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Re-login failed: {str(exc)}",
        )

    # Hot-apply to a running engine so live trading resumes immediately.
    if result.get("success") and result.get("tokens"):
        try:
            from api.dependencies import get_engine as _get_engine
            engine = _get_engine()
            applied = apply_tokens_to_engine(engine, broker_name, result["tokens"])
            result["applied_to_running_engine"] = applied
        except Exception as exc:
            logger.warning("Could not hot-apply token to engine: %s", exc)
            result["applied_to_running_engine"] = False

    # Never echo raw tokens back to the client.
    result.pop("tokens", None)
    return result


@router.get("/token-status")
async def broker_token_status(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Session-token status for every configured broker — powers the
    Settings session panel (valid/expired + countdown + re-login method)."""
    from brokers.relogin import get_token_status

    try:
        statuses = await get_token_status(repo)
        return {"brokers": statuses}
    except Exception as exc:
        logger.error("Failed to build token status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build token status: {str(exc)}",
        )


@router.get("/preflight")
async def broker_session_preflight(
    broker: Optional[str] = None,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Pre-flight daily-session check for a broker (single source of truth
    for the dashboard warning banner + scheduler's 08:45 alert).

    ``broker`` defaults to the RUNNING engine's active broker so the
    frontend can call /preflight without arguments and get the check for
    the broker that actually matters right now.
    """
    from brokers.relogin import preflight_session_check

    broker_name = broker
    if not broker_name:
        try:
            from api.dependencies import get_engine as _get_engine

            engine = _get_engine()
            broker_name = getattr(engine, "broker_name", None) or "paper"
        except Exception:
            broker_name = "paper"

    try:
        result = await preflight_session_check(broker_name, repo)
        return result
    except Exception as exc:
        logger.error("Pre-flight session check failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pre-flight check failed: {str(exc)}",
        )
