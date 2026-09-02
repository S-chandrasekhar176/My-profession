"""Shoonya (Finvasia Noren) broker integration.

Wire format per official Shoonya API documentation
(https://shoonya.com/api-documentation) and the Noren OMS REST contract:

* Every request is a POST whose body is form-encoded with two fields:
    - ``jData``: a URL-encoded JSON object holding the endpoint parameters
    - ``jKey`` : the session token (``susertoken``) returned by Login —
      required on every endpoint except Login itself.
* Content-Type is ``text/plain`` (the gateway URL-decodes ``jData`` itself).
* Responses are JSON with ``stat`` == ``Ok`` on success and ``emsg`` on failure.

Authentication (daily re-login):
    POST /NorenWClientTP/Login
    jData = {
        "apkversion": "1.0*",
        "uid": <user id>,
        "pwd": sha256(password),
        "factor2": <TOTP code>,          # 2FA
        "vc": <vendor code>,
        "appkey": sha256(uid + "|" + api_key),
        "imei": <device identifier>,
    }
    -> susertoken (used as jKey), actid, exarr, prarr ...

Sessions expire daily (early morning); `_refresh_if_needed` re-runs the
TOTP login automatically when the stored token has expired.
"""

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional
import pyotp

import httpx

from brokers.base import BaseBroker
from brokers.token_manager import TokenManager
from errors.error_types import BrokerError, ConnectionLostError, TokenExpiredError

logger = logging.getLogger(__name__)

# Shoonya Noren REST API endpoints (classic trading gateway — the one the
# official NorenRestApiPy SDK uses; the redesigned docs' /NorenWClientAPI/
# paths accept the same jData/jKey contract).
_BASE_URL = "https://api.shoonya.com/NorenWClientTP"
_LOGIN_URL = f"{_BASE_URL}/Login"
_QUICK_AUTH_URL = f"{_BASE_URL}/QuickAuth"
_LOGOUT_URL = f"{_BASE_URL}/Logout"
_USER_DETAILS_URL = f"{_BASE_URL}/UserDetails"
_QUOTE_URL = f"{_BASE_URL}/GetQuotes"
_MARGIN_URL = f"{_BASE_URL}/Limits"
_ORDER_URL = f"{_BASE_URL}/PlaceOrder"
_CANCEL_URL = f"{_BASE_URL}/CancelOrder"
_ORDER_STATUS_URL = f"{_BASE_URL}/OrderBook"
_POSITIONS_URL = f"{_BASE_URL}/PositionBook"
_HOLDINGS_URL = f"{_BASE_URL}/Holdings"

# Standard NSE symbol token mapping for Shoonya (Noren API)
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

# Noren sessions are killed in the early morning (broker-side), well before
# the next market open. We conservatively treat a token as expired 18 hours
# after issue and always before the next 08:00 IST.
_SESSION_TTL_SECONDS = 18 * 3600


class ShoonyaBroker(BaseBroker):
    """Shoonya (Noren) broker integration.

    Uses httpx for HTTP calls to Shoonya's Noren REST API.
    Requires user_id, password, vendor_code, app_key, and TOTP secret for
    authentication (all available from trade.shoonya.com → Profile → API key).
    """

    def __init__(
        self,
        user_id: str = "",
        password: str = "",
        vendor_code: str = "",
        app_key: str = "",
        totp_secret: str = "",
        factor2_pin: str = "",
        token_manager: Optional[TokenManager] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        self.user_id = user_id or self.config.get("user_id", "")
        self.password = password or self.config.get("password", "")
        self.vendor_code = vendor_code or self.config.get("vendor_code", "")
        self.app_key = app_key or self.config.get("app_key", "")
        self.totp_secret = totp_secret or self.config.get("totp_secret", "")
        self.factor2_pin = factor2_pin or self.config.get("factor2_pin", "")
        self.token_manager = token_manager or TokenManager()
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False
        self._session_token: str = ""

    # ────────────────────────────────────────────────────────────
    # Noren wire format helpers
    # ────────────────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=30.0,
                # Noren gateway expects text/plain; jData carries url-encoded JSON
                headers={"Content-Type": "text/plain", "Accept": "application/json"},
            )
        return self._client

    async def _noren_post(
        self,
        url: str,
        payload: Dict[str, Any],
        with_session: bool = True,
    ) -> Dict[str, Any]:
        """POST a Noren request: jData=<json> (+ jKey=<susertoken>)."""
        client = self._get_client()
        form: Dict[str, str] = {"jData": json.dumps(payload)}
        if with_session and self._session_token:
            form["jKey"] = self._session_token
        response = await client.post(url, data=form)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise BrokerError(broker="shoonya", message="Malformed Noren response (non-dict)")
        if data.get("stat") == "Not_Ok":
            emsg = str(data.get("emsg", "Unknown Noren error"))
            # Session problems must surface as TokenExpiredError so callers
            # can re-login instead of hammering a dead session.
            if "session" in emsg.lower() or "invalid session key" in emsg.lower():
                self._authenticated = False
                raise TokenExpiredError(broker="shoonya", message=emsg)
            raise BrokerError(broker="shoonya", message=emsg)
        return data

    @staticmethod
    def _sha256(data: str) -> str:
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _totp_now(self) -> str:
        """Current 2FA code: TOTP secret if configured, else the static pin."""
        if self.totp_secret and self.totp_secret.strip():
            try:
                cleaned = self.totp_secret.replace(" ", "").upper()
                return pyotp.TOTP(cleaned).now()
            except Exception as e:
                logger.warning("Failed to generate TOTP for Shoonya: %s", e)
        return self.factor2_pin.strip()

    # ────────────────────────────────────────────────────────────
    # Authentication (daily re-login)
    # ────────────────────────────────────────────────────────────

    async def authenticate(self) -> Dict[str, Any]:
        """TOTP login per Noren contract: POST /Login with jData payload.

        Returns {success, message, susertoken?, actid?, expires_at?}.
        """
        if not (self.user_id and self.password):
            return {"success": False, "message": "Shoonya user_id and password are required."}
        try:
            payload = {
                "apkversion": "1.0*",
                "uid": self.user_id,
                "pwd": self._sha256(self.password),
                "factor2": self._totp_now(),
                "vc": self.vendor_code or self.user_id,
                "appkey": self._sha256(f"{self.user_id}|{self.app_key}") if self.app_key else "",
                "imei": "ultrabot",
            }
            data = await self._noren_post(_LOGIN_URL, payload, with_session=False)

            if data.get("stat") == "Ok" and data.get("susertoken"):
                self._session_token = str(data.get("susertoken"))
                self._authenticated = True
                expires_at = time.time() + _SESSION_TTL_SECONDS
                actid = str(data.get("actid", self.user_id))

                self.token_manager.store_token(
                    broker_name="shoonya",
                    access_token=self._session_token,
                    refresh_token="",
                    ttl=_SESSION_TTL_SECONDS,
                    extra={"actid": actid, "exarr": data.get("exarr", [])},
                )
                logger.info("Shoonya login successful for user %s", self.user_id)
                return {
                    "success": True,
                    "message": "Authenticated with Shoonya",
                    "susertoken": self._session_token,
                    "actid": actid,
                    "expires_at": expires_at,
                }
            # stat Ok but no susertoken should not happen; treat as failure.
            return {"success": False, "message": data.get("emsg", "Login failed (no session token returned)")}

        except TokenExpiredError:
            # Login endpoint cannot raise this meaningfully; be safe anyway.
            return {"success": False, "message": "Session expired during login — retry."}
        except httpx.HTTPStatusError as e:
            logger.error("Shoonya auth HTTP error: %s", e)
            return {"success": False, "message": f"HTTP error {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error("Shoonya connection error: %s", e)
            return {"success": False, "message": f"Connection error: {str(e)}"}
        except BrokerError as e:
            logger.error("Shoonya auth failed: %s", e)
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error("Shoonya auth unexpected error: %s", e)
            return {"success": False, "message": str(e)}

    def apply_session_token(self, susertoken: str, actid: str = "") -> None:
        """Hot-apply a freshly obtained session token (e.g. via the
        one-click Re-login button) without recreating the broker instance."""
        self._session_token = susertoken
        self._authenticated = bool(susertoken)
        self.token_manager.store_token(
            broker_name="shoonya",
            access_token=susertoken,
            refresh_token="",
            ttl=_SESSION_TTL_SECONDS,
            extra={"actid": actid or self.user_id},
        )

    async def logout(self) -> Dict[str, Any]:
        """End the Noren session server-side."""
        if not self._session_token:
            return {"success": True, "message": "No active session"}
        try:
            await self._noren_post(_LOGOUT_URL, {"uid": self.user_id})
            self.token_manager.remove_token("shoonya")
            self._session_token = ""
            self._authenticated = False
            return {"success": True, "message": "Logged out from Shoonya"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def _refresh_if_needed(self) -> bool:
        """Re-run TOTP login when the session token has expired."""
        if self.token_manager.is_expired("shoonya"):
            result = await self.authenticate()
            return result.get("success", False)
        return True

    # ────────────────────────────────────────────────────────────
    # Trading / account APIs
    # ────────────────────────────────────────────────────────────

    async def get_user_details(self) -> Dict[str, Any]:
        """POST /UserDetails — exchanges enabled, products, account id."""
        return await self._noren_post(_USER_DETAILS_URL, {"uid": self.user_id})

    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        await self._refresh_if_needed()
        token_id = _TOKEN_MAP.get(symbol.upper())
        if token_id and self._authenticated and self._session_token:
            try:
                data = await self._noren_post(
                    _QUOTE_URL,
                    {"uid": self.user_id, "exch": exchange, "token": token_id},
                )
                lp_str = data.get("lp", "0")
                if lp_str and float(lp_str) > 0:
                    return float(lp_str)
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Failed to get Shoonya direct LTP for %s: %s", symbol, e)

        # Real-time fallback to market feed / Yahoo
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
        """POST /Limits — margin per the documented formula:
        available = (cash + payin + payout + daycash + unclearedcash
                     + brkcollamt + collateral + aux_brkcollamt) - marginused
        """
        await self._refresh_if_needed()
        try:
            data = await self._noren_post(_MARGIN_URL, {"uid": self.user_id, "actid": self.user_id})

            def _num(key: str) -> float:
                try:
                    return float(data.get(key, 0) or 0)
                except (TypeError, ValueError):
                    return 0.0

            total_credits = (
                _num("cash") + _num("payin") + _num("payout") + _num("daycash")
                + _num("unclearedcash") + _num("brkcollamt") + _num("collateral")
                + _num("aux_brkcollamt")
            )
            used = _num("marginused")
            return {
                "total": total_credits,
                "available": total_credits - used,
                "used": used,
            }
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
            tx_type = "B" if transaction_type.upper() == "BUY" else "S"
            prctyp = "MKT" if order_type.upper() == "MARKET" else "LMT"

            # Shoonya product codes (C=CNC delivery, I=Intraday/MIS, M=NRML)
            shoonya_product = {"MIS": "I", "INTRADAY": "I", "CNC": "C", "NRML": "M"}
            prod_code = shoonya_product.get(product.upper(), "I")

            payload = {
                "uid": self.user_id,
                "actid": self.user_id,
                "exch": exchange,
                "tsym": symbol,
                "qty": str(quantity),
                "prc": "0" if order_type.upper() == "MARKET" else str(price),
                "prctyp": prctyp,
                "ret": "DAY",
                "trantype": tx_type,
                "prd": prod_code,
            }

            data = await self._noren_post(_ORDER_URL, payload)

            if data.get("stat") == "Ok":
                order_id = data.get("norenordno", "")
                return {
                    "success": True,
                    "order_id": order_id,
                    "message": f"Order placed: {transaction_type} {quantity} {symbol}",
                }
            return {
                "success": False,
                "order_id": None,
                "message": data.get("emsg", "Order placement failed"),
            }
        except BrokerError as e:
            return {"success": False, "order_id": None, "message": str(e)}
        except Exception as e:
            logger.error("Failed to place order for %s: %s", symbol, e)
            return {"success": False, "order_id": None, "message": str(e)}

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        await self._refresh_if_needed()
        try:
            data = await self._noren_post(
                _CANCEL_URL, {"uid": self.user_id, "norenordno": order_id}
            )
            if data.get("stat") == "Ok":
                return {"success": True, "message": f"Order {order_id} cancelled"}
            return {"success": False, "message": data.get("emsg", "Cancel failed")}
        except BrokerError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return {"success": False, "message": str(e)}

    async def get_positions(self) -> List[Dict[str, Any]]:
        await self._refresh_if_needed()
        try:
            data = await self._noren_post(
                _POSITIONS_URL, {"uid": self.user_id, "actid": self.user_id}
            )
            if data.get("stat") == "Ok":
                positions = []
                for item in data.get("poslist", []) or []:
                    try:
                        net_qty = int(item.get("netqty", "0") or 0)
                    except (TypeError, ValueError):
                        continue
                    if net_qty == 0:
                        continue
                    positions.append({
                        "symbol": item.get("tsym", ""),
                        "exchange": item.get("exch", ""),
                        "quantity": abs(net_qty),
                        "avg_price": float(item.get("avgprc", 0) or 0),
                        "pnl": float(item.get("pnl", 0) or 0),
                        "side": "LONG" if net_qty > 0 else "SHORT",
                    })
                return positions
            return []
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            return []

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        await self._refresh_if_needed()
        try:
            data = await self._noren_post(
                _ORDER_STATUS_URL, {"uid": self.user_id, "norenordno": order_id}
            )
            if data.get("stat") == "Ok":
                orders = data.get("ordlist", []) or []
                if orders:
                    order = orders[0]
                    return {
                        "success": True,
                        "order_id": order_id,
                        "status": order.get("status", "UNKNOWN"),
                        "filled_qty": int(order.get("fillshares", "0") or 0),
                        "filled_price": float(order.get("avgprc", "0") or 0),
                        "symbol": order.get("tsym", ""),
                        "transaction_type": "BUY" if order.get("trantype", "") == "B" else "SELL",
                    }
            return {"success": False, "message": "Order not found"}
        except Exception as e:
            logger.error("Failed to get order status for %s: %s", order_id, e)
            return {"success": False, "message": str(e)}

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def get_name(self) -> str:
        return "shoonya"
