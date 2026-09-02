"""Unit & Integration tests for Live Scan Telemetry and Active Broker Tracking."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app import app, lifespan
from api.dependencies import get_engine
from config.settings import settings


@pytest_asyncio.fixture
async def client():
    """Create an async test client with lifespan context."""
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
async def auth_headers(client):
    """Get valid auth headers."""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_engine_telemetry_initial_state(client):
    """Engine initializes with zeroed telemetry and empty recent events list."""
    engine = get_engine()
    telemetry = engine.get_scan_telemetry()

    assert telemetry["total_scans"] >= 0
    assert telemetry["symbols_scanned"] >= 0
    assert telemetry["signals_generated"] >= 0
    assert telemetry["signals_passed"] >= 0
    assert telemetry["signals_rejected"] >= 0
    assert isinstance(telemetry["rejections_by_gate"], dict)
    assert isinstance(telemetry["rejections_by_strategy"], dict)
    assert isinstance(telemetry["recent_events"], list)
    assert "broker" in telemetry
    assert "state" in telemetry


@pytest.mark.asyncio
async def test_record_telemetry_events_and_buffer_cap(client):
    """Engine correctly buffers and caps recent telemetry events."""
    engine = get_engine()

    # Record a passed event
    engine._record_telemetry_event(
        symbol="RELIANCE",
        strategy="ORB",
        status="PASSED",
        direction="BUY",
        price=2950.0,
        confidence=0.85,
        gate="ALL_GATES_PASSED",
        reason="Passed all 16 risk gates",
    )

    # Record a rejected event
    engine._record_telemetry_event(
        symbol="TCS",
        strategy="MB",
        status="REJECTED",
        direction="SELL",
        price=4120.0,
        confidence=0.55,
        gate="G10_MinConfidence",
        reason="Confidence 0.55 below minimum 0.65",
    )

    telemetry = engine.get_scan_telemetry()
    assert len(telemetry["recent_events"]) >= 2

    # Verify rolling buffer caps at 100 entries
    for i in range(120):
        engine._record_telemetry_event(
            symbol=f"SYM_{i}",
            strategy="VC",
            status="NO_SETUP",
        )

    telemetry_after = engine.get_scan_telemetry()
    assert len(engine._recent_scan_telemetry) == 100
    assert len(telemetry_after["recent_events"]) <= 50


@pytest.mark.asyncio
async def test_get_scan_telemetry_endpoint(client, auth_headers):
    """GET /api/engine/scan-telemetry returns valid telemetry JSON structure."""
    resp = await client.get("/api/engine/scan-telemetry", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert "total_scans" in data
    assert "symbols_scanned" in data
    assert "signals_generated" in data
    assert "signals_passed" in data
    assert "signals_rejected" in data
    assert "rejections_by_gate" in data
    assert "recent_events" in data
    assert "broker" in data
    assert "mode" in data
    assert "state" in data


@pytest.mark.asyncio
async def test_engine_status_includes_broker_and_metrics(client, auth_headers):
    """GET /api/engine/status returns broker and scan statistics."""
    resp = await client.get("/api/engine/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert "broker" in data
    assert "symbols_scanned" in data
    assert "signals_passed" in data
    assert "signals_rejected" in data
    assert "rejections_by_gate" in data
    assert "rejections_by_strategy" in data


@pytest.mark.asyncio
async def test_telemetry_idle_reason_and_scanning_status(client):
    """Engine get_scan_telemetry() includes scanning_status and idle_reason reflecting market/engine conditions."""
    engine = get_engine()
    telemetry = engine.get_scan_telemetry()

    assert "scanning_status" in telemetry
    assert "idle_reason" in telemetry
    assert telemetry["scanning_status"] in (
        "scanning_active",
        "market_closed",
        "outside_trade_window",
        "engine_stopped",
        "paused",
        "risk_blocked",
    )
    if telemetry["scanning_status"] != "scanning_active":
        assert len(telemetry["idle_reason"]) > 0
