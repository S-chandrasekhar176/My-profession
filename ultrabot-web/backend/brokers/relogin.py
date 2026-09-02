"""One-click broker re-login orchestration.

All four supported live brokers expire their API sessions daily:

* Angel One — session till 12 midnight IST (SmartAPI docs)
* Shoonya   — Noren session killed in the early morning
* Dhan      — access token valid 24h from generation
* Fyers     — token valid for the trading day (until ~05:30 IST next morning)

This module implements the daily re-login flows described in each broker's
official API documentation:

* angel_one: TOTP loginByPassword (fully automatic when TOTP secret stored)
* shoonya:   Noren /Login with jData {pwd: sha256, factor2: TOTP} (automatic)
* dhan:      auth.dhan.co generateAccessToken with PIN+TOTP (automatic),
             falling back to /v2/RenewToken for web-generated tokens
* fyers:     SEBI-mandated browser 2FA — no silent path; we return the
             auth URL for the user to complete login (by design)

Successful re-logins are persisted (encrypted) back into the broker
credentials table together with a ``token_expires_at`` timestamp, and are
hot-applied to a running engine's live broker instance so an engine restart
is NOT required.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# Brokers whose daily re-login can be fully automated with stored TOTP creds.
_TOTP_BROKERS = {"angel_one", "shoonya", "dhan"}
# Brokers requiring the browser-based OAuth flow (SEBI 2FA).
_BROWSER_BROKERS = {"fyers"}
_ALL_RELOGIN_BROKERS = _TOTP_BROKERS | _BROWSER_BROKERS

# Fyers tokens are cut in the early morning; the exact minute varies, so we
# conservatively estimate expiry at 05:30 IST of the *next* calendar day.
def _next_ist_early_morning_epoch(now: Optional[datetime] = None) -> float:
    now_ist = now or datetime.now(_IST)
    target = now_ist.replace(hour=5, minute=30, second=0, microsecond=0)
    if target <= now_ist:
        target = target + timedelta(days=1)
    return target.timestamp()


def _seconds_until(epoch: Optional[float]) -> int:
    if not epoch:
        return 0
    return max(0, int(epoch - time.time()))


async def preflight_session_check(broker_name: str, repo) -> Dict[str, Any]:
    """Pre-flight broker session check — is the daily session usable?

    Called at pre-market init (08:45 IST) and at engine start so the user is
    warned BEFORE the market opens / engine trades, instead of discovering an
    expired token through rejected orders mid-session.

    Returns a dict:
        {
          "ok": bool,            # session usable for the whole trading day
          "level": "ok"|"warning"|"critical"|"skipped",
          "broker": str,
          "message": str,        # human-readable, action-oriented
          "token_state": str,    # valid|expired|unknown|not_applicable
          "seconds_until_expiry": int|None,
          "relogin_method": "totp"|"browser"|"none",
        }

    Rules:
      * paper / yahoo / unknown broker  -> ok, level "skipped" (no daily session)
      * credentials missing             -> warning (engine can't trade live)
      * token expired                   -> critical (orders WILL be rejected)
      * token unknown (never logged in) -> warning
      * token valid                     -> ok, with seconds_until_expiry
    """
    name = (broker_name or "paper").lower()

    if name not in _ALL_RELOGIN_BROKERS:
        return {
            "ok": True,
            "level": "skipped",
            "broker": name,
            "message": f"Broker '{name}' does not require a daily session token.",
            "token_state": "not_applicable",
            "seconds_until_expiry": None,
            "relogin_method": "none",
        }

    method = "totp" if name in _TOTP_BROKERS else "browser"

    # Distinguish "no credentials stored" from "cannot read storage":
    # get_token_status() swallows repo errors and returns [] for both, which
    # would mislabel a DB outage as "not configured". Probe directly first.
    try:
        creds = await repo.get_all_broker_credentials()
        cred_names = {getattr(c, "broker_name", "") or "" for c in creds}
        storage_ok = True
    except Exception as exc:  # storage failure must never crash the caller
        logger.error("Pre-flight session check failed to read credentials: %s", exc)
        cred_names = set()
        storage_ok = False

    if not storage_ok:
        return {
            "ok": False,
            "level": "warning",
            "broker": name,
            "message": f"Could not verify {name} session (storage error). Verify manually in Settings.",
            "token_state": "unknown",
            "seconds_until_expiry": None,
            "relogin_method": method,
        }

    if name not in cred_names:
        return {
            "ok": False,
            "level": "warning",
            "broker": name,
            "message": f"{name} credentials not configured — engine cannot route live orders.",
            "token_state": "unknown",
            "seconds_until_expiry": None,
            "relogin_method": method,
        }

    try:
        statuses = await get_token_status(repo)
    except Exception as exc:  # defensive: get_token_status normally degrades itself
        logger.error("Pre-flight session check failed to read token status: %s", exc)
        return {
            "ok": False,
            "level": "warning",
            "broker": name,
            "message": f"Could not verify {name} session (storage error: {exc}). Verify manually in Settings.",
            "token_state": "unknown",
            "seconds_until_expiry": None,
            "relogin_method": method,
        }

    status = next((s for s in statuses if s.get("broker") == name), None)

    if status is None or not status.get("has_credentials"):
        return {
            "ok": False,
            "level": "warning",
            "broker": name,
            "message": f"{name} credentials not configured — engine cannot route live orders.",
            "token_state": "unknown",
            "seconds_until_expiry": None,
            "relogin_method": method,
        }

    token_state = status.get("token_state", "unknown")
    secs = status.get("seconds_until_expiry")

    if token_state == "valid":
        return {
            "ok": True,
            "level": "ok",
            "broker": name,
            "message": f"{name} session valid (~{_seconds_until(secs) // 60} min remaining).",
            "token_state": "valid",
            "seconds_until_expiry": secs,
            "relogin_method": method,
        }

    if token_state == "expired":
        return {
            "ok": False,
            "level": "critical",
            "broker": name,
            "message": (
                f"{name} session EXPIRED — live orders will be rejected. "
                + ("Use one-click re-login in Settings." if method == "totp"
                   else "Complete the browser 2FA login from Settings before market open.")
            ),
            "token_state": "expired",
            "seconds_until_expiry": secs,
            "relogin_method": method,
        }

    # unknown: credentials exist but no token / no expiry recorded
    return {
        "ok": False,
        "level": "warning",
        "broker": name,
        "message": (
            f"{name} has credentials but no valid session today — log in before market open. "
            + ("Use one-click re-login in Settings." if method == "totp"
               else "Complete the browser 2FA login from Settings.")
        ),
        "token_state": "unknown",
        "seconds_until_expiry": secs,
        "relogin_method": method,
    }


async def perform_relogin(broker_name: str, repo) -> Dict[str, Any]:
    """Run the daily re-login flow for a broker using its stored credentials.

    Args:
        broker_name: one of angel_one / shoonya / dhan / fyers.
        repo: Repository with get_broker_credentials/save_broker_credentials.

    Returns a dict:
        success, message, broker, relogin_method,
        expires_at (epoch s) / seconds_until_expiry,
        auth_url (fyers only).
    """
    broker_name = (broker_name or "").lower().strip()
    if broker_name not in _ALL_RELOGIN_BROKERS:
        return {
            "success": False,
            "broker": broker_name,
            "message": (
                f"Broker '{broker_name}' does not need a daily re-login "
                "(supported: " + ", ".join(sorted(_ALL_RELOGIN_BROKERS)) + ")."
            ),
        }

    from utils.encryption import decrypt_credentials, encrypt_credentials

    cred_record = await repo.get_broker_credentials(broker_name)
    if cred_record is None or not cred_record.encrypted_credentials:
        return {
            "success": False,
            "broker": broker_name,
            "message": "No stored credentials — save them in Settings first.",
        }
    try:
        cred_data = decrypt_credentials(cred_record.encrypted_credentials)
    except Exception as exc:
        return {
            "success": False,
            "broker": broker_name,
            "message": f"Failed to decrypt stored credentials: {exc}",
        }

    # ── Fyers: browser 2FA — hand back the login URL ──────────────
    if broker_name == "fyers":
        try:
            from brokers.fyers import FyersBroker
            app_id = cred_data.get("app_id", "")
            redirect_uri = cred_data.get("redirect_uri", "")
            if not (app_id and redirect_uri):
                return {
                    "success": False,
                    "broker": "fyers",
                    "message": "Fyers App ID and Redirect URI must be saved first.",
                }
            auth_url = FyersBroker.build_auth_url(app_id=app_id, redirect_uri=redirect_uri)
            return {
                "success": False,
                "broker": "fyers",
                "relogin_method": "browser",
                "requires_browser": True,
                "auth_url": auth_url,
                "message": (
                    "Fyers requires browser login with 2FA every day (SEBI rules). "
                    "Open the login URL, complete 2FA, and the token is saved "
                    "automatically via the redirect."
                ),
            }
        except Exception as exc:
            return {"success": False, "broker": "fyers", "message": f"Failed to build Fyers login URL: {exc}"}

    # ── TOTP brokers: fully automatic re-login ────────────────────
    if broker_name == "angel_one":
        totp_secret = cred_data.get("totp_secret", "")
        if not totp_secret:
            return {
                "success": False,
                "broker": "angel_one",
                "message": "No TOTP secret stored. Add it in Settings to enable one-click re-login.",
            }
        try:
            from brokers.angel_one import AngelOneBroker, _next_midnight_ist_epoch

            broker = AngelOneBroker(
                api_key=cred_data.get("api_key", ""),
                client_code=cred_data.get("client_id", cred_data.get("client_code", "")),
                pin=cred_data.get("pin", ""),
                totp_secret=totp_secret,
            )
            try:
                result = await broker.authenticate()
            finally:
                await broker.close()
            if not result.get("success"):
                return {"success": False, "broker": "angel_one", "message": result.get("message", "Login failed")}

            cred_data["jwt_token"] = result.get("jwt_token", "")
            cred_data["feed_token"] = result.get("feed_token", "")
            cred_data["refresh_token"] = result.get("refresh_token", "")
            expires_at = result.get("expires_at") or _next_midnight_ist_epoch()
            tokens = {
                "kind": "angel_one",
                "jwt_token": cred_data["jwt_token"],
                "feed_token": cred_data["feed_token"],
                "refresh_token": cred_data["refresh_token"],
            }
        except Exception as exc:
            return {"success": False, "broker": "angel_one", "message": f"Angel One re-login error: {exc}"}

    elif broker_name == "shoonya":
        totp_secret = cred_data.get("totp_secret", "")
        if not (totp_secret or cred_data.get("factor2_pin")):
            return {
                "success": False,
                "broker": "shoonya",
                "message": "No TOTP secret stored. Add it in Settings to enable one-click re-login.",
            }
        try:
            from brokers.shoonya import ShoonyaBroker

            broker = ShoonyaBroker(
                user_id=cred_data.get("user_id", ""),
                password=cred_data.get("password", ""),
                vendor_code=cred_data.get("vendor_code", ""),
                app_key=cred_data.get("app_key", ""),
                totp_secret=totp_secret,
                factor2_pin=cred_data.get("factor2_pin", ""),
            )
            try:
                result = await broker.authenticate()
            finally:
                await broker.close()
            if not result.get("success"):
                return {"success": False, "broker": "shoonya", "message": result.get("message", "Login failed")}

            cred_data["susertoken"] = result.get("susertoken", "")
            expires_at = result.get("expires_at") or (time.time() + 18 * 3600)
            tokens = {
                "kind": "shoonya",
                "susertoken": cred_data["susertoken"],
                "actid": result.get("actid", ""),
            }
        except Exception as exc:
            return {"success": False, "broker": "shoonya", "message": f"Shoonya re-login error: {exc}"}

    elif broker_name == "dhan":
        totp_secret = cred_data.get("totp_secret", "")
        pin = cred_data.get("pin", "")
        try:
            from brokers.dhan import DhanBroker

            result: Dict[str, Any] = {"success": False}
            if totp_secret and pin:
                result = await DhanBroker.authenticate_with_totp(
                    client_id=cred_data.get("client_id", ""),
                    pin=pin,
                    totp_secret=totp_secret,
                )
            if not result.get("success"):
                # Fallback: renew an existing (web-generated) token for 24h.
                broker = DhanBroker(
                    client_id=cred_data.get("client_id", ""),
                    access_token=cred_data.get("access_token", ""),
                )
                try:
                    renew = await broker.renew_token()
                finally:
                    await broker.close()
                if renew.get("success"):
                    result = renew
                elif totp_secret and not pin:
                    return {
                        "success": False,
                        "broker": "dhan",
                        "message": "Dhan PIN is missing — add it in Settings for one-click re-login.",
                    }
            if not result.get("success"):
                return {
                    "success": False,
                    "broker": "dhan",
                    "message": result.get(
                        "message",
                        "Re-login failed. Store Dhan PIN + TOTP secret for automatic re-login.",
                    ),
                }

            cred_data["access_token"] = result.get("access_token", "")
            # expiryTime from the API is ISO-like local time; fall back to +24h.
            expires_at = None
            expiry_str = result.get("expiry_time", "")
            if expiry_str:
                try:
                    expires_at = datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    expires_at = None
            if not expires_at:
                expires_at = time.time() + 24 * 3600
            tokens = {"kind": "dhan", "access_token": cred_data["access_token"]}
        except Exception as exc:
            return {"success": False, "broker": "dhan", "message": f"Dhan re-login error: {exc}"}
    else:  # pragma: no cover — guarded above
        return {"success": False, "broker": broker_name, "message": "Unsupported broker"}

    # ── Persist the fresh tokens (encrypted) + expiry ─────────────
    try:
        encrypted = encrypt_credentials(cred_data)
        extra: Dict[str, Any] = {"account_type": cred_data.get("account_type", "live")}
        extra["token_expires_at"] = expires_at
        extra["last_relogin_at"] = time.time()
        from datetime import datetime as _dt
        await repo.save_broker_credentials(
            broker_name=broker_name,
            encrypted_creds=encrypted,
            extra=extra,
            last_connected_at=_dt.now(_IST).isoformat(),
            last_error=None,
        )
    except Exception as exc:
        logger.error("Failed to persist re-login tokens for %s: %s", broker_name, exc)
        return {
            "success": False,
            "broker": broker_name,
            "message": f"Login succeeded but persisting the new token failed: {exc}",
        }

    return {
        "success": True,
        "broker": broker_name,
        "relogin_method": "totp",
        "expires_at": expires_at,
        "seconds_until_expiry": _seconds_until(expires_at),
        "tokens": tokens,
        "message": "Re-login successful — new session token is active.",
    }


def apply_tokens_to_engine(engine: Any, broker_name: str, tokens: Dict[str, Any]) -> bool:
    """Hot-apply freshly obtained tokens to a RUNNING engine so trading and
    data keep flowing without an engine/backend restart.

    Two targets:
      1. The engine's live broker instance (execution) — matched by name.
      2. The engine's realtime candle FEED (P1) — a FyersCandleFeed is
         refreshed whenever a fresh Fyers token arrives, even while the
         engine executes on paper (data: Fyers · execution: paper is a
         first-class hybrid mode).

    Returns True if anything was updated.
    """
    try:
        if engine is None:
            return False
        applied = False

        broker = getattr(engine, "broker", None)
        if (
            broker is not None
            and (getattr(engine, "broker_name", "") or "").lower() == (broker_name or "").lower()
        ):
            kind = tokens.get("kind")
            if kind == "angel_one" and hasattr(broker, "apply_session"):
                broker.apply_session(
                    jwt_token=tokens.get("jwt_token", ""),
                    feed_token=tokens.get("feed_token", ""),
                    refresh_token=tokens.get("refresh_token", ""),
                )
                applied = True
            elif kind == "shoonya" and hasattr(broker, "apply_session_token"):
                broker.apply_session_token(
                    susertoken=tokens.get("susertoken", ""),
                    actid=tokens.get("actid", ""),
                )
                applied = True
            elif kind == "dhan" and hasattr(broker, "apply_session_token"):
                broker.apply_session_token(access_token=tokens.get("access_token", ""))
                applied = True
            if applied:
                logger.info("Hot-applied fresh %s session token to the running engine", broker_name)

        # P1: Fyers feed hot-apply — works regardless of the execution broker.
        if (broker_name or "").lower() == "fyers":
            feed = getattr(engine, "feed", None)
            primary = getattr(feed, "primary", None) if feed is not None else None
            if primary is not None and hasattr(primary, "apply_new_token"):
                primary.apply_new_token(str(tokens.get("access_token") or ""))
                applied = True
                logger.info("Hot-applied fresh Fyers token to the running engine's realtime feed")

        return applied
    except Exception as exc:
        logger.warning("Failed to hot-apply token to engine: %s", exc)
        return False


async def get_token_status(repo) -> List[Dict[str, Any]]:
    """Status of every configured broker's session token — powers the
    Settings "Session status" panel and the expiry countdowns.

    The four daily-session brokers (angel_one/shoonya/dhan/fyers) are always
    included even before credentials are saved, so their cards can show
    "save credentials to enable re-login" instead of spinning forever.
    """
    import json as _json

    try:
        creds = await repo.get_all_broker_credentials()
    except Exception as exc:
        logger.error("Failed to load broker credentials: %s", exc)
        creds = []

    statuses: List[Dict[str, Any]] = []
    seen: set = set()
    for cred in creds:
        name = (cred.broker_name or "").lower()
        seen.add(name)
        extra: Dict[str, Any] = {}
        if cred.extra:
            if isinstance(cred.extra, dict):
                extra = cred.extra
            else:
                try:
                    parsed = _json.loads(cred.extra)
                    extra = parsed if isinstance(parsed, dict) else {}
                except (ValueError, TypeError):
                    extra = {}

        expires_at = extra.get("token_expires_at")
        last_relogin = extra.get("last_relogin_at")
        now = time.time()
        if expires_at and float(expires_at) > now:
            token_state = "valid"
        elif expires_at:
            token_state = "expired"
        else:
            token_state = "unknown"

        statuses.append({
            "broker": name,
            "has_credentials": bool(cred.encrypted_credentials),
            "auth_status": (
                "error" if cred.last_error
                else ("connected" if cred.last_connected_at else "never_connected")
            ),
            "token_state": token_state,
            "token_expires_at": expires_at,
            "seconds_until_expiry": _seconds_until(expires_at) if expires_at else None,
            "last_relogin_at": last_relogin,
            "last_auth": cred.last_connected_at,
            "last_error": cred.last_error,
            "can_auto_relogin": name in _TOTP_BROKERS,
            "relogin_method": (
                "totp" if name in _TOTP_BROKERS
                else ("browser" if name in _BROWSER_BROKERS else "none")
            ),
        })

    # Daily-session brokers with no credential row yet — so their Settings
    # cards can render an honest "not configured" state.
    for name in sorted(_TOTP_BROKERS | _BROWSER_BROKERS):
        if name in seen:
            continue
        statuses.append({
            "broker": name,
            "has_credentials": False,
            "auth_status": "never_connected",
            "token_state": "unknown",
            "token_expires_at": None,
            "seconds_until_expiry": None,
            "last_relogin_at": None,
            "last_auth": None,
            "last_error": None,
            "can_auto_relogin": name in _TOTP_BROKERS,
            "relogin_method": (
                "totp" if name in _TOTP_BROKERS
                else ("browser" if name in _BROWSER_BROKERS else "none")
            ),
        })
    return statuses
