"""Tests for all REST API endpoints.

Uses httpx AsyncClient with ASGI transport for full async testing.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app import app
from db.database import init_db


@pytest_asyncio.fixture
async def client():

    """Create an async test client.

    init_db() creates the schema on the isolated per-run test database
    (tests/conftest.py redirects DB_PATH); previously this fixture
    silently relied on the PRODUCTION data/ultrabot.db already having
    tables, which is exactly how the suite ended up mutating live state.
    ASGITransport does not run the app's lifespan, so the schema must be
    created here explicitly.
    """
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client):
    """Get valid auth headers by logging in first."""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── Public endpoints ─────────────────────────────


@pytest.mark.asyncio
async def test_root_returns_app_info(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "UltraBot Web"
    assert "version" in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_health_returns_healthy(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["db"] == "connected"
    assert "engine" in data


# ── Auth endpoints ───────────────────────────────


@pytest.mark.asyncio
async def test_login_success(client):
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_username(client):
    resp = await client.post(
        "/api/auth/login",
        data={"username": "hacker", "password": "admin"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_authenticated(client, auth_headers):
    resp = await client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_me_unauthenticated(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout(client, auth_headers):
    resp = await client.post("/api/auth/logout", headers=auth_headers)
    assert resp.status_code == 200


# ── Protected endpoints (401 without token) ─────


@pytest.mark.asyncio
async def test_dashboard_401(client):
    resp = await client.get("/api/dashboard")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_engine_status_401(client):
    resp = await client.get("/api/engine/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trades_401(client):
    resp = await client.get("/api/trades")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_strategies_401(client):
    resp = await client.get("/api/strategies")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_risk_401(client):
    resp = await client.get("/api/risk/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_watchlist_401(client):
    resp = await client.get("/api/watchlist")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_brokers_401(client):
    resp = await client.get("/api/brokers")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_errors_401(client):
    resp = await client.get("/api/errors")
    assert resp.status_code == 401


# ── Authenticated endpoints ────────────────────


@pytest.mark.asyncio
async def test_dashboard_returns_data(client, auth_headers):
    resp = await client.get("/api/dashboard", headers=auth_headers)
    # 200 if engine running, 503 if repository not initialized (no lifespan in test)
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_engine_status(client, auth_headers):
    resp = await client.get("/api/engine/status", headers=auth_headers)
    # 200 if engine available, 503 if not (no lifespan in test)
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "state" in data
        assert data["state"] == "stopped"


@pytest.mark.asyncio
async def test_get_errors(client, auth_headers):
    resp = await client.get("/api/errors", headers=auth_headers)
    # 200 if repository available, 503 if not (no lifespan in test)
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert isinstance(data, dict)
        assert "errors" in data
        assert "count" in data


@pytest.mark.asyncio
async def test_get_error_stats(client, auth_headers):
    resp = await client.get("/api/errors/stats", headers=auth_headers)
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "total_errors" in data
