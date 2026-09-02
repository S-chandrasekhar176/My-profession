import json
import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import pyotp

import httpx

from brokers.base import BaseBroker
from brokers.token_manager import TokenManager
from errors.error_types import BrokerError, ConnectionLostError, TokenExpiredError

logger = logging.getLogger(__name__)

# IST timezone (UTC+5:30) — SmartAPI sessions are cut at midnight IST.
_IST = timezone(timedelta(hours=5, minutes=30))

# Angel One SmartAPI endpoints
_BASE_URL = "https://apiconnect.angelone.in"
_LOGIN_URL = f"{_BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword"
_REFRESH_URL = f"{_BASE_URL}/rest/auth/angelbroking/jwt/v1/generateTokens"
_QUOTE_URL = f"{_BASE_URL}/rest/secure/angelbroking/marketData/v1/quote"
_MARGIN_URL = f"{_BASE_URL}/rest/secure/angelbroking/user/v1/getRms"
_ORDER_URL = f"{_BASE_URL}/rest/secure/angelbroking/order/v1/placeOrder"
_CANCEL_URL = f"{_BASE_URL}/rest/secure/angelbroking/order/v1/cancelOrder"
_ORDER_STATUS_URL = f"{_BASE_URL}/rest/secure/angelbroking/order/v1/orderDetails"
_POSITIONS_URL = f"{_BASE_URL}/rest/secure/angelbroking/position/v1/book"

# Standard NSE symbol token mapping for SmartAPI
_TOKEN_MAP: Dict[str, str] = {
    "RELIANCE": "2885",
    "TCS": "11536",
    "INFY": "1594",
    "HDFCBANK": "1333",
    "ICICIBANK": "4963",
    "SBIN": "3045",
    "BHARTIARTL": "10604",
    "ITC": "1660",
    "TMPV": "3456",   # Tata Motors PV — inherited TATAMOTORS scrip code post Oct-2025 demerger
    "TMCV": "759782",  # Tata Motors CV — new NSE scrip code
    "LT": "11483",
    "BAJFINANCE": "317",
    "MARUTI": "10999",
    "SUNPHARMA": "3351",
    "WIPRO": "3787",
    "AXISBANK": "5900",
    "KOTAKBANK": "1922",
    "HCLTECH": "7229",
    "TATASTEEL": "3499",
    "TITAN": "3506",
    "ASIANPAINT": "236",
    "HINDUNILVR": "1394",
    "ADANIENT": "25",
    "ADANIPORTS": "15083",
    "BAJAJFINSV": "16675",
    "BAJAJ-AUTO": "16669",
    "BPCL": "526",
    "CIPLA": "694",
    "COALINDIA": "20374",
    "DRREDDY": "881",
    "EICHERMOT": "910",
    "GRASIM": "1232",
    "HEROMOTOCO": "1348",
    "HINDALCO": "1363",
    "INDUSINDBK": "5258",
    "JSWSTEEL": "11723",
    "M&M": "2031",
    "NESTLEIND": "17963",
    "NTPC": "11630",
    "ONGC": "2475",
    "POWERGRID": "14977",
    "TECHM": "13538",
    "ULTRACEMCO": "11532",
    "APOLLOHOSP": "157",
    "BRITANNIA": "547",
    "DIVISLAB": "10940",
    "HDFCLIFE": "467",
    "SBILIFE": "21808",
    "SHRIRAMFIN": "4306",
    "TRENT": "1964",
    "BEL": "383",
    "NIFTY": "26000",
    "BANKNIFTY": "26009",
    "FINNIFTY": "26037",
    "MIDCPNIFTY": "26074",
}


def _get_local_ip() -> str:
    """Dynamically determine host local IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_mac_address() -> str:
    """Dynamically format host MAC address."""
    try:
        node = uuid.getnode()
        mac = ":".join(f"{(node >> ele) & 0xff:02x}" for ele in range(40, -1, -8))
        return mac
    except Exception:
        return "02:00:00:00:00:00"


def _next_midnight_ist_epoch() -> float:
    """Epoch seconds of the next 00:00 IST. SmartAPI docs: "The session
    established via SmartAPI remains active till 12 midnight"."""
    now_ist = datetime.now(_IST)
    midnight = (now_ist + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


class AngelOneBroker(BaseBroker):
    """Angel One SmartAPI broker integration.

    Uses httpx for HTTP calls to Angel One's REST API.
    Requires api_key, client_code, and pin for authentication.
    """

    def __init__(
        self,
        api_key: str = "",
        client_code: str = "",
        pin: str = "",
        totp_secret: str = "",
        jwt_token: str = "",
        refresh_token: str = "",
        feed_token: str = "",
        token_manager: Optional[TokenManager] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        self.api_key = api_key or self.config.get("api_key", "")
        self.client_code = client_code or self.config.get("client_code", "")
        self.pin = pin or self.config.get("pin", "")
        self.totp_secret = totp_secret or self.config.get("totp_secret", "")
        self.jwt_token = jwt_token or self.config.get("jwt_token", "")
        self.refresh_token = refresh_token or self.config.get("refresh_token", "")
        self.feed_token = feed_token or self.config.get("feed_token", "")
        self.token_manager = token_manager or TokenManager()
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=30.0,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        return self._client

    def _auth_headers(self) -> Dict[str, str]:
        """Construct authenticated request headers with SmartAPI credentials and local IP/MAC."""
        local_ip = _get_local_ip()
        mac_addr = _get_mac_address()
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "X-ClientCode": self.client_code,
            "X-FeedToken": self.feed_token,
            "X-ClientPublicIP": local_ip,
            "X-ClientLocalIP": local_ip,
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-MACAddress": mac_addr,
            "X-PrivateKey": self.api_key.strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def authenticate(self) -> Dict[str, Any]:
        """Authenticate with Angel One SmartAPI.
        
        Ref: https://smartapi.angelbroking.com/docs/Introduction
        """
        try:
            if not self.api_key or not self.client_code or not self.pin:
                return {
                    "success": False,
                    "message": "Missing credentials. Please provide API Key, Client Code, and PIN.",
                }

            client = self._get_client()
            
            # Generate TOTP if secret is provided
            totp = ""
            if self.totp_secret and self.totp_secret.strip():
                try:
                    # Clean spaces from TOTP secret
                    secret_cleaned = self.totp_secret.replace(" ", "").upper()
                    totp_obj = pyotp.TOTP(secret_cleaned)
                    totp = totp_obj.now()
                except Exception as e:
                    logger.warning("Failed to generate TOTP: %s", e)

            payload = {
                "clientcode": self.client_code.strip().upper(),
                "password": self.pin.strip(),
                "totp": totp,
            }
            local_ip = _get_local_ip()
            mac_addr = _get_mac_address()
            headers = {
                "X-ClientPublicIP": local_ip,
                "X-ClientLocalIP": local_ip,
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-MACAddress": mac_addr,
                "X-PrivateKey": self.api_key.strip(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            
            response = await client.post(_LOGIN_URL, json=payload, headers=headers)
            
            try:
                data = response.json()
            except Exception:
                data = {}

            if response.status_code == 200 and data.get("status") is True:
                d = data.get("data", {})
                self.jwt_token = d.get("jwtToken", "")
                self.refresh_token = d.get("refreshToken", "")
                self.feed_token = d.get("feedToken", "")
                self._authenticated = True

                expires_at = _next_midnight_ist_epoch()
                self.token_manager.store_token(
                    broker_name="angel_one",
                    access_token=self.jwt_token,
                    refresh_token=self.refresh_token,
                    ttl=max(60, int(expires_at - time.time())),
                    extra={"feed_token": self.feed_token},
                )

                logger.info("Angel One authentication successful for client %s", self.client_code)
                return {
                    "success": True,
                    "jwt_token": self.jwt_token,
                    "feed_token": self.feed_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": expires_at,
                }
            else:
                err_msg = data.get("message", "Authentication failed")
                err_code = data.get("errorcode", "")
                full_msg = f"{err_msg} ({err_code})" if err_code else err_msg
                logger.error("Angel One auth failed: %s", full_msg)
                return {"success": False, "message": full_msg}

        except httpx.HTTPStatusError as e:
            err_msg = f"HTTP error {e.response.status_code}"
            try:
                err_json = e.response.json()
                if "message" in err_json:
                    err_msg = err_json["message"]
            except Exception:
                pass
            logger.error("Angel One auth HTTP error: %s", err_msg)
            return {"success": False, "message": err_msg}
        except httpx.RequestError as e:
            logger.error("Angel One connection error: %s", e)
            return {"success": False, "message": f"Connection error: {str(e)}"}
        except Exception as e:
            logger.error("Angel One auth unexpected error: %s", e)
            return {"success": False, "message": str(e)}

    def apply_session(self, jwt_token: str, feed_token: str = "", refresh_token: str = "") -> None:
        """Hot-apply a freshly logged-in session (from the one-click
        Re-login button) without recreating the broker instance."""
        self.jwt_token = jwt_token
        if feed_token:
            self.feed_token = feed_token
        if refresh_token:
            self.refresh_token = refresh_token
        self._authenticated = bool(jwt_token)
        self.token_manager.store_token(
            broker_name="angel_one",
            access_token=jwt_token,
            refresh_token=self.refresh_token,
            ttl=max(60, int(_next_midnight_ist_epoch() - time.time())),
            extra={"feed_token": self.feed_token},
        )

    async def _refresh_if_needed(self) -> bool:
        if self.token_manager.is_expired("angel_one"):
            if self.refresh_token:
                try:
                    client = self._get_client()
                    payload = {
                        "refreshToken": self.refresh_token,
                    }
                    headers = self._auth_headers()
                    headers["Authorization"] = f"Bearer {self.refresh_token}"
                    response = await client.post(_REFRESH_URL, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    if data.get("status") is True:
                        self.jwt_token = data["data"].get("jwtToken", "")
                        self.feed_token = data["data"].get("feedToken", "")
                        self.token_manager.store_token(
                            broker_name="angel_one",
                            access_token=self.jwt_token,
                            refresh_token=self.refresh_token,
                            extra={"feed_token": self.feed_token},
                        )
                        return True
                except Exception as e:
                    logger.error("Token refresh failed: %s", e)
                    raise TokenExpiredError(broker="angel_one") from e
            else:
                raise TokenExpiredError(broker="angel_one")
        return False

    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        await self._refresh_if_needed()
        token = _TOKEN_MAP.get(symbol.upper(), "")
        if token and self._authenticated and self.jwt_token:
            try:
                client = self._get_client()
                mode = "" if exchange == "NSE" else exchange
                payload = {
                    "mode": mode,
                    "exchangeTokens": {exchange: [token]},
                }
                response = await client.post(
                    _QUOTE_URL, json=payload, headers=self._auth_headers()
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") is True and data.get("data"):
                        quote_data = data["data"]
                        if isinstance(quote_data, list) and quote_data:
                            return float(quote_data[0].get("ltp", 0.0))
                        elif isinstance(quote_data, dict):
                            fetched = quote_data.get("fetched", [])
                            if fetched and isinstance(fetched, list):
                                return float(fetched[0].get("ltp", 0.0))
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Angel One quote fetch failed for %s: %s", symbol, e)

        # Real-time fallback to market data feed / Yahoo Finance
        try:
            from feeds.feed_manager import FeedManager
            feed = FeedManager()
            price = await feed.get_latest_price(symbol)
            if price and price > 0:
                return float(price)
        except Exception:
            pass
        return 0.0

    async def get_margin(self) -> Dict[str, float]:
        await self._refresh_if_needed()
        try:
            client = self._get_client()
            response = await client.get(_MARGIN_URL, headers=self._auth_headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is True:
                rms = data.get("data", {})
                return {
                    "total": float(rms.get("net", 0)),
                    "available": float(rms.get("availablecash", 0)),
                    "used": float(rms.get("utilisedmargin", 0)),
                }
            return {"total": 0.0, "available": 0.0, "used": 0.0}
        except Exception as e:
            logger.error("Failed to get margin: %s", e)
            return {"total": 0.0, "available": 0.0, "used": 0.0}

    async def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float,
        order_type: str = "MARKET",
        product: str = "MIS",
        segment: str = "EQ",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # **kwargs absorbs engine-level order metadata (stop_loss=, target=,
        # direction=...) so callers never hit a TypeError; brokers that do not
        # need them simply ignore the extras (PaperBroker stores them).
        await self._refresh_if_needed()
        try:
            client = self._get_client()
            variety = "NORMAL"
            product_code = product
            order_type_code = "MARKET" if order_type.upper() == "MARKET" else "LIMIT"
            transaction_type_code = "BUY" if transaction_type.upper() == "BUY" else "SELL"

            # Map product to Angel One codes
            ao_product_map = {"MIS": "MIS", "CNC": "CNC", "NRML": "NRML"}
            product_code = ao_product_map.get(product, "MIS")

            # Angel One tradingsymbols are bare ("RELIANCE") — strip any
            # feed-style "NSE:"/"NFO:" prefix the caller may carry.
            ao_symbol = str(symbol).upper()
            if ":" in ao_symbol:
                ao_symbol = ao_symbol.split(":", 1)[1]
            if ao_symbol.endswith("-EQ"):
                ao_symbol = ao_symbol[:-3]

            token = _TOKEN_MAP.get(ao_symbol, "")
            if not token:
                # Never send an empty symboltoken — pre-reject honestly.
                return {
                    "success": False,
                    "order_id": None,
                    "message": (
                        f"Angel One: no symbol token configured for '{ao_symbol}' — order NOT sent. "
                        "Add the token to brokers/angel_one.py _TOKEN_MAP."
                    ),
                }

            payload = {
                "variety": variety,
                "tradingsymbol": ao_symbol,
                "symboltoken": token,
                "transactiontype": transaction_type_code,
                "exchange": exchange,
                "ordertype": order_type_code,
                "producttype": product_code,
                "duration": "DAY",
                "price": str(price) if order_type.upper() == "LIMIT" else "0",
                "squareoff": "0",
                "stoploss": "0",
                "quantity": str(quantity),
            }

            response = await client.post(
                _ORDER_URL, json=payload, headers=self._auth_headers()
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") is True:
                order_id = data.get("data", {}).get("orderid", "")
                return {
                    "success": True,
                    "order_id": order_id,
                    "message": f"Order placed: {transaction_type_code} {quantity} {symbol}",
                }
            else:
                return {
                    "success": False,
                    "order_id": None,
                    "message": data.get("message", "Order placement failed"),
                }
        except Exception as e:
            logger.error("Failed to place order for %s: %s", symbol, e)
            return {"success": False, "order_id": None, "message": str(e)}

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        await self._refresh_if_needed()
        try:
            client = self._get_client()
            payload = {
                "variety": "NORMAL",
                "orderid": order_id,
            }
            response = await client.post(
                _CANCEL_URL, json=payload, headers=self._auth_headers()
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") is True:
                return {"success": True, "message": f"Order {order_id} cancelled"}
            return {"success": False, "message": data.get("message", "Cancel failed")}
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return {"success": False, "message": str(e)}

    async def get_positions(self) -> List[Dict[str, Any]]:
        await self._refresh_if_needed()
        try:
            client = self._get_client()
            response = await client.get(
                _POSITIONS_URL, headers=self._auth_headers()
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") is True:
                positions = []
                for item in data.get("data", []):
                    positions.append({
                        "symbol": item.get("tradingsymbol", ""),
                        "exchange": item.get("exchange", ""),
                        "quantity": int(item.get("netqty", 0)),
                        "avg_price": float(item.get("avgprc", 0)),
                        "pnl": float(item.get("pnl", 0)),
                        "side": "LONG" if int(item.get("netqty", 0)) > 0 else "SHORT",
                    })
                return positions
            return []
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            return []

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        await self._refresh_if_needed()
        try:
            client = self._get_client()
            payload = {
                "variety": "NORMAL",
                "orderid": order_id,
            }
            response = await client.post(
                _ORDER_STATUS_URL, json=payload, headers=self._auth_headers()
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") is True:
                order_data = data.get("data", {})
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": order_data.get("status", "UNKNOWN"),
                    "filled_qty": int(order_data.get("filledquantity", 0)),
                    "filled_price": float(order_data.get("avgprc", 0)),
                    "symbol": order_data.get("tradingsymbol", ""),
                    "transaction_type": order_data.get("transactiontype", ""),
                }
            return {"success": False, "message": data.get("message", "Failed to get order status")}
        except Exception as e:
            logger.error("Failed to get order status for %s: %s", order_id, e)
            return {"success": False, "message": str(e)}

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def get_name(self) -> str:
        return "angel_one"
