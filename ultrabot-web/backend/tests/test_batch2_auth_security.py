import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from api.dependencies import get_current_user, create_access_token
from api.routes.auth import revoke_token
from api.routes.risk import update_risk_limits, RiskLimitsUpdate
from api.websocket import websocket_endpoint
from config.settings import settings


@pytest.mark.asyncio
async def test_rest_token_revocation_enforcement():
    token = create_access_token({"sub": "admin_revoked_subject"})

    # Valid token works
    user = await get_current_user(token)
    assert user == "admin_revoked_subject"

    # Revoke token
    revoke_token(token)

    # Now get_current_user must reject with 401
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_websocket_mandatory_auth():
    ws_mock = AsyncMock()
    ws_mock.close = AsyncMock()

    # 1. Reject missing token
    await websocket_endpoint(ws_mock, token=None)
    ws_mock.close.assert_called_with(code=1008, reason="Authentication token required")

    # 2. Reject invalid token
    ws_mock.close.reset_mock()
    await websocket_endpoint(ws_mock, token="invalid-token-string")
    ws_mock.close.assert_called_with(code=1008, reason="Invalid token")

    # 3. Reject revoked token
    revoked = create_access_token({"sub": "ws_revoked_subject"})
    revoke_token(revoked)
    ws_mock.close.reset_mock()
    await websocket_endpoint(ws_mock, token=revoked)
    ws_mock.close.assert_called_with(code=1008, reason="Token revoked")


@pytest.mark.asyncio
async def test_risk_limits_update_section_routing(monkeypatch):
    # v0.4.6 SENTINEL RULE: payload values must NEVER equal the shipped
    # defaults — a test payload whose values coincide with defaults.yaml is
    # how the v0.4.2 hard_risk_pct 1.0→1.5 / vix_threshold 20→22.0 pollution
    # shipped invisibly (an unpatched save() persisted exactly these values).
    # 0.85 / 19.0 / 12.5 differ from every shipped default so any future
    # leak is caught by the conftest pristine-values tripwire AND can never
    # masquerade as a legitimate config value.
    body = RiskLimitsUpdate(
        kelly_max_fraction=0.08,
        hard_risk_pct=0.85,
        max_position_size_pct=12.5,
        vix_high_threshold=19.0,
    )

    from config.settings import Settings
    monkeypatch.setattr(Settings, "save", lambda self: True)

    # Seed the capital section with sentinels — v0.4.2 invariant: the risk
    # route must NEVER write into the capital section (it is owned solely by
    # PUT /api settings / the Capital tab). Regression guard for the
    # config-hygiene cross-write incident.
    sentinel_capital = {
        "virtual_capital": 500000,
        "max_capital_usage_pct": 90,
        "min_position_size": 5000,
        "max_per_position_pct": 25.0,
        "carry_forward_capital": True,
    }
    settings._raw_config["capital"] = dict(sentinel_capital)

    res = await update_risk_limits(body=body, username="admin")
    assert res["message"] == "Risk limits updated successfully"

    # Verify position sizing config was updated
    pos_cfg = settings._raw_config.get("position_sizing", {})
    assert pos_cfg.get("kelly_max_fraction") == 0.08
    assert pos_cfg.get("hard_risk_pct") == 0.85

    # Verify risk config and key aliases (risk-section keys only)
    risk_cfg = settings._raw_config.get("risk", {})
    assert risk_cfg.get("max_per_position_pct") == 12.5
    assert risk_cfg.get("max_position_size_pct") == 12.5
    assert risk_cfg.get("vix_threshold") == 19.0
    assert risk_cfg.get("vix_high_threshold") == 19.0

    # Verify capital config is COMPLETELY UNTOUCHED by the risk save
    cap_cfg = settings._raw_config.get("capital", {})
    assert cap_cfg.get("max_per_position_pct") == 25.0
    assert cap_cfg == sentinel_capital


@pytest.mark.asyncio
async def test_jwt_token_authentication():
    from api.dependencies import create_access_token
    token = create_access_token(data={"sub": "admin"})
    user = await get_current_user(token)
    assert user == "admin"

    # Test invalid / demo token rejection
    with pytest.raises(HTTPException):
        await get_current_user("demo-token")

    # WebSocket authentication with valid JWT
    ws_mock = AsyncMock()
    ws_mock.close = AsyncMock()
    ws_mock.accept = AsyncMock()
    ws_mock.receive_text = AsyncMock(side_effect=Exception("disconnect"))

    await websocket_endpoint(ws_mock, token=token)
    ws_mock.accept.assert_called_once()


@pytest.mark.asyncio
async def test_angel_one_refresh_headers():
    from brokers.angel_one import AngelOneBroker
    broker = AngelOneBroker(
        api_key="test_key",
        client_code="CLIENT123",
        pin="1234",
        jwt_token="old_expired_jwt",
        refresh_token="valid_refresh_token",
    )
    # Set expired
    broker.token_manager.store_token("angel_one", "old_expired_jwt", refresh_token="valid_refresh_token", ttl=-10)

    # Mock client
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": True, "data": {"jwtToken": "new_jwt_123", "feedToken": "feed_123"}}
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=mock_resp)
    broker._client = mock_client

    refreshed = await broker._refresh_if_needed()
    assert refreshed is True
    assert broker.jwt_token == "new_jwt_123"

    # Verify that Authorization header sent to _REFRESH_URL used Bearer <refresh_token>
    args, kwargs = mock_client.post.call_args
    headers = kwargs.get("headers", {})
    assert headers.get("Authorization") == "Bearer valid_refresh_token"


@pytest.mark.asyncio
async def test_shoonya_unmapped_token_fallback():
    from brokers.shoonya import ShoonyaBroker
    broker = ShoonyaBroker(user_id="U123")
    broker._authenticated = True
    broker._session_token = "valid_session"

    # When querying an unmapped symbol, ShoonyaBroker should safely fall back without crashing
    with patch("feeds.feed_manager.FeedManager.get_latest_price", AsyncMock(return_value=1500.0)):
        price = await broker.get_ltp("UNKNOWN_SYMBOL_XYZ")
        assert price == 1500.0

