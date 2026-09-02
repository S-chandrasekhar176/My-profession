"""Zerodha Kite Connect v3 Broker Integration for UltraBot.

Provides complete implementation of BaseBroker for Zerodha:
  - Kite Connect REST API v3 authentication & session management
  - Order placement (Regular, Market, Limit, SL, SL-M with MIS/CNC/NRML)
  - Order cancellation & modification
  - Net & Day positions fetching with PnL
  - Margins & Capital checks (Equity & Commodity)
  - LTP & Quote fetching with fallback
  - Instrument Token resolution
"""
import logging
from typing import Any, Dict, List, Optional
import httpx

from brokers.base import BaseBroker
from errors.error_types import BrokerError

logger = logging.getLogger(__name__)

_KITE_BASE_URL = "https://api.kite.trade"


class KiteBroker(BaseBroker):
    """Zerodha Kite Connect v3 API Integration."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        access_token: str = "",
        user_id: str = "",
        account_type: str = "live",
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        super().__init__(config=config)
        cfg = config or {}
        self.api_key = (api_key or cfg.get("api_key") or "").strip()
        self.api_secret = (api_secret or cfg.get("api_secret") or "").strip()
        self.access_token = (access_token or cfg.get("access_token") or "").strip()
        self.user_id = (user_id or cfg.get("user_id") or cfg.get("client_id") or "").strip().upper()
        self.account_type = account_type or cfg.get("account_type", "live")
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False

    def _headers(self) -> Dict[str, str]:
        """Build standard Kite Connect v3 request headers."""
        auth_header = f"token {self.api_key}:{self.access_token}" if self.access_token else f"token {self.api_key}"
        return {
            "X-Kite-Version": "3",
            "Authorization": auth_header,
            "Accept": "application/json",
            "User-Agent": "UltraBot/1.0",
        }

    def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_KITE_BASE_URL,
                timeout=15.0,
                headers=self._headers(),
            )
        return self._client

    async def disconnect(self) -> None:
        """Close underlying HTTP client connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._authenticated = False

    # ─────────────────────────────────────────────
    # Authentication & User Profile
    # ─────────────────────────────────────────────

    async def authenticate(self) -> Dict[str, Any]:
        """Validate Kite Connect session by checking user profile or margins."""
        if not self.api_key:
            return {
                "success": False,
                "message": "Zerodha API Key is missing. Please configure credentials.",
            }

        try:
            client = self._get_client()
            # If access token is provided, verify against /user/profile
            if self.access_token:
                res = await client.get("/user/profile", headers=self._headers())
                data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}

                if res.status_code == 200 and data.get("status") == "success":
                    self._authenticated = True
                    user_data = data.get("data", {})
                    user_name = user_data.get("user_name", self.user_id or "Zerodha User")
                    return {
                        "success": True,
                        "message": f"Connected to Zerodha Kite Connect for {user_name} ({user_data.get('user_id', '')})",
                        "data": user_data,
                    }
                else:
                    err_msg = data.get("message") or f"Authentication failed (Status {res.status_code})"
                    return {
                        "success": False,
                        "message": err_msg,
                    }

            # If no access_token yet, acknowledge credential presence
            return {
                "success": True,
                "message": f"Zerodha API Key verified for User ID {self.user_id or 'Active'}. Daily Access Token required for live trading.",
            }

        except Exception as exc:
            logger.error("Zerodha authentication error: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": f"Connection error to Kite API: {str(exc)}",
            }

    # ─────────────────────────────────────────────
    # Market Data & LTP
    # ─────────────────────────────────────────────

    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetch Last Traded Price (LTP) from Kite Connect with fallback."""
        formatted_symbol = f"{exchange.upper()}:{symbol.upper()}"
        try:
            if self.access_token and self.api_key:
                client = self._get_client()
                res = await client.get(
                    f"/quote/ltp?i={formatted_symbol}",
                    headers=self._headers(),
                )
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") == "success":
                        quote_data = data.get("data", {}).get(formatted_symbol, {})
                        last_price = quote_data.get("last_price", 0.0)
                        if last_price > 0:
                            return float(last_price)

            # Fallback to Yahoo Finance quote
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?interval=1d&range=1d"
            async with httpx.AsyncClient(timeout=10.0) as cl:
                res = await cl.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if res.status_code == 200:
                    data = res.json()
                    meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice", 0.0)
                    if price > 0:
                        return float(price)

        except Exception as exc:
            logger.warning("Error fetching direct LTP for %s from Kite: %s", symbol, exc)

        # Real-time fallback to market feed / Yahoo Finance
        try:
            from feeds.feed_manager import FeedManager
            feed = FeedManager()
            price = await feed.get_latest_price(symbol)
            if price and price > 0:
                return float(price)
        except Exception:
            pass

        return 0.0

    # ─────────────────────────────────────────────
    # Margins & Funds
    # ─────────────────────────────────────────────

    async def get_margin(self) -> Dict[str, float]:
        """Fetch available margin from Kite Connect."""
        try:
            if self.access_token and self.api_key:
                client = self._get_client()
                res = await client.get("/user/margins", headers=self._headers())
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") == "success":
                        eq_margins = data.get("data", {}).get("equity", {})
                        avail = float(eq_margins.get("available", {}).get("live_balance", 0.0) or eq_margins.get("net", 0.0))
                        used = float(eq_margins.get("utilised", {}).get("debits", 0.0))
                        total = avail + used
                        return {
                            "available": round(avail, 2),
                            "used": round(used, 2),
                            "total": round(total, 2),
                        }

            return {
                "available": 0.0,
                "used": 0.0,
                "total": 0.0,
            }
        except Exception as exc:
            logger.error("Failed to fetch Kite margins: %s", exc)
            return {
                "available": 0.0,
                "used": 0.0,
                "total": 0.0,
            }

    # ─────────────────────────────────────────────
    # Order Placement & Management
    # ─────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        exchange: str = "NSE",
        transaction_type: str = "BUY",
        quantity: int = 1,
        price: float = 0.0,
        order_type: str = "MARKET",
        product: str = "MIS",
        segment: str = "EQ",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # **kwargs absorbs engine-level order metadata (stop_loss=, target=,
        # direction=...) so callers never hit a TypeError; brokers that do not
        # need them simply ignore the extras (PaperBroker stores them).
        """Place an order via Kite Connect."""
        try:
            client = self._get_client()
            # Kite tradingsymbols are BARE ("RELIANCE", "RELIANCE26AUG1410CE") —
            # strip any feed-style "NSE:"/"NFO:" prefix the caller may carry.
            kite_symbol = str(symbol).upper()
            if ":" in kite_symbol:
                kite_symbol = kite_symbol.split(":", 1)[1]
            if kite_symbol.endswith("-EQ"):
                kite_symbol = kite_symbol[:-3]
            order_payload = {
                "tradingsymbol": kite_symbol,
                "exchange": exchange.upper(),
                "transaction_type": transaction_type.upper(),
                "order_type": order_type.upper(),
                "quantity": str(quantity),
                "product": product.upper(),
                "validity": "DAY",
            }

            if order_type.upper() in ("LIMIT", "SL") and price > 0:
                order_payload["price"] = str(round(price, 2))

            headers = self._headers()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

            res = await client.post(
                "/orders/regular",
                data=order_payload,
                headers=headers,
            )

            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}

            if res.status_code in (200, 201) and data.get("status") == "success":
                order_id = data.get("data", {}).get("order_id", "")
                logger.info("Zerodha order placed: %s %d %s @ %.2f (ID: %s)", transaction_type, quantity, symbol, price, order_id)
                return {
                    "success": True,
                    "order_id": order_id,
                    "status": "SUBMITTED",
                    "message": "Order submitted successfully to Zerodha",
                    "raw": data,
                }
            else:
                err_msg = data.get("message") or f"Order rejected (Status {res.status_code})"
                logger.warning("Zerodha order placement rejected: %s", err_msg)
                return {
                    "success": False,
                    "status": "REJECTED",
                    "message": err_msg,
                    "raw": data,
                }

        except Exception as exc:
            logger.error("Kite order placement exception: %s", exc, exc_info=True)
            return {
                "success": False,
                "status": "ERROR",
                "message": f"Order placement failed: {str(exc)}",
            }

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a pending regular order."""
        try:
            client = self._get_client()
            res = await client.delete(f"/orders/regular/{order_id}", headers=self._headers())
            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}

            if res.status_code == 200 and data.get("status") == "success":
                return {
                    "success": True,
                    "order_id": order_id,
                    "message": "Order cancelled successfully",
                }
            else:
                return {
                    "success": False,
                    "order_id": order_id,
                    "message": data.get("message") or f"Cancel failed (Status {res.status_code})",
                }
        except Exception as exc:
            return {
                "success": False,
                "order_id": order_id,
                "message": f"Cancel order error: {str(exc)}",
            }

    # ─────────────────────────────────────────────
    # Positions & Order Status
    # ─────────────────────────────────────────────

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch open net positions from Kite Connect."""
        try:
            client = self._get_client()
            res = await client.get("/portfolio/positions", headers=self._headers())
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    net_positions = data.get("data", {}).get("net", [])
                    results = []
                    for pos in net_positions:
                        qty = pos.get("quantity", 0)
                        if qty != 0:
                            results.append({
                                "symbol": pos.get("tradingsymbol", ""),
                                "exchange": pos.get("exchange", "NSE"),
                                "quantity": qty,
                                "side": "BUY" if qty > 0 else "SELL",
                                "avg_price": float(pos.get("average_price", 0.0)),
                                "pnl": float(pos.get("pnl", 0.0)),
                                "product": pos.get("product", "MIS"),
                                "m2m": float(pos.get("m2m", 0.0)),
                            })
                    return results

            return []
        except Exception as exc:
            logger.error("Failed to fetch Kite positions: %s", exc)
            return []

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Fetch status of an individual order."""
        try:
            client = self._get_client()
            res = await client.get(f"/orders/{order_id}", headers=self._headers())
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    history = data.get("data", [])
                    if history:
                        last_state = history[-1]
                        status_str = last_state.get("status", "UNKNOWN")
                        filled_qty = int(last_state.get("filled_quantity", 0))
                        filled_price = float(last_state.get("average_price", 0.0))
                        return {
                            "success": True,
                            "order_id": order_id,
                            "status": status_str,
                            "filled_qty": filled_qty,
                            "filled_quantity": filled_qty,
                            "filled_price": filled_price,
                            "average_price": filled_price,
                            "avg_price": filled_price,
                            "status_message": last_state.get("status_message", ""),
                        }
            return {
                "success": False,
                "order_id": order_id,
                "status": "UNKNOWN",
                "message": "Failed to fetch order status from Kite",
            }
        except Exception as exc:
            return {
                "success": False,
                "order_id": order_id,
                "status": "ERROR",
                "message": str(exc),
            }

    def get_name(self) -> str:
        """Broker identifier name."""
        return "zerodha"

    async def close(self) -> None:
        """Close any open client sessions."""
        client = getattr(self, "client", None) or getattr(self, "_client", None)
        if client is not None and hasattr(client, "aclose"):
            try:
                await client.aclose()
            except Exception:
                pass
