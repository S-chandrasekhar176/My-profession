#!/usr/bin/env python3
"""Client-side authenticated API client for internal monitoring scripts.

Fixes the 401-Unauthorized defect class on internal monitoring scripts
(persist/backlog_2026-09-22.md): /api/engine/status is JWT-protected BY
DESIGN — the fix lives HERE (scripts), never in backend auth.

Eliminates the four defects of the reference watcher script:
  1. Credentials hardcoded in source        -> env UB_USER/UB_PASS or a
     chmod-600 secret file. Secrets are NEVER logged.
  2. Token cached with no TTL tracking      -> state file stores
     {access_token, issued_at, ttl_hours}; token age is checked per call.
  3. A full protected GET burned per cycle  -> the cached TTL decides; zero
     token-validation calls. One poll = one protected call (or zero when
     only /api/health liveness is needed — /api/health is unauthenticated
     by design and requires no token at all).
  4. Dead `if status == 401` branch         -> urllib raises HTTPError on
     401; handling lives in the exception path. On 401: re-login ONCE ->
     retry ONCE -> fail loudly (non-zero exit code). No unbounded loops.

There is NO web-JWT refresh endpoint on the backend (auth routes are
login/logout/me only — verified on tip f990fc1). "Refresh" = re-login.

State/secret file permissions: chmod 600 where the OS honors it (POSIX);
on Windows mode bits are best-effort — the file lives in the user's
private state dir.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration (client-side only)
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("UB_BASE_URL", "http://127.0.0.1:8000")
STATE_DIR = os.environ.get("UB_STATE_DIR", os.path.join(os.path.expanduser("~"), ".ub_monitor"))
TOKEN_STATE_FILE = os.path.join(STATE_DIR, "token_state.json")
CREDENTIALS_FILE = os.environ.get("UB_CREDENTIALS_FILE", os.path.join(STATE_DIR, "credentials.json"))

LOGIN_PATH = "/api/auth/login"
HEALTH_PATH = "/api/health"  # unauthenticated watchdog liveness endpoint

PROACTIVE_RELOGIN_FRACTION = 0.8  # re-login at 80% of TTL (requirement #4)
HTTP_TIMEOUT_SECONDS = 10

# Local opener that bypasses any ambient proxy for localhost calls
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_utc = timezone.utc


def _log(message: str) -> None:
    ts = datetime.now(_utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[{ts} UTC] {message}", flush=True)


def _restrict_permissions(path: str) -> None:
    """chmod 600 (owner read/write only). Best-effort on Windows."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _redact(text: str) -> str:
    """Defense-in-depth: strip anything JWT-shaped from log text."""
    return re.sub(r"eyJ[A-Za-z0-9_\-\.]{20,}", "<redacted-token>", text)


# ---------------------------------------------------------------------------
# Credentials (requirement #1): env vars or chmod-600 secret file. Never
# hardcoded, never logged.
# ---------------------------------------------------------------------------
def load_credentials() -> tuple:
    """Return (username, password) from env vars or the credentials file.

    Precedence: UB_USER/UB_PASS env vars > UB_CREDENTIALS_FILE JSON
    {"username": ..., "password": ...}.
    Raises RuntimeError (loud, actionable) when neither is available.
    """
    user = os.environ.get("UB_USER", "").strip()
    password = os.environ.get("UB_PASS", "")
    if user and password:
        return user, password

    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as fh:
            creds = json.load(fh)
        user = str(creds.get("username", "")).strip()
        password = str(creds.get("password", ""))
        if user and password:
            return user, password
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise RuntimeError(
            f"Credentials file {CREDENTIALS_FILE} is unreadable/invalid: {type(exc).__name__}"
        ) from exc

    raise RuntimeError(
        "No credentials configured. Set UB_USER and UB_PASS environment "
        f"variables, or create {CREDENTIALS_FILE} containing "
        '{"username": "...", "password": "..."} with chmod 600. '
        "Credentials are never hardcoded or logged."
    )


# ---------------------------------------------------------------------------
# Token state file (requirement #2): {access_token, issued_at, ttl_hours},
# chmod 600. issued_at in epoch seconds, ttl_hours in hours.
# ---------------------------------------------------------------------------
def _load_token_state():
    try:
        with open(TOKEN_STATE_FILE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        token = str(state.get("access_token", ""))
        issued_at = float(state.get("issued_at", 0.0))
        ttl_hours = float(state.get("ttl_hours", 0.0))
        if token and issued_at > 0 and ttl_hours > 0:
            return {"access_token": token, "issued_at": issued_at, "ttl_hours": ttl_hours}
    except FileNotFoundError:
        return None
    except Exception:
        return None  # corrupt file == forced-expiry case: caller re-logins
    return None


def _save_token_state(token: str, ttl_hours: float) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    state = {
        "access_token": token,
        "issued_at": time.time(),
        "ttl_hours": float(ttl_hours),
    }
    with open(TOKEN_STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    _restrict_permissions(TOKEN_STATE_FILE)


def invalidate_token_state() -> None:
    try:
        os.remove(TOKEN_STATE_FILE)
    except FileNotFoundError:
        pass
    except Exception as exc:
        _log(f"Warning: could not remove token state file: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Raw HTTP (urllib; proxies bypassed for localhost)
# ---------------------------------------------------------------------------
def http_json(
    method: str,
    path: str,
    token=None,
    body=None,
    form_encoded: bool = False,
):
    """Return (status, parsed_json). Raises HTTPError on >=400 (urllib
    semantics — the 401 path is handled by callers via the exception)."""
    url = BASE_URL.rstrip("/") + path
    if form_encoded:
        data = body.encode("utf-8") if isinstance(body, str) else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    else:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with _OPENER.open(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


def _is_http_error_with_code(exc: Exception, code: int) -> bool:
    """urllib raises HTTPError on 401 — detect via the exception type (the
    old scripts' `if resp.status == 401` branch was dead code for exactly
    this reason)."""
    return isinstance(exc, urllib.error.HTTPError) and exc.code == code


# ---------------------------------------------------------------------------
# Login / token lifecycle (requirements #2, #4, #5, #7)
# ---------------------------------------------------------------------------
def login() -> dict:
    """POST /api/auth/login (form-encoded, same fields/encoding as the
    frontend login). Returns {access_token, token_type, expires_in_hours}.
    Persists token + TTL to the chmod-600 state file.
    Raises on failure (caller decides whether that is fatal)."""
    username, password = load_credentials()
    form = (
        f"username={urllib.request.quote(username, safe='')}"
        f"&password={urllib.request.quote(password, safe='')}"
    )
    status, payload = http_json("POST", LOGIN_PATH, body=form, form_encoded=True)
    if status != 200 or not payload.get("access_token"):
        raise RuntimeError(f"Login failed (http {status})")
    _save_token_state(payload["access_token"], float(payload.get("expires_in_hours", 24)))
    _log("Login OK — token cached with TTL tracking")  # never log the token
    return payload


def get_token(proactive: bool = True) -> str:
    """Return a usable access token WITHOUT spending any protected call.

    - Cached token younger than 80% of TTL -> reuse (zero network calls).
    - Token age >= 80% of TTL -> proactive re-login BEFORE the next protected
      call (requirement #4: mid-poll expiry should never occur).
    - No state file / corrupt state file / expired token -> fresh login.
    Login failures propagate (fail loudly, requirement #5).
    """
    state = _load_token_state()
    if state:
        age_hours = max(0.0, time.time() - state["issued_at"]) / 3600.0
        if age_hours < PROACTIVE_RELOGIN_FRACTION * state["ttl_hours"]:
            return state["access_token"]
        _log(
            f"Token age {age_hours:.2f}h >= {int(PROACTIVE_RELOGIN_FRACTION * 100)}% of "
            f"{state['ttl_hours']:.0f}h TTL — proactive re-login"
        )
    else:
        _log("No usable cached token — logging in")
    login()
    return _load_token_state()["access_token"]


# ---------------------------------------------------------------------------
# Authenticated call with the bounded 401 recovery path (requirements #5, #7)
# ---------------------------------------------------------------------------
def authed_json(method: str, path: str, body=None):
    """One protected call, TTL-gated. Exactly one protected request per
    invocation in the happy path; on 401: re-login ONCE, retry ONCE; if the
    retry also fails, re-raise (fail loudly — scripts exit non-zero)."""
    token = get_token(proactive=True)
    try:
        return http_json(method, path, token=token, body=body)
    except Exception as exc:
        if not _is_http_error_with_code(exc, 401):
            raise  # non-auth failure: propagate to caller's error handling
        # 401 in the exception path: cache lied (clock skew / backend restart
        # with a new JWT secret) -> ONE re-login, ONE retry.
        _log("401 on protected call — re-login once, retry once")
        invalidate_token_state()
        login()
        token = _load_token_state()["access_token"]
        try:
            return http_json(method, path, token=token, body=body)
        except Exception as retry_exc:
            if _is_http_error_with_code(retry_exc, 401):
                _log("FATAL: 401 persists after single re-login+retry — check credentials")
            raise  # caller fails loudly (non-zero exit / alert); no loop


# ---------------------------------------------------------------------------
# Liveness (requirement #6): /api/health is unauthenticated BY DESIGN
# ---------------------------------------------------------------------------
def health_json():
    """GET /api/health — NO Authorization header, NO token touch.
    Use for loop-alive / broker-name / heartbeat liveness checks."""
    return http_json("GET", HEALTH_PATH)


# ---------------------------------------------------------------------------
# Object-Oriented Client Wrapper (UBAuthClient)
# ---------------------------------------------------------------------------
class UBAuthClient:
    """Object-oriented wrapper around module-level auth helpers.

    Provides .authed_json(), .get_token(), and .is_healthy() for scripts
    and monitoring harnesses.
    """

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url

    def authed_json(self, method: str, path: str, body=None):
        return authed_json(method, path, body=body)

    def get_token(self, proactive: bool = True) -> str:
        return get_token(proactive=proactive)

    def is_healthy(self) -> bool:
        try:
            status, payload = health_json()
            return status == 200 and payload.get("status") in ("healthy", "degraded")
        except Exception:
            return False

    def health(self) -> dict:
        status, payload = health_json()
        return payload



# ---------------------------------------------------------------------------
# Self-test (forced-expiry simulation, bounded-retry proof) — runs OFFLINE
# against mocked HTTP: no backend required, no real secrets used.
# ---------------------------------------------------------------------------
def _selftest() -> int:
    failures = []

    calls = []
    status_map = {}

    def fake_http(method, path, token=None, body=None, form_encoded=False):
        calls.append((method, path, bool(token)))
        key = "LOGIN" if form_encoded else (method, path)
        outcome = status_map.get(key, 200)
        if outcome >= 400:
            raise urllib.error.HTTPError(url=path, code=outcome, msg="x", hdrs=None, fp=None)
        return outcome, {"access_token": "eyJSelftestToken.value.sig", "expires_in_hours": 24}

    # Patch module-global http_json: all module functions (login/get_token/
    # authed_json/health_json) resolve it via module globals at call time.
    import ub_auth_client as mod

    mod.http_json = fake_http
    mod.load_credentials = lambda: ("selftest-user", "selftest-pass")  # no real creds

    def reset_state():
        if os.path.exists(TOKEN_STATE_FILE):
            os.remove(TOKEN_STATE_FILE)
        calls.clear()
        status_map.clear()

    # T1: fresh login persists TTL
    reset_state()
    mod.get_token()
    if not any(c[1] == LOGIN_PATH for c in calls):
        failures.append("T1-no-login")
    st = mod._load_token_state()
    if not st or st["ttl_hours"] != 24.0:
        failures.append("T1-ttl-not-persisted")

    # T2: young cached token => NO login call burned
    calls.clear()
    mod.get_token()
    if any(c[1] == LOGIN_PATH for c in calls):
        failures.append("T2-login-burned-per-call")

    # T3: token age >= 80% TTL => proactive re-login BEFORE use
    st = mod._load_token_state()
    st["issued_at"] = time.time() - 0.85 * st["ttl_hours"] * 3600.0
    with open(TOKEN_STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    calls.clear()
    mod.get_token()
    if not any(c[1] == LOGIN_PATH for c in calls):
        failures.append("T3-no-proactive-relogin-at-80pct")

    # T4: corrupt state file (forced expiry) => seamless recovery
    with open(TOKEN_STATE_FILE, "w", encoding="utf-8") as fh:
        fh.write("{corrupt json!!")
    calls.clear()
    mod.get_token()
    if not any(c[1] == LOGIN_PATH for c in calls):
        failures.append("T4-corrupt-state-no-recovery")

    # T5: 401 on FIRST protected attempt -> ONE re-login + ONE retry succeeds
    reset_state()
    mod.get_token()  # warm cache (1 login)
    calls.clear()
    remaining_fails = {("GET", "/api/engine/status"): 1}

    def fake_http_fail_once(method, path, token=None, body=None, form_encoded=False):
        calls.append((method, path, bool(token)))
        key = "LOGIN" if form_encoded else (method, path)
        if remaining_fails.get(key, 0) > 0:
            remaining_fails[key] -= 1
            raise urllib.error.HTTPError(url=path, code=401, msg="x", hdrs=None, fp=None)
        if form_encoded:
            return 200, {"access_token": "eyJSelftestToken.value.sig", "expires_in_hours": 24}
        return 200, {}

    mod.http_json = fake_http_fail_once
    status, _ = mod.authed_json("GET", "/api/engine/status")
    logins = [c for c in calls if c[1] == LOGIN_PATH]
    protected = [c for c in calls if c[1] == "/api/engine/status"]
    if not (status == 200 and len(logins) == 1 and len(protected) == 2):
        failures.append(f"T5-401-path-wrong(logins={len(logins)},protected={len(protected)},status={status})")

    # T6: persistent 401 on protected route (login OK) -> raises after exactly
    # 2 protected attempts (one relogin + one retry, NO unbounded loop)
    calls.clear()
    status_map.clear()

    def fake_http_always_401(method, path, token=None, body=None, form_encoded=False):
        calls.append((method, path, bool(token)))
        if form_encoded:
            return 200, {"access_token": "eyJSelftestToken.value.sig", "expires_in_hours": 24}
        raise urllib.error.HTTPError(url=path, code=401, msg="x", hdrs=None, fp=None)

    mod.http_json = fake_http_always_401
    try:
        mod.authed_json("GET", "/api/engine/status")
        failures.append("T6-persistent-401-did-not-raise")
    except Exception:
        pass
    protected = [c for c in calls if c[1] == "/api/engine/status"]
    if len(protected) != 2:
        failures.append(f"T6-unbounded-retry(protected={len(protected)})")

    # T6b: re-login itself 401s (dead credentials) -> raises, protected == 1
    calls.clear()

    def fake_http_login_401(method, path, token=None, body=None, form_encoded=False):
        calls.append((method, path, bool(token)))
        raise urllib.error.HTTPError(url=path, code=401, msg="x", hdrs=None, fp=None)

    mod.http_json = fake_http_login_401
    try:
        mod.authed_json("GET", "/api/engine/status")
        failures.append("T6b-dead-creds-did-not-raise")
    except Exception:
        pass
    protected = [c for c in calls if c[1] == "/api/engine/status"]
    if len(protected) != 1:
        failures.append(f"T6b-retry-after-dead-login(protected={len(protected)})")

    # T7: /api/health is called WITHOUT any token
    mod.http_json = fake_http
    calls.clear()
    mod.health_json()
    h = [c for c in calls if c[1] == HEALTH_PATH]
    if not h or h[0][2]:
        failures.append("T7-health-used-token")

    if failures:
        _log(f"SELFTEST FAILED: {failures}")
        return 1
    _log(
        "SELFTEST PASSED: ttl cache, 80% proactive re-login, corrupt-state recovery, "
        "bounded 401 retry (one relogin + one retry), tokenless health"
    )
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    _log("Usage: python ub_auth_client.py --selftest (or import as a module)")
    sys.exit(2)
