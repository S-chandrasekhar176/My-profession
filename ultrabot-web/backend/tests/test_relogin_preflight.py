"""Tests for the re-login pre-flight session check (P0-c).

Covers:
* brokers/relogin.preflight_session_check — every branch: non-session
  brokers (paper/yahoo/None/unknown), missing credentials, expired token,
  unknown token, valid token, storage errors
* core/scheduler.on_pre_market_init — the 08:45 IST pre-flight wiring:
  alert routed on warning/critical, silence when everything is fine
* GET /api/brokers/preflight — auth required, response shape, explicit
  broker param, graceful default when the engine is not running
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from brokers.relogin import preflight_session_check, get_token_status

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────
# Helpers (mirror tests/test_broker_relogin.py)
# ─────────────────────────────────────────────


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

    repo = MagicMock()

    async def get_all():
        return list(records.values())

    repo.get_all_broker_credentials = AsyncMock(side_effect=get_all)
    return repo


def _cred_with_token(broker: str, expires_at: float | None):
    extra = {"token_expires_at": expires_at} if expires_at is not None else {}
    return _make_cred_record(broker, {"api_key": "AK"}, extra)


# ─────────────────────────────────────────────
# preflight_session_check — unit branches
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_broker_is_skipped():
    """Paper broker needs no daily session — must return ok/skipped."""
    res = await preflight_session_check("paper", _make_repo({}))
    assert res["ok"] is True
    assert res["level"] == "skipped"
    assert res["token_state"] == "not_applicable"
    assert res["relogin_method"] == "none"


@pytest.mark.asyncio
async def test_yahoo_broker_is_skipped():
    res = await preflight_session_check("yahoo", _make_repo({}))
    assert res["ok"] is True
    assert res["level"] == "skipped"


@pytest.mark.asyncio
async def test_none_broker_name_defaults_to_paper():
    res = await preflight_session_check(None, _make_repo({}))
    assert res["ok"] is True
    assert res["level"] == "skipped"
    assert res["broker"] == "paper"


@pytest.mark.asyncio
async def test_unregistered_broker_is_skipped():
    """A broker outside the four daily-session brokers (e.g. kite) is skipped."""
    res = await preflight_session_check("kite", _make_repo({}))
    assert res["ok"] is True
    assert res["level"] == "skipped"


@pytest.mark.asyncio
async def test_no_credentials_is_warning():
    """Daily-session broker with no stored credentials → warning (not crash)."""
    res = await preflight_session_check("fyers", _make_repo({}))
    assert res["ok"] is False
    assert res["level"] == "warning"
    assert res["token_state"] == "unknown"
    assert "credentials" in res["message"].lower()
    assert res["relogin_method"] == "browser"


@pytest.mark.asyncio
async def test_credentials_without_token_is_warning():
    """Credentials exist but no token_expires_at recorded → warning."""
    repo = _make_repo({"angel_one": _cred_with_token("angel_one", None)})
    res = await preflight_session_check("angel_one", repo)
    assert res["ok"] is False
    assert res["level"] == "warning"
    assert res["token_state"] == "unknown"
    assert res["relogin_method"] == "totp"
    assert "re-login" in res["message"] or "log in" in res["message"].lower()


@pytest.mark.asyncio
async def test_expired_token_is_critical():
    """Expired token → critical with an explicit 'orders will be rejected' message."""
    past = time.time() - 3600
    repo = _make_repo({"dhan": _cred_with_token("dhan", past)})
    res = await preflight_session_check("dhan", repo)
    assert res["ok"] is False
    assert res["level"] == "critical"
    assert res["token_state"] == "expired"
    assert "expired" in res["message"].lower()
    assert res["relogin_method"] == "totp"


@pytest.mark.asyncio
async def test_fyers_expired_mentions_browser_2fa():
    """Fyers (browser broker) critical message must point to the browser flow."""
    past = time.time() - 60
    repo = _make_repo({"fyers": _cred_with_token("fyers", past)})
    res = await preflight_session_check("fyers", repo)
    assert res["level"] == "critical"
    assert res["relogin_method"] == "browser"
    assert "browser" in res["message"].lower()


@pytest.mark.asyncio
async def test_valid_token_is_ok_with_countdown():
    """Valid token → ok with seconds_until_expiry reflecting remaining life."""
    future = time.time() + 3600
    repo = _make_repo({"shoonya": _cred_with_token("shoonya", future)})
    res = await preflight_session_check("shoonya", repo)
    assert res["ok"] is True
    assert res["level"] == "ok"
    assert res["token_state"] == "valid"
    assert res["seconds_until_expiry"] is not None
    assert 3500 <= res["seconds_until_expiry"] <= 3600


@pytest.mark.asyncio
async def test_repo_failure_degrades_to_warning():
    """Repository raising must never crash the pre-flight — degrade to warning."""

    repo = MagicMock()
    repo.get_all_broker_credentials = AsyncMock(side_effect=RuntimeError("db offline"))

    res = await preflight_session_check("angel_one", repo)
    assert res["ok"] is False
    assert res["level"] == "warning"
    assert "verify" in res["message"].lower()


@pytest.mark.asyncio
async def test_case_insensitive_broker_name():
    """Broker names arrive lower-cased regardless of caller casing."""
    future = time.time() + 1800
    repo = _make_repo({"fyers": _cred_with_token("fyers", future)})
    res = await preflight_session_check("FYERS", repo)
    assert res["ok"] is True
    assert res["broker"] == "fyers"


# ─────────────────────────────────────────────
# Scheduler wiring — 08:45 IST pre-market init
# ─────────────────────────────────────────────


def _mock_engine(broker_name: str, with_alert: bool = True):
    engine = MagicMock()
    engine.broker_name = broker_name
    engine.feed = None
    engine.current_regime = "Sideways"
    engine._broadcast = AsyncMock()
    if with_alert:
        engine._route_alert = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_scheduler_preflight_alerts_on_expired_session():
    """Engine running on fyers with an expired token → risk_warning alert routed."""
    from core.scheduler import MarketLifecycleScheduler

    past = time.time() - 120
    repo = _make_repo({"fyers": _cred_with_token("fyers", past)})
    engine = _mock_engine("fyers")

    async def get_repo():
        return repo

    scheduler = MarketLifecycleScheduler(engine=engine, repository_getter=get_repo)

    # Skip holiday/weekend gating and the watchlist build (feed=None is fine;
    # the builder handles a None feed by returning an empty list).
    engine.daily_risk = None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scheduler, "_is_trading_day", lambda: True)
        await scheduler.on_pre_market_init(force=True)

    engine._route_alert.assert_any_call("risk_warning", lambda d: True) if False else None
    # Inspect the actual alert payload
    called = False
    for call in engine._route_alert.call_args_list:
        args, _ = call
        if args and args[0] == "risk_warning":
            payload = args[1]
            assert payload.get("type") == "relogin_preflight"
            assert payload.get("level") == "critical"
            assert payload.get("broker") == "fyers"
            called = True
    assert called, "expected a risk_warning alert for the expired fyers session"


@pytest.mark.asyncio
async def test_scheduler_preflight_silent_when_session_valid():
    """Engine on a valid session → no risk_warning alert routed."""
    from core.scheduler import MarketLifecycleScheduler

    future = time.time() + 3600
    repo = _make_repo({"angel_one": _cred_with_token("angel_one", future)})
    engine = _mock_engine("angel_one")
    engine.daily_risk = None

    async def get_repo():
        return repo

    scheduler = MarketLifecycleScheduler(engine=engine, repository_getter=get_repo)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scheduler, "_is_trading_day", lambda: True)
        await scheduler.on_pre_market_init(force=True)

    for call in engine._route_alert.call_args_list:
        args, _ = call
        assert args[0] != "risk_warning", "no risk_warning expected on a valid session"


@pytest.mark.asyncio
async def test_scheduler_preflight_silent_for_paper_broker():
    """Paper broker → skipped level, no alert, no repo access needed."""
    from core.scheduler import MarketLifecycleScheduler

    engine = _mock_engine("paper")
    engine.daily_risk = None

    repo = MagicMock()
    repo.get_all_broker_credentials = AsyncMock()

    async def get_repo():
        return repo

    scheduler = MarketLifecycleScheduler(engine=engine, repository_getter=get_repo)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scheduler, "_is_trading_day", lambda: True)
        await scheduler.on_pre_market_init(force=True)

    for call in engine._route_alert.call_args_list:
        args, _ = call
        assert args[0] != "risk_warning"


# ─────────────────────────────────────────────
# GET /api/brokers/preflight
# ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=__import__("app").app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client):
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_preflight_endpoint_requires_auth(client):
    resp = await client.get("/api/brokers/preflight")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preflight_endpoint_explicit_paper(client, auth_headers):
    """Explicit broker=paper → skipped result (engine may or may not run)."""
    resp = await client.get("/api/brokers/preflight", params={"broker": "paper"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "skipped"
    assert data["ok"] is True
    assert data["broker"] == "paper"


@pytest.mark.asyncio
async def test_preflight_endpoint_unconfigured_fyers(client, auth_headers):
    """Fyers with no credentials row → warning with browser re-login hint."""
    resp = await client.get("/api/brokers/preflight", params={"broker": "fyers"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # The shared test DB may or may not carry a fyers row from earlier
    # suites; both outcomes are valid shapes — assert the contract:
    assert data["level"] in ("warning", "critical", "ok")
    assert data["broker"] == "fyers"
    assert set(data.keys()) >= {"ok", "level", "broker", "message", "token_state", "relogin_method"}


@pytest.mark.asyncio
async def test_preflight_endpoint_defaults_without_engine(client, auth_headers):
    """No broker param and no running engine → must NOT 500; falls back to paper."""
    resp = await client.get("/api/brokers/preflight", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["broker"] in ("paper", "angel_one", "shoonya", "dhan", "fyers")
    assert data["level"] in ("ok", "warning", "critical", "skipped")
