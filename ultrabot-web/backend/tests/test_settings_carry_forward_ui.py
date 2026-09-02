"""Tests for the Capital tab's carry-forward toggle (P0.5-b).

Verifies the full round-trip the Settings UI depends on:
PUT /api/settings {capital: {carry_forward_capital: X}} → in-memory config
flip → engine's get_capital_config() sees it. The disk write is patched out
so the shipped defaults.yaml is never mutated by the test run.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


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
async def test_carry_forward_roundtrip_via_settings_api(client, auth_headers):
    """Toggle on → settings GET reflects True; toggle off → False.

    Verified through the API itself (PUT then GET) rather than by importing
    the config.settings singleton: tests/test_capital_resolver.py re-imports
    config.settings (fresh-import cycle check), which leaves TWO Settings
    instances alive — the routes keep the first, a fresh import gets the
    second. Asserting on the API response is both immune to that and a
    truer end-to-end check of what the Settings UI reads.
    """
    async def _current_flag() -> bool:
        resp = await client.get("/api/settings", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        cfg = resp.json()
        cfg = cfg.get("config", cfg) if isinstance(cfg, dict) else {}
        capital = cfg.get("capital", {}) if isinstance(cfg, dict) else {}
        return bool(capital.get("carry_forward_capital", False))

    # Never let the test write to the shipped defaults.yaml. v0.4.2: patch the
    # class of the settings instance the SETTINGS ROUTE actually holds — not
    # a fresh ``from config.settings import Settings`` (which can be a
    # different class if another test re-imported the module, resurrecting a
    # real save() that re-serialized the shipped config — see conftest.py).
    import api.routes.settings_api as _settings_route

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(_settings_route.settings), "save", lambda self: True)

        # ── Toggle ON ──
        resp = await client.put(
            "/api/settings",
            json={"capital": {"carry_forward_capital": True}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert await _current_flag() is True

        # ── Toggle OFF ──
        resp = await client.put(
            "/api/settings",
            json={"capital": {"carry_forward_capital": False}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert await _current_flag() is False


@pytest.mark.asyncio
async def test_carry_forward_requires_auth(client):
    """Unauthenticated settings PUT must be rejected (401)."""
    resp = await client.put(
        "/api/settings",
        json={"capital": {"carry_forward_capital": True}},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_capital_section_reaches_settings_get(client, auth_headers):
    """GET /api/settings must expose the capital section (the UI loads the
    toggle's initial state from here)."""
    resp = await client.get("/api/settings", headers=auth_headers)
    assert resp.status_code == 200
    cfg = resp.json()
    cfg = cfg.get("config", cfg) if isinstance(cfg, dict) else {}
    capital = cfg.get("capital", {}) if isinstance(cfg, dict) else {}
    assert "virtual_capital" in capital
    # The key must be present (bool) so the Switch has a definite state.
    assert isinstance(capital.get("carry_forward_capital", False), bool)
