"""Tests for the DELETE /api/brokers/{broker}/credentials endpoint.

Exercises the real FastAPI app via ASGI transport against the real DB
(self-cleaning: seeds and removes its own rows; uses the 'upstox' broker
name which has no save-credentials UI, so it can never hold real user data).
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app import app
from db.database import async_session_factory
from db.repository import Repository


@pytest_asyncio.fixture
async def client():
    """Create an async test client."""
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


async def _seed_upstox_credentials() -> None:
    """Store a (fake) upstox credential row the way a real save would."""
    from utils.encryption import encrypt_credentials

    async with async_session_factory() as session:
        repo = Repository(session)
        await repo.save_broker_credentials(
            broker_name="upstox",
            encrypted_creds=encrypt_credentials({"api_key": "test-key"}),
            extra={"account_type": "live"},
        )


async def _cleanup_upstox_credentials() -> None:
    async with async_session_factory() as session:
        repo = Repository(session)
        await repo.delete_broker_credentials("upstox")


@pytest.mark.asyncio
async def test_delete_broker_credentials_requires_auth(client):
    """Unauthenticated DELETE must be rejected."""
    resp = await client.delete("/api/brokers/upstox/credentials")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_broker_credentials_rejects_invalid_broker(client, auth_headers):
    """DELETE with an unknown broker name must 400."""
    resp = await client.delete(
        "/api/brokers/not_a_real_broker/credentials",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "Invalid broker" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_broker_credentials_nothing_stored(client, auth_headers):
    """DELETE when nothing is stored returns success=False (idempotent no-op)."""
    await _cleanup_upstox_credentials()
    resp = await client.delete(
        "/api/brokers/upstox/credentials",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["broker"] == "upstox"


@pytest.mark.asyncio
async def test_delete_broker_credentials_round_trip(client, auth_headers):
    """Seed → status shows configured → DELETE → success + row gone + status clean."""
    await _seed_upstox_credentials()
    try:
        # The status endpoint must now report upstox as configured.
        status = await client.get("/api/brokers", headers=auth_headers)
        assert status.status_code == 200
        brokers = status.json()["brokers"]
        assert any(b["broker"] == "upstox" and b["has_credentials"] for b in brokers)

        # DELETE removes the stored row.
        resp = await client.delete(
            "/api/brokers/upstox/credentials",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["broker"] == "upstox"

        # Status no longer lists upstox.
        status2 = await client.get("/api/brokers", headers=auth_headers)
        brokers2 = status2.json()["brokers"]
        assert not any(b["broker"] == "upstox" for b in brokers2)

        # Repo-level confirmation.
        async with async_session_factory() as session:
            repo = Repository(session)
            assert await repo.get_broker_credentials("upstox") is None

        # Deleting again is an honest no-op.
        resp2 = await client.delete(
            "/api/brokers/upstox/credentials",
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["success"] is False
    finally:
        await _cleanup_upstox_credentials()
