import logging
import time
from typing import Any, Dict, List, Optional
import httpx
import pyotp

from brokers.base import BaseBroker
from errors.error_types import BrokerError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.dhan.co/v2"
# TOTP-based token generation & renewal (DhanHQ v2 "Authentication" docs).
_AUTH_BASE_URL = "https://auth.dhan.co"
_GENERATE_TOKEN_URL = f"{_AUTH_BASE_URL}/app/generateAccessToken"
_RENEW_TOKEN_URL = f"{_BASE_URL}/RenewToken"
# Dhan access tokens are valid for 24 hours from generation.
_DHAN_TOKEN_TTL_SECONDS = 24 * 3600

# Comprehensive Dhan NSE security IDs mapping
_DHAN_SECURITY_MAP: Dict[str, str] = {
    "HDFCBANK": "1333",
    "RELIANCE": "2885",
    "TCS": "11536",
    "INFY": "1594",
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
    "NIFTY": "26000",
    "BANKNIFTY": "26009",
}


class DhanBroker(BaseBroker):
    """Dhan HQ API v2 broker integration.

    Documentation: https://dhanhq.co/docs/v2/
    Requires client_id and access_token. The access token expires every 24h;
    if a TOTP secret + PIN are stored, `authenticate_with_totp()` performs a
    fully automatic re-login (auth.dhan.co/app/generateAccessToken).
    """

    def __init__(
        self,
        client_id: str = "",
        access_token: str = "",
        pin: str = "",
        totp_secret: str = "",
        account_type: str = "live",
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        self.client_id = client_id or self.config.get("client_id", "")
        self.access_token = access_token or self.config.get("access_token", "")
        self.pin = pin or self.config.get("pin", "")
        self.totp_secret = totp_secret or self.config.get("totp_secret", "")
        self.account_type = account_type
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=20.0,
            )
        return self._client

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "client-id": self.client_id,
            "access-token": self.access_token,
        }

    # ────────────────────────────────────────────────────────────
    # Daily token lifecycle (24h expiry per DhanHQ docs)
    # ────────────────────────────────────────────────────────────

    @staticmethod
    async def authenticate_with_totp(client_id: str, pin: str, totp_secret: str) -> Dict[str, Any]:
        """Generate a fresh 24h access token via auth.dhan.co.

        POST /app/generateAccessToken?dhanClientId=<id>&pin=<pin>&totp=<code>
        (TOTP must be enabled for the account at web.dhan.co).
        Returns {success, access_token?, expiry_time?, message}.
        """
        if not (client_id and pin and totp_secret):
            return {
                "success": False,
                "message": "Dhan Client ID, PIN and TOTP secret are required for automatic re-login.",
            }
        try:
            cleaned = totp_secret.replace(" ", "").upper()
            totp = pyotp.TOTP(cleaned).now()
        except Exception as exc:
            return {"success": False, "message": f"Invalid TOTP secret: {exc}"}

        try:
            async with httpx.AsyncClient(timeout=20.0) as auth_client:
                response = await auth_client.post(
                    _GENERATE_TOKEN_URL,
                    params={"dhanClientId": client_id, "pin": pin, "totp": totp},
                )
            if response.status_code == 200:
                data = response.json()
                token = data.get("accessToken", "")
                if token:
                    return {
                        "success": True,
                        "access_token": token,
                        "expiry_time": data.get("expiryTime", ""),
                        "client_name": data.get("dhanClientName", ""),
                        "message": "Dhan access token generated (valid 24h)",
                    }
                return {"success": False, "message": f"Dhan token response missing accessToken: {data}"}
            return {
                "success": False,
                "message": f"Dhan token generation failed ({response.status_code}): {response.text}",
            }
        except Exception as exc:
            logger.error("Dhan TOTP auth error: %s", exc, exc_info=True)
            return {"success": False, "message": f"Dhan connection error: {exc}"}

    async def renew_token(self) -> Dict[str, Any]:
        """POST /v2/RenewToken — refresh a *web-generated* token for another
        24h. Only works while the current token is still active."""
        if not self.access_token:
            return {"success": False, "message": "No current Dhan access token to renew."}
        try:
            client = self._get_client()
            response = await client.post(
                _RENEW_TOKEN_URL,
                headers={
                    "access-token": self.access_token,
                    "dhanClientId": self.client_id,
                },
            )
            if response.status_code == 200:
                data = response.json()
                token = data.get("accessToken", "")
                if token:
                    self.access_token = token
                    self._authenticated = True
                    return {
                        "success": True,
                        "access_token": token,
                        "expiry_time": data.get("expiryTime", ""),
                        "message": "Dhan token renewed (valid 24h)",
                    }
            return {
                "success": False,
                "message": f"Dhan token renewal failed ({response.status_code}): {response.text}",
            }
        except Exception as exc:
            return {"success": False, "message": f"Dhan token renewal error: {exc}"}

    def apply_session_token(self, access_token: str) -> None:
        """Hot-apply a freshly generated token without recreating the broker."""
        self.access_token = access_token
        self._authenticated = bool(access_token)

    async def authenticate(self) -> Dict[str, Any]:
        """Authenticate / test Dhan credentials by fetching fund limits."""
        try:
            client = self._get_client()
            response = await client.get("/fundlimit", headers=self._headers())
            
            if response.status_code == 200:
                data = response.json()
                self._authenticated = True
                return {
                    "success": True,
                    "message": "Dhan v2 authentication successful",
                    "data": data,
                }
            else:
                return {
                    "success": False,
                    "message": f"Dhan authentication failed with status {response.status_code}: {response.text}",
                }
        except Exception as exc:
            logger.error("Dhan authentication error: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": f"Dhan connection error: {str(exc)}",
            }

    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetch latest real LTP from Dhan or live market feed.

        Dhan /marketfeed/ltp response shape (per docs):
            {"data": {"NSE_EQ": {"11536": {"last_price": 4520}}}, "status": "success"}
        """
        # 1. If authenticated, try Dhan MarketFeed LTP API
        if self._authenticated and self.access_token:
            try:
                sec_id = _DHAN_SECURITY_MAP.get(symbol.upper(), "")
                if sec_id:
                    client = self._get_client()
                    dhan_seg = "NSE_EQ" if exchange == "NSE" else "NSE_FNO"
                    payload = {dhan_seg: [int(sec_id)]}
                    res = await client.post("/marketfeed/ltp", json=payload, headers=self._headers())
                    if res.status_code == 200:
                        data = res.json()
                        seg_map = (data.get("data") or {}).get(dhan_seg) or {}
                        entry = seg_map.get(str(sec_id)) or seg_map.get(sec_id) or {}
                        ltp = entry.get("last_price", 0.0)
                        if ltp and float(ltp) > 0:
                            return float(ltp)
            except Exception as e:
                logger.warning("Dhan direct quote error for %s: %s", symbol, e, exc_info=True)

        # 2. Live FeedManager fallback
        try:
            from feeds.feed_manager import FeedManager
            feed = FeedManager()
            price = await feed.get_latest_price(symbol)
            if price and price > 0:
                return float(price)
        except Exception as exc:
            logger.warning("Failed to fetch real LTP for %s: %s", symbol, exc)

        return 0.0

    async def get_margin(self) -> Dict[str, float]:
        """Get available funds from Dhan."""
        try:
            client = self._get_client()
            response = await client.get("/fundlimit", headers=self._headers())
            if response.status_code == 200:
                data = response.json()
                avail = float(data.get("availMargin", data.get("cashBalance", 0.0)))
                used = float(data.get("utilizedAmount", 0.0))
                return {
                    "available": avail,
                    "used": used,
                    "total": avail + used,
                }
        except Exception as exc:
            logger.warning("Failed to fetch Dhan margin: %s", exc)

        return {"available": 0.0, "used": 0.0, "total": 0.0}

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
        """Place an order via Dhan v2 API."""
        try:
            client = self._get_client()
            
            dhan_segment = "NSE_EQ" if exchange == "NSE" else "NSE_FNO"
            dhan_product = "INTRADAY" if product in ("MIS", "INTRADAY") else "CNC"
            dhan_order_type = "MARKET" if order_type == "MARKET" else "LIMIT"
            security_id = _DHAN_SECURITY_MAP.get(symbol.upper(), "")
            if not security_id:
                logger.error("Dhan order rejected: unmapped security ID for symbol '%s'", symbol)
                return {
                    "success": False,
                    "message": f"Dhan order rejected: unmapped security ID for symbol '{symbol}'. Please configure token.",
                }

            payload = {
                "dhanClientId": self.client_id,
                "transactionType": transaction_type.upper(),
                "exchangeSegment": dhan_segment,
                "productType": dhan_product,
                "orderType": dhan_order_type,
                "validity": "DAY",
                "tradingSymbol": symbol,
                "securityId": str(security_id),
                "quantity": quantity,
                "price": price if dhan_order_type == "LIMIT" else 0,
            }

            response = await client.post("/orders", json=payload, headers=self._headers())
            if response.status_code in (200, 201):
                data = response.json()
                return {
                    "success": True,
                    "order_id": str(data.get("orderId", "DHAN-ORDER-001")),
                    "message": "Order placed successfully on Dhan",
                    "data": data,
                }
            else:
                return {
                    "success": False,
                    "order_id": None,
                    "message": f"Dhan order failed: {response.text}",
                }
        except Exception as exc:
            logger.error("Dhan place_order error: %s", exc, exc_info=True)
            return {
                "success": False,
                "order_id": None,
                "message": f"Dhan place_order error: {str(exc)}",
            }

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order on Dhan."""
        try:
            client = self._get_client()
            response = await client.delete(f"/orders/{order_id}", headers=self._headers())
            if response.status_code in (200, 202):
                return {"success": True, "message": f"Order {order_id} cancelled on Dhan"}
            return {"success": False, "message": f"Cancel failed: {response.text}"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions from Dhan."""
        try:
            client = self._get_client()
            response = await client.get("/positions", headers=self._headers())
            if response.status_code == 200:
                data = response.json()
                positions = []
                for p in (data if isinstance(data, list) else data.get("data", [])):
                    qty = int(p.get("netQty", p.get("positionQty", 0)))
                    if qty != 0:
                        positions.append({
                            "symbol": p.get("tradingSymbol", "UNKNOWN"),
                            "quantity": abs(qty),
                            "avg_price": float(p.get("buyAvg", p.get("costPrice", 0.0))),
                            "pnl": float(p.get("realizedProfit", 0.0) + p.get("unrealizedProfit", 0.0)),
                            "side": "LONG" if qty > 0 else "SHORT",
                        })
                return positions
        except Exception as exc:
            logger.warning("Failed to fetch Dhan positions: %s", exc)
        return []

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get order status from Dhan."""
        try:
            client = self._get_client()
            response = await client.get(f"/orders/{order_id}", headers=self._headers())
            if response.status_code == 200:
                data = response.json()
                status_str = data.get("orderStatus", "COMPLETE")
                filled_qty = int(data.get("filledQty", 0))
                avg_price = float(data.get("price", 0.0))
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": status_str,
                    "filled_qty": filled_qty,
                    "filled_price": avg_price,
                    "avg_price": avg_price,
                }
            return {
                "success": False,
                "order_id": order_id,
                "status": "UNKNOWN",
                "message": f"Dhan order status failed: {response.text}",
            }
        except Exception as exc:
            logger.warning("Failed to get Dhan order status: %s", exc)
            return {
                "success": False,
                "order_id": order_id,
                "status": "ERROR",
                "message": str(exc),
            }

    def get_name(self) -> str:
        return "dhan"

    async def close(self) -> None:
        """Close any open httpx client sessions."""
        client = getattr(self, "client", None) or getattr(self, "_client", None)
        if client is not None and hasattr(client, "aclose"):
            try:
                await client.aclose()
            except Exception:
                pass
