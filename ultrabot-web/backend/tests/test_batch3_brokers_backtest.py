import pytest
from unittest.mock import MagicMock, AsyncMock
from brokers.paper_broker import PaperBroker
from brokers.angel_one import AngelOneBroker
from feeds.shoonya_websocket import ShoonyaWebSocketFeed
from fees.nse_fee_calculator import NSEFeeCalculator


@pytest.mark.asyncio
async def test_paper_broker_short_position_lifecycle():
    calc = NSEFeeCalculator()
    broker = PaperBroker(initial_capital=100000.0, fee_calculator=calc)

    # 1. Place a SELL (SHORT) order
    res = await broker.place_order(
        symbol="TATASTEEL",
        exchange="NSE",
        transaction_type="SELL",
        quantity=100,
        order_type="MARKET",
        price=150.0,
        product="MIS",
    )
    assert res["success"] is True
    assert "TATASTEEL" in broker.positions

    pos = broker.positions["TATASTEEL"]
    assert pos["direction"] == "SHORT"
    assert pos["quantity"] == 100
    assert pos["entry_price"] == 150.0
    assert pos["status"] == "OPEN"

    # 2. Close the SHORT position with a lower exit price (profitable short)
    close_res = await broker.close_position(
        symbol="TATASTEEL",
        qty=100,
        exit_price=140.0,
    )
    assert close_res["success"] is True
    assert pos["status"] == "CLOSED"
    assert pos["realized_pnl"] > 900.0  # Gross ₹1000 - fees


def test_angel_one_auth_headers_consolidated():
    broker = AngelOneBroker(
        api_key="mock_key",
        client_code="C12345",
        pin="1234",
        totp_secret="MOCKTOTP",
    )
    broker.jwt_token = "jwt_token_123"
    broker.feed_token = "feed_token_456"

    headers = broker._auth_headers()
    assert headers["Authorization"] == "Bearer jwt_token_123"
    assert headers["X-ClientCode"] == "C12345"
    assert headers["X-FeedToken"] == "feed_token_456"
    assert headers["X-PrivateKey"] == "mock_key"
    assert "X-ClientLocalIP" in headers


def test_shoonya_feed_initialization():
    feed = ShoonyaWebSocketFeed(
        user_id="U123",
        password="pwd",
        vendor_code="VCODE",
        app_key="APPKEY",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    assert feed.user_id == "U123"
    assert feed.totp_secret == "JBSWY3DPEHPK3PXP"
