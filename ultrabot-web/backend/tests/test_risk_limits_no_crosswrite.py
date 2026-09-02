"""Tests for the v0.4.2 risk-limits cross-write fix (config-hygiene incident).

INVARIANT under test: PUT /api/risk/limits owns ONLY the `risk` and
`position_sizing` config sections. The `capital` section is owned
exclusively by PUT /api/settings (the Capital tab).

Pre-existing bug being regression-guarded: the route used to write
max_per_position_pct / max_capital_usage_pct into the CAPITAL section too,
so saving risk limits silently changed the user's capital allocation
(a stale 10% once leaked into capital.max_per_position_pct this way).

All tests run through the real FastAPI route (authenticated) with the
disk save patched out, so the shipped defaults.yaml is never mutated.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import api.routes.risk as _risk_route

# The settings object the risk route ACTUALLY holds. Patching via
# ``type(...)`` is singleton-split-proof: even if some other test leaves a
# second Settings class in sys.modules, we always patch (and seed/assert
# through) the exact instance the route under test uses.
_route_settings = _risk_route.settings

SENTINEL_CAPITAL = {
    "virtual_capital": 500000,
    "max_capital_usage_pct": 90,
    "min_position_size": 5000,
    "max_per_position_pct": 25.0,
    "carry_forward_capital": True,
}


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


@pytest.fixture
def capital_sentinel(monkeypatch):
    """Seed a known capital section, patch out disk writes, restore after.

    Disk-write patch targets the ROUTE's own Settings instance class, so no
    module-split can resurrect a real save() (see conftest.py tripwire).
    """
    monkeypatch.setattr(type(_route_settings), "save", lambda self: True)
    original = _route_settings._raw_config.get("capital")
    _route_settings._raw_config["capital"] = dict(SENTINEL_CAPITAL)
    yield dict(SENTINEL_CAPITAL)
    # Restore whatever was there before so other tests see a pristine world
    if original is None:
        _route_settings._raw_config.pop("capital", None)
    else:
        _route_settings._raw_config["capital"] = original


def _capital_now() -> dict:
    return _route_settings._raw_config.get("capital", {})


@pytest.mark.asyncio
async def test_max_position_size_pct_never_touches_capital(client, auth_headers, capital_sentinel):
    """THE incident scenario: saving a risk position-size limit must leave
    capital.max_per_position_pct exactly as the user set it in the Capital tab."""
    resp = await client.put(
        "/api/risk/limits",
        json={"max_position_size_pct": 12.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    risk_cfg = _route_settings._raw_config.get("risk", {})
    assert risk_cfg.get("max_position_size_pct") == 12.0
    assert risk_cfg.get("max_per_position_pct") == 12.0  # G3 alias inside risk section

    # Capital section must be byte-identical to the sentinel
    assert _capital_now() == capital_sentinel
    assert _capital_now()["max_per_position_pct"] == 25.0


@pytest.mark.asyncio
async def test_max_capital_usage_pct_never_touches_capital(client, auth_headers, capital_sentinel):
    """Second cross-write that shipped with the same bug: the risk-side
    capital-usage view must not overwrite capital.max_capital_usage_pct."""
    resp = await client.put(
        "/api/risk/limits",
        json={"max_capital_usage_pct": 60.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    risk_cfg = _route_settings._raw_config.get("risk", {})
    assert risk_cfg.get("max_capital_usage_pct") == 60.0

    # The capital section's own usage limit is UNCHANGED (sentinel had 90)
    assert _capital_now()["max_capital_usage_pct"] == 90
    assert _capital_now() == capital_sentinel


@pytest.mark.asyncio
async def test_kelly_fraction_never_touches_capital(client, auth_headers, capital_sentinel):
    resp = await client.put(
        "/api/risk/limits",
        json={"kelly_max_fraction": 0.06},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    pos_cfg = _route_settings._raw_config.get("position_sizing", {})
    assert pos_cfg.get("kelly_max_fraction") == 0.06
    assert _capital_now() == capital_sentinel


@pytest.mark.asyncio
async def test_full_payload_leaves_capital_untouched(client, auth_headers, capital_sentinel):
    """Every accepted field at once — the exact payload shape the Settings UI
    sends — must still not leak a single key into the capital section."""
    payload = {
        "max_daily_trades": 12,
        "max_daily_loss_pct": 2.0,
        "max_open_positions": 4,
        "max_position_size_pct": 15.0,
        "max_consecutive_losses": 4,
        "max_drawdown_pct": 6.0,
        "max_sector_concentration_pct": 35.0,
        "vix_high_threshold": 24.0,
        "max_capital_usage_pct": 70.0,
        "cooloff_minutes": 45,
        "min_signal_confidence": 0.65,
        "kelly_max_fraction": 0.07,
        "hard_risk_pct": 1.2,
    }
    resp = await client.put("/api/risk/limits", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    assert _capital_now() == capital_sentinel


@pytest.mark.asyncio
async def test_repeated_risk_saves_capital_stays_stable(client, auth_headers, capital_sentinel):
    """Save risk limits 5 times in a row with different values — the capital
    section must remain bit-identical throughout (no cumulative drift)."""
    for i, vix in enumerate([20.0, 22.0, 24.0, 26.0, 28.0]):
        resp = await client.put(
            "/api/risk/limits",
            json={"vix_high_threshold": vix, "max_position_size_pct": 10.0 + i},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _capital_now() == capital_sentinel, f"capital drifted on iteration {i}"


@pytest.mark.asyncio
async def test_missing_capital_section_not_created_by_risk_save(client, auth_headers, monkeypatch):
    """Edge case: config with NO capital section at all — a risk save must not
    create one (the old bug used setdefault('capital', {}) which materialised
    an empty section on every risk save)."""
    monkeypatch.setattr(type(_route_settings), "save", lambda self: True)
    original = _route_settings._raw_config.pop("capital", None)
    try:
        resp = await client.put(
            "/api/risk/limits",
            json={"max_position_size_pct": 12.0, "max_capital_usage_pct": 55.0},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "capital" not in _route_settings._raw_config, (
            "risk save materialised a capital section — cross-write regression"
        )
    finally:
        if original is not None:
            _route_settings._raw_config["capital"] = original


@pytest.mark.asyncio
async def test_unauthenticated_risk_save_rejected(client, capital_sentinel):
    resp = await client.put(
        "/api/risk/limits",
        json={"max_position_size_pct": 12.0},
    )
    assert resp.status_code == 401
    assert _capital_now() == capital_sentinel


@pytest.mark.asyncio
async def test_out_of_bounds_value_rejected_before_any_write(client, auth_headers, capital_sentinel):
    """422 validation must fire BEFORE any config mutation (FastAPI model
    bounds), so a fat-fingered value can never partially apply."""
    resp = await client.put(
        "/api/risk/limits",
        json={"max_position_size_pct": 99.0},  # bound is 1.0–15.0
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert _capital_now() == capital_sentinel
