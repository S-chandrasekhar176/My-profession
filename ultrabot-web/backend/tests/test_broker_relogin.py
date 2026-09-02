"""Tests for the daily re-login / broker session-token lifecycle.

Covers (per the official broker API docs):
* Shoonya Noren wire format — jData/jKey form encoding, TOTP login payload
  (sha256 password, sha256(uid|appkey)), session-expiry detection
* Dhan — auth.dhan.co generateAccessToken (PIN+TOTP), /v2/RenewToken
  fallback, /marketfeed/ltp response parsing
* Angel One — loginByPassword expiry (midnight IST), apply_session
* brokers/relogin.py orchestrator — per-broker dispatch, token persistence
  (encrypted + token_expires_at), engine hot-apply, token status builder
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from brokers.shoonya import ShoonyaBroker, _QUOTE_URL
from brokers.dhan import DhanBroker
from brokers.angel_one import AngelOneBroker, _next_midnight_ist_epoch
from brokers.relogin import (
    _next_ist_early_morning_epoch,
    apply_tokens_to_engine,
    get_token_status,
    perform_relogin,
)

_IST = timezone(timedelta(hours=5, minutes=30))
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _mock_http_client(post_handler):
    """httpx client stand-in with is_closed=False so brokers reuse it."""
    client = MagicMock()
    client.is_closed = False
    client.post = post_handler
    client.aclose = AsyncMock()
    return client


def _noren_ok(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", "https://api.shoonya.com/NorenWClientTP/Login"),
        json={"stat": "Ok", **payload},
    )


def _make_cred_record(broker_name: str, cred_data: dict, extra: dict | None = None):
    """A stand-in for the DB BrokerCredential row."""
    from utils.encryption import encrypt_credentials

    return SimpleNamespace(
        broker_name=broker_name,
        encrypted_credentials=encrypt_credentials(cred_data),
        extra=json.dumps(extra or {}),
        last_connected_at=None,
        last_error=None,
        is_enabled=True,
    )


def _make_repo(records: dict):
    """Async-mock Repository with in-memory credential rows."""
    saved: dict = {}

    async def get_broker_credentials(name):
        return records.get(name) or saved.get(name)

    async def save_broker_credentials(broker_name, encrypted_creds, **kwargs):
        base = saved.get(broker_name) or records.get(broker_name) or SimpleNamespace(
            broker_name=broker_name,
            encrypted_credentials="",
            extra="{}",
            last_connected_at=None,
            last_error=None,
        )
        saved[broker_name] = SimpleNamespace(
            broker_name=broker_name,
            encrypted_credentials=encrypted_creds,
            extra=kwargs.get("extra", base.extra if hasattr(base, "extra") else "{}"),
            last_connected_at=kwargs.get("last_connected_at"),
            last_error=kwargs.get("last_error"),
            is_enabled=True,
        )
        return saved[broker_name]

    repo = MagicMock()

    async def get_all():
        merged = {**records, **saved}
        return [await get_broker_credentials(k) for k in merged]

    repo.get_broker_credentials = AsyncMock(side_effect=get_broker_credentials)
    repo.save_broker_credentials = AsyncMock(side_effect=save_broker_credentials)
    repo.get_all_broker_credentials = AsyncMock(side_effect=get_all)
    repo.saved = saved
    return repo


# ─────────────────────────────────────────────
# Shoonya — Noren wire format
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shoonya_login_posts_jdata_totp_payload():
    """Login must POST jData JSON with sha256(pwd), TOTP factor2 and
    sha256(uid|appkey) — the Noren contract (NOT Bearer headers)."""
    import hashlib

    broker = ShoonyaBroker(
        user_id="FA12345",
        password="secret-pw",
        vendor_code="FA12345_U",
        app_key="my-api-key",
        totp_secret=TOTP_SECRET,
    )

    captured = {}

    async def fake_post(url, data=None, **kwargs):
        captured["url"] = str(url)
        captured["form"] = data
        return _noren_ok({"susertoken": "tok-abc", "actid": "FA12345"})

    broker._client = _mock_http_client(fake_post)

    result = await broker.authenticate()

    assert result["success"] is True
    assert "Login" in captured["url"]
    jdata = json.loads(captured["form"]["jData"])
    # jData fields per Noren /Login
    assert jdata["uid"] == "FA12345"
    assert jdata["pwd"] == hashlib.sha256(b"secret-pw").hexdigest()
    assert jdata["factor2"].isdigit() and len(jdata["factor2"]) == 6  # TOTP
    assert jdata["vc"] == "FA12345_U"
    assert jdata["appkey"] == hashlib.sha256(b"FA12345|my-api-key").hexdigest()
    assert jdata["apkversion"] == "1.0*"
    assert "imei" in jdata
    # Login itself must NOT send jKey (no session yet)
    assert "jKey" not in captured["form"]
    assert broker._session_token == "tok-abc"
    assert broker._authenticated is True
    await broker.close()


@pytest.mark.asyncio
async def test_shoonya_api_calls_carry_jkey_session_token():
    """All post-login calls must send jKey=<susertoken> (Noren session auth)."""
    broker = ShoonyaBroker(user_id="FA12345", password="pw", totp_secret=TOTP_SECRET)
    broker.apply_session_token("tok-xyz", actid="FA12345")

    captured = []

    async def fake_post(url, data=None, **kwargs):
        captured.append((str(url), data))
        return _noren_ok({"lp": "1911.60"})

    broker._client = _mock_http_client(fake_post)

    price = await broker.get_ltp("SUNPHARMA")
    assert price == pytest.approx(1911.60)
    url, form = captured[0]
    assert "GetQuotes" in url
    assert form["jKey"] == "tok-xyz"
    quote = json.loads(form["jData"])
    assert quote == {"uid": "FA12345", "exch": "NSE", "token": "3351"}
    await broker.close()


@pytest.mark.asyncio
async def test_shoonya_session_expired_raises_token_expired():
    broker = ShoonyaBroker(user_id="FA12345", password="pw", totp_secret=TOTP_SECRET)
    broker.apply_session_token("dead-token")

    async def fake_post(url, data=None, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"stat": "Not_Ok", "emsg": "Session Expired : Invalid Session Key"},
        )

    broker._client = _mock_http_client(fake_post)

    from errors.error_types import TokenExpiredError

    # _noren_post is the shared gateway — session errors must surface as
    # TokenExpiredError (so callers re-login instead of reusing dead tokens).
    with pytest.raises(TokenExpiredError):
        await broker._noren_post(_QUOTE_URL, {"uid": "FA12345"})
    assert broker._authenticated is False
    await broker.close()


@pytest.mark.asyncio
async def test_shoonya_margin_uses_documented_formula():
    """available = (cash+payin+payout+daycash+unclearedcash+brkcollamt+
    collateral+aux_brkcollamt) - marginused — per the Limits docs."""
    broker = ShoonyaBroker(user_id="FA12345", password="pw", totp_secret=TOTP_SECRET)
    broker.apply_session_token("tok")

    async def fake_post(url, data=None, **kwargs):
        return _noren_ok(
            {
                "cash": "100000",
                "payin": "5000",
                "payout": "0",
                "daycash": "0",
                "unclearedcash": "0",
                "brkcollamt": "0",
                "collateral": "5000",
                "aux_brkcollamt": "0",
                "marginused": "20000",
            }
        )

    broker._client = _mock_http_client(fake_post)

    margin = await broker.get_margin()
    assert margin["total"] == pytest.approx(110000.0)
    assert margin["used"] == pytest.approx(20000.0)
    assert margin["available"] == pytest.approx(90000.0)
    await broker.close()


# ─────────────────────────────────────────────
# Dhan — TOTP login + LTP parsing
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dhan_generate_access_token_totp_flow():
    """auth.dhan.co/app/generateAccessToken with dhanClientId/pin/totp query
    params; response accessToken + expiryTime parsed."""
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = str(url)
        captured["params"] = kwargs.get("params")

        def responder(request):
            return httpx.Response(
                200,
                request=request,
                json={
                    "dhanClientId": "1100000123",
                    "dhanClientName": "TEST USER",
                    "accessToken": "jwt-token-xyz",
                    "expiryTime": "2026-01-01T00:00:00.000",
                },
            )

        return httpx.AsyncClient()._transport.handle_request  # noqa: E501 (placeholder)

    # Simpler: monkeypatch httpx.AsyncClient with a stub
    class StubResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class StubClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, **kwargs):
            captured["url"] = str(url)
            captured["params"] = params
            return StubResponse(
                {
                    "dhanClientId": "1100000123",
                    "accessToken": "jwt-token-xyz",
                    "expiryTime": "2026-01-01T00:00:00.000",
                }
            )

    with patch.object(httpx, "AsyncClient", StubClient):
        result = await DhanBroker.authenticate_with_totp(
            client_id="1100000123", pin="123456", totp_secret=TOTP_SECRET
        )

    assert result["success"] is True
    assert result["access_token"] == "jwt-token-xyz"
    assert result["expiry_time"] == "2026-01-01T00:00:00.000"
    assert "generateAccessToken" in captured["url"]
    assert captured["params"]["dhanClientId"] == "1100000123"
    assert captured["params"]["pin"] == "123456"
    assert captured["params"]["totp"].isdigit() and len(captured["params"]["totp"]) == 6


@pytest.mark.asyncio
async def test_dhan_ltp_parses_nested_marketfeed_response():
    """/marketfeed/ltp returns data.NSE_EQ.<securityId>.last_price."""
    broker = DhanBroker(client_id="1100000123", access_token="jwt")
    broker._authenticated = True

    async def fake_post(path, json=None, headers=None):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.dhan.co/v2/marketfeed/ltp"),
            json={
                "data": {"NSE_EQ": {"11536": {"last_price": 4520.5}}},
                "status": "success",
            },
        )

    broker._client = _mock_http_client(fake_post)

    price = await broker.get_ltp("TCS")
    assert price == pytest.approx(4520.5)
    await broker.close()


@pytest.mark.asyncio
async def test_dhan_renew_token_flow():
    broker = DhanBroker(client_id="1100000123", access_token="old-jwt")

    async def fake_post(path, json=None, headers=None):
        assert "RenewToken" in str(path)
        assert headers["access-token"] == "old-jwt"
        assert headers["dhanClientId"] == "1100000123"
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.dhan.co/v2/RenewToken"),
            json={"accessToken": "new-jwt", "expiryTime": "2026-01-02T00:00:00.000"},
        )

    broker._client = _mock_http_client(fake_post)

    result = await broker.renew_token()
    assert result["success"] is True
    assert broker.access_token == "new-jwt"
    await broker.close()


# ─────────────────────────────────────────────
# Angel One — expiry semantics
# ─────────────────────────────────────────────


def test_angel_one_expiry_is_next_midnight_ist():
    now_ist = datetime.now(_IST)
    expiry = _next_midnight_ist_epoch()
    exp_ist = datetime.fromtimestamp(expiry, _IST)
    assert exp_ist.hour == 0 and exp_ist.minute == 0 and exp_ist.second == 0
    assert exp_ist > now_ist
    assert (exp_ist - now_ist) <= timedelta(days=1)


def test_angel_one_apply_session_hot_apply():
    broker = AngelOneBroker(api_key="k", client_code="A123", pin="1234")
    broker.apply_session(jwt_token="jwt-1", feed_token="feed-1", refresh_token="ref-1")
    assert broker.jwt_token == "jwt-1"
    assert broker.feed_token == "feed-1"
    assert broker.refresh_token == "ref-1"
    assert broker._authenticated is True
    assert broker.token_manager.get_token("angel_one") == "jwt-1"


def test_fyers_expiry_helper_next_0530_ist():
    now_ist = datetime.now(_IST)
    expiry = _next_ist_early_morning_epoch()
    exp_ist = datetime.fromtimestamp(expiry, _IST)
    assert exp_ist.hour == 5 and exp_ist.minute == 30
    assert exp_ist > now_ist


# ─────────────────────────────────────────────
# Relogin orchestrator
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_perform_relogin_angel_one_persists_tokens():
    repo = _make_repo(
        {
            "angel_one": _make_cred_record(
                "angel_one",
                {
                    "api_key": "AK",
                    "client_id": "A123",
                    "pin": "1234",
                    "totp_secret": TOTP_SECRET,
                    "account_type": "live",
                },
            )
        }
    )

    async def fake_auth(self):
        return {
            "success": True,
            "jwt_token": "jwt-new",
            "feed_token": "feed-new",
            "refresh_token": "ref-new",
            "expires_at": time.time() + 3600,
        }

    async def fake_close(self):
        return None

    with patch.object(AngelOneBroker, "authenticate", fake_auth), patch.object(
        AngelOneBroker, "close", fake_close
    ):
        result = await perform_relogin("angel_one", repo)

    assert result["success"] is True
    assert result["relogin_method"] == "totp"
    assert result["tokens"]["jwt_token"] == "jwt-new"
    assert result["seconds_until_expiry"] > 0

    # Persisted encrypted with expiry metadata
    saved = repo.saved["angel_one"]
    from utils.encryption import decrypt_credentials

    stored = decrypt_credentials(saved.encrypted_credentials)
    assert stored["jwt_token"] == "jwt-new"
    assert stored["totp_secret"] == TOTP_SECRET  # preserved
    extra = saved.extra if isinstance(saved.extra, dict) else json.loads(saved.extra)
    assert extra["token_expires_at"] == pytest.approx(time.time() + 3600, abs=5)
    assert extra["last_relogin_at"] > 0
    assert saved.last_error is None
    assert saved.last_connected_at is not None


@pytest.mark.asyncio
async def test_perform_relogin_shoonya_persists_susertoken():
    repo = _make_repo(
        {
            "shoonya": _make_cred_record(
                "shoonya",
                {
                    "user_id": "FA12345",
                    "password": "pw",
                    "vendor_code": "FA12345_U",
                    "app_key": "KEY",
                    "totp_secret": TOTP_SECRET,
                },
            )
        }
    )

    async def fake_auth(self):
        return {
            "success": True,
            "susertoken": "suser-tok",
            "actid": "FA12345",
            "expires_at": time.time() + 64800,
        }

    async def fake_close(self):
        return None

    with patch.object(ShoonyaBroker, "authenticate", fake_auth), patch.object(
        ShoonyaBroker, "close", fake_close
    ):
        result = await perform_relogin("shoonya", repo)

    assert result["success"] is True
    assert result["tokens"]["susertoken"] == "suser-tok"
    saved = repo.saved["shoonya"]
    from utils.encryption import decrypt_credentials

    stored = decrypt_credentials(saved.encrypted_credentials)
    assert stored["susertoken"] == "suser-tok"
    assert stored["password"] == "pw"


@pytest.mark.asyncio
async def test_perform_relogin_dhan_totp_success():
    repo = _make_repo(
        {
            "dhan": _make_cred_record(
                "dhan",
                {
                    "client_id": "1100000123",
                    "pin": "123456",
                    "totp_secret": TOTP_SECRET,
                    "access_token": "",
                },
            )
        }
    )

    async def fake_totp(client_id, pin, totp_secret):
        return {
            "success": True,
            "access_token": "dhan-jwt",
            "expiry_time": "",
        }

    with patch.object(DhanBroker, "authenticate_with_totp", staticmethod(fake_totp)):
        result = await perform_relogin("dhan", repo)

    assert result["success"] is True
    assert result["tokens"]["access_token"] == "dhan-jwt"
    saved = repo.saved["dhan"]
    from utils.encryption import decrypt_credentials

    stored = decrypt_credentials(saved.encrypted_credentials)
    assert stored["access_token"] == "dhan-jwt"


@pytest.mark.asyncio
async def test_perform_relogin_dhan_falls_back_to_renew():
    """No TOTP secret → try /v2/RenewToken on the stored web token."""
    repo = _make_repo(
        {"dhan": _make_cred_record("dhan", {"client_id": "1100000123", "access_token": "web-jwt"})}
    )

    async def fake_renew(self):
        return {"success": True, "access_token": "renewed-jwt", "expiry_time": ""}

    async def fake_close(self):
        return None

    with patch.object(DhanBroker, "renew_token", fake_renew), patch.object(
        DhanBroker, "close", fake_close
    ):
        result = await perform_relogin("dhan", repo)

    assert result["success"] is True
    assert result["tokens"]["access_token"] == "renewed-jwt"


@pytest.mark.asyncio
async def test_perform_relogin_fyers_returns_browser_url():
    repo = _make_repo(
        {
            "fyers": _make_cred_record(
                "fyers",
                {"app_id": "APP-100", "secret_key": "sk", "redirect_uri": "http://127.0.0.1:8000/api/brokers/fyers/callback"},
            )
        }
    )

    from brokers.fyers import FyersBroker

    def fake_build(app_id, redirect_uri, state="ultrabot"):
        return "https://api-t1.fyers.in/api/v3/generate-authcode?client_id=APP-100"

    with patch.object(FyersBroker, "build_auth_url", staticmethod(fake_build)):
        result = await perform_relogin("fyers", repo)

    assert result["success"] is False
    assert result["requires_browser"] is True
    assert "generate-authcode" in result["auth_url"]
    assert result["relogin_method"] == "browser"


@pytest.mark.asyncio
async def test_perform_relogin_missing_totp_reports_actionable_error():
    repo = _make_repo(
        {"angel_one": _make_cred_record("angel_one", {"api_key": "AK", "client_id": "A123", "pin": "1234"})}
    )
    result = await perform_relogin("angel_one", repo)
    assert result["success"] is False
    assert "TOTP" in result["message"]


@pytest.mark.asyncio
async def test_perform_relogin_unsupported_broker():
    repo = _make_repo({})
    result = await perform_relogin("paper", repo)
    assert result["success"] is False
    assert "does not need a daily re-login" in result["message"]


@pytest.mark.asyncio
async def test_perform_relogin_no_credentials():
    repo = _make_repo({})
    result = await perform_relogin("shoonya", repo)
    assert result["success"] is False
    assert "No stored credentials" in result["message"]


def test_apply_tokens_to_engine_hot_applies_matching_broker():
    engine = MagicMock()
    engine.broker_name = "angel_one"
    broker = MagicMock(spec=AngelOneBroker)
    engine.broker = broker

    ok = apply_tokens_to_engine(
        engine,
        "angel_one",
        {"kind": "angel_one", "jwt_token": "jwt", "feed_token": "feed", "refresh_token": "ref"},
    )
    assert ok is True
    broker.apply_session.assert_called_once_with(
        jwt_token="jwt", feed_token="feed", refresh_token="ref"
    )


def test_apply_tokens_to_engine_skips_different_broker():
    engine = MagicMock()
    engine.broker_name = "shoonya"
    engine.broker = MagicMock(spec=ShoonyaBroker)
    ok = apply_tokens_to_engine(
        engine, "angel_one", {"kind": "angel_one", "jwt_token": "jwt"}
    )
    assert ok is False


def test_apply_tokens_to_engine_shoonya_and_dhan_kinds():
    engine = MagicMock()
    engine.broker_name = "shoonya"
    sbroker = MagicMock(spec=ShoonyaBroker)
    engine.broker = sbroker
    assert apply_tokens_to_engine(
        engine, "shoonya", {"kind": "shoonya", "susertoken": "s", "actid": "A"}
    )
    sbroker.apply_session_token.assert_called_once_with(susertoken="s", actid="A")

    engine2 = MagicMock()
    engine2.broker_name = "dhan"
    dbroker = MagicMock(spec=DhanBroker)
    engine2.broker = dbroker
    assert apply_tokens_to_engine(engine2, "dhan", {"kind": "dhan", "access_token": "t"})
    dbroker.apply_session_token.assert_called_once_with(access_token="t")


@pytest.mark.asyncio
async def test_get_token_status_valid_expired_unknown():
    now = time.time()
    repo = _make_repo(
        {
            "angel_one": _make_cred_record(
                "angel_one", {"api_key": "AK"}, {"token_expires_at": now + 7200, "last_relogin_at": now - 60}
            ),
            "shoonya": _make_cred_record(
                "shoonya", {"user_id": "U"}, {"token_expires_at": now - 100, "last_relogin_at": now - 90000}
            ),
            "dhan": _make_cred_record("dhan", {"client_id": "D"}),
        }
    )

    statuses = {s["broker"]: s for s in await get_token_status(repo)}

    assert statuses["angel_one"]["token_state"] == "valid"
    assert statuses["angel_one"]["seconds_until_expiry"] > 0
    assert statuses["angel_one"]["can_auto_relogin"] is True
    assert statuses["angel_one"]["relogin_method"] == "totp"

    assert statuses["shoonya"]["token_state"] == "expired"
    assert statuses["shoonya"]["seconds_until_expiry"] == 0

    assert statuses["dhan"]["token_state"] == "unknown"
    assert statuses["dhan"]["token_expires_at"] is None


# ─────────────────────────────────────────────
# Engine start: DB credentials wiring
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_start_route_loads_db_credentials():
    """The /api/engine/start route must decrypt the broker's stored
    credentials and pass them into engine.start(broker_config=...)."""
    from api.routes.engine import _load_broker_config

    repo = _make_repo(
        {
            "shoonya": _make_cred_record(
                "shoonya",
                {
                    "user_id": "FA12345",
                    "password": "pw",
                    "vendor_code": "FA12345_U",
                    "app_key": "KEY",
                    "totp_secret": TOTP_SECRET,
                },
            )
        }
    )

    config = await _load_broker_config("shoonya", repo)
    assert config == {
        "user_id": "FA12345",
        "password": "pw",
        "vendor_code": "FA12345_U",
        "app_key": "KEY",
        "totp_secret": TOTP_SECRET,
    }

    # paper has no kwarg map → {}
    assert await _load_broker_config("paper", repo) == {}
    # missing credentials → {}
    assert await _load_broker_config("dhan", repo) == {}
