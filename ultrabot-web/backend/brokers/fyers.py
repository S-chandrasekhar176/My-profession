"""Fyers API v3 broker integration.

Built on Fyers' own officially maintained `fyers-apiv3` SDK
(https://pypi.org/project/fyers-apiv3/) rather than hand-rolled HTTP calls,
so authentication, endpoint paths, and payload formats stay correct as
Fyers evolves their API.

Docs: https://myapi.fyers.in/docsv3
Regulatory notice (mandatory from 1 Apr 2026):
    https://myapi.fyers.in/mandatory-regulatory-changes

IMPORTANT — daily re-authentication:
Per Fyers' SEBI-aligned regulatory changes, a token generated today is NOT
valid indefinitely and a fresh login (with 2FA) is required once per
trading day. This module intentionally does NOT attempt any silent/
automatic token refresh — that would defeat the daily-2FA requirement.
Re-authentication must go through `FyersBroker.build_auth_url()` +
`FyersBroker.exchange_auth_code()`, which is what the
`/api/brokers/fyers/authorize` and `/api/brokers/fyers/callback` routes
call, driven by a human clicking "Connect / Re-authenticate" in Settings.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)

try:
    from fyers_apiv3 import fyersModel
    _FYERS_SDK_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as exc:
    fyersModel = None
    _FYERS_SDK_AVAILABLE = False
    logger.warning("fyers_apiv3 SDK not available or missing dependencies: %s", exc)


def _require_fyers_sdk() -> None:
    """v0.4.8 (hotfix #4): raise an actionable error when the Fyers SDK is
    missing, instead of letting callers crash with a cryptic
    "'NoneType' object has no attribute 'SessionModel'" AttributeError.

    The SDK must be installed SEPARATELY with --no-deps (it pins
    aiohttp==3.9.x which conflicts with this project's aiohttp>=3.10):
        pip install --no-deps -r requirements-fyers.txt
    See requirements-fyers.txt header and start.sh.
    """
    if not _FYERS_SDK_AVAILABLE or fyersModel is None:
        raise RuntimeError(
            "Fyers SDK (fyers-apiv3) is not installed. Install it separately "
            "to avoid dependency conflicts: pip install --no-deps -r requirements-fyers.txt "
            "(start.sh does this automatically)."
        )

from brokers.base import BaseBroker
from core.rate_limiter import RateLimiter, RateLimitExceeded



# Fyers documented rate limits (per API key), see the regulatory notice above.
# Transactional = orders/cancels. Non-transactional = quotes/history/margin/etc.
#
# HOTFIX #5 (live 2026-09-01): Fyers returned 429 "request limit reached" for 2/51
# history calls during the 08:45 watchlist build even though our client admitted
# exactly 10 req/s (the documented cap). Conclusion: Fyers' server-side window is
# stricter than the documented 10/s (fixed-window/burst intolerance). We now run
# both limiters at 8 req/s — 20% headroom below the documented cap — so bursts
# degrade gracefully instead of tripping 429s. Per-minute caps unchanged (200).
_transactional_limiter = RateLimiter(per_second=8, per_minute=200, per_day=10_000, name="fyers-transactional")
_data_limiter = RateLimiter(per_second=8, per_minute=200, per_day=100_000, name="fyers-data")


class FyersBroker(BaseBroker):
    """Fyers API v3 broker integration.

    Requires app_id (client_id), access_token (obtained via the OAuth flow
    below), and secret_key (only needed for the token exchange itself, not
    for day-to-day calls).
    """

    def __init__(
        self,
        app_id: str = "",
        access_token: str = "",
        secret_key: str = "",
        pin: str = "",
        redirect_uri: str = "",
        account_type: str = "live",
        client_id: str = "",
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config)
        self.app_id = app_id or client_id or self.config.get("app_id", self.config.get("client_id", ""))
        self.access_token = access_token or self.config.get("access_token", "")
        self.secret_key = secret_key or self.config.get("secret_key", "")
        self.pin = pin or self.config.get("pin", "")
        self.redirect_uri = redirect_uri or self.config.get("redirect_uri", "")
        self.account_type = account_type
        self._client: Optional[Any] = None
        self._authenticated = False

    def _get_client(self) -> Any:
        if not _FYERS_SDK_AVAILABLE or fyersModel is None:
            raise RuntimeError("fyers-apiv3 SDK is not installed or has missing dependencies (e.g. aws-lambda-powertools).")
        if self._client is None:
            self._client = fyersModel.FyersModel(
                client_id=self.app_id,
                token=self.access_token,
                is_async=False,
                log_path="",
            )
        return self._client


    # ────────────────────────────────────────────────────────────
    # OAuth: daily login flow (used by the Settings "Connect" button)
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def build_auth_url(app_id: str, redirect_uri: str, state: str = "ultrabot") -> str:
        """Build the Fyers login URL. Opening this in a browser and completing
        login + 2FA is the only supported way to (re)authenticate — there is
        no fully silent path, by Fyers' own design.
        """
        _require_fyers_sdk()  # v0.4.8 (hotfix #4): actionable error, not NoneType crash
        session = fyersModel.SessionModel(
            client_id=app_id,
            redirect_uri=redirect_uri,
            response_type="code",
            state=state,
        )
        return session.generate_authcode()

    @staticmethod
    async def exchange_auth_code(
        app_id: str, secret_key: str, redirect_uri: str, auth_code: str
    ) -> Dict[str, Any]:
        """Exchange a one-time auth_code (from the callback redirect) for an
        access_token. The SDK call is synchronous (uses `requests`), so it
        runs in a worker thread to avoid blocking the event loop.
        """

        def _do_exchange() -> Dict[str, Any]:
            _require_fyers_sdk()  # v0.4.8 (hotfix #4)
            session = fyersModel.SessionModel(
                client_id=app_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="authorization_code",
            )
            session.set_token(auth_code)
            return session.generate_token()

        try:
            result = await asyncio.to_thread(_do_exchange)
            if result.get("s") == "ok" and result.get("access_token"):
                return {
                    "success": True,
                    "access_token": result["access_token"],
                    "message": "Fyers access token generated successfully",
                }
            return {
                "success": False,
                "message": f"Fyers token exchange failed: {result}",
            }
        except Exception as exc:
            logger.error("Fyers auth code exchange error: %s", exc, exc_info=True)
            return {"success": False, "message": f"Fyers token exchange error: {str(exc)}"}

    # ────────────────────────────────────────────────────────────
    # Rate-limited SDK call wrapper
    # ────────────────────────────────────────────────────────────

    async def _call(self, limiter: RateLimiter, fn: Callable, *args, **kwargs) -> Any:
        """Run a synchronous SDK method off the event loop, gated by the
        given rate limiter."""
        try:
            await limiter.acquire()
        except RateLimitExceeded as exc:
            logger.warning("Fyers rate limit hit: %s", exc)
            return {"s": "error", "message": str(exc)}
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ────────────────────────────────────────────────────────────
    # BaseBroker interface
    # ────────────────────────────────────────────────────────────

    async def authenticate(self) -> Dict[str, Any]:
        """Validate Fyers credentials by checking the profile endpoint."""
        try:
            client = self._get_client()
            data = await self._call(_data_limiter, client.get_profile)
            if isinstance(data, dict) and data.get("s") == "ok":
                self._authenticated = True
                return {
                    "success": True,
                    "message": "Fyers v3 authentication successful",
                    "data": data,
                }
            return {
                "success": False,
                "message": f"Fyers authentication failed: {data}",
            }
        except Exception as exc:
            logger.error("Fyers authentication error: %s", exc, exc_info=True)
            return {"success": False, "message": f"Fyers connection error: {str(exc)}"}

    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetch latest LTP via the Fyers quotes endpoint."""
        try:
            fyers_sym = f"{exchange}:{symbol}-EQ"
            client = self._get_client()
            data = await self._call(_data_limiter, client.quotes, {"symbols": fyers_sym})
            if isinstance(data, dict) and data.get("s") == "ok" and data.get("d"):
                val = data["d"][0].get("v", {}).get("lp", 0.0)
                if val:
                    return float(val)
            return 0.0
        except Exception as exc:
            logger.warning("Failed to fetch Fyers LTP for %s: %s", symbol, exc)
            return 0.0

    async def get_candles(
        self,
        symbol: str,
        exchange: str = "NSE",
        resolution: str = "5",
        range_from: str = "",
        range_to: str = "",
    ) -> List[Dict[str, Any]]:
        """Fetch historical OHLCV candles via the Fyers history endpoint.

        resolution: candle size in minutes as a string ('1','5','15','60','D').
        range_from/range_to: 'YYYY-MM-DD'. If omitted, caller should supply
        a sane default window before calling.

        v0.4.8 (hotfix #2): symbol formatting is now idempotent. Callers may
        pass either a raw engine symbol ("SBIN") or an already-formatted
        Fyers identifier ("NSE:SBIN-EQ", "NSE:NIFTY50-INDEX" — as produced by
        feeds.fyers_candles.to_fyers_symbol). Previously a pre-formatted
        symbol was reformatted into "NSE:NSE:SBIN-EQ-EQ", which Fyers rejects
        with -300 "Invalid symbol provided" — silently degrading the live
        FyersCandleFeed AND the backtest primary history source to Yahoo.
        """
        try:
            raw_sym = (symbol or "").strip().upper()
            if ":" in raw_sym:
                fyers_sym = raw_sym  # already a Fyers-style identifier — use as-is
            else:
                fyers_sym = f"{exchange}:{raw_sym}-EQ"
            client = self._get_client()
            payload = {
                "symbol": fyers_sym,
                "resolution": resolution,
                "date_format": "1",
                "range_from": range_from,
                "range_to": range_to,
                "cont_flag": "1",
            }
            data = await self._call(_data_limiter, client.history, payload)
            if isinstance(data, dict) and data.get("s") == "ok":
                candles = []
                for c in data.get("candles", []):
                    # Fyers format: [epoch, open, high, low, close, volume]
                    candles.append({
                        "timestamp": c[0],
                        "open": c[1],
                        "high": c[2],
                        "low": c[3],
                        "close": c[4],
                        "volume": c[5],
                    })
                return candles
            logger.warning("Fyers history call returned no data for %s: %s", symbol, data)
            return []
        except Exception as exc:
            logger.warning("Failed to fetch Fyers candles for %s: %s", symbol, exc)
            return []

    async def get_option_chain(
        self,
        symbol: str,
        exchange: str = "NSE",
        strike_count: int = 10,
        timestamp: str = "",
    ) -> Dict[str, Any]:
        """Fetch option chain from Fyers API v3."""
        try:
            client = self._get_client()
            sym_upper = symbol.upper().replace(" ", "").replace("_", "")
            if sym_upper in ("NIFTY", "NIFTY50", "NIFTY-50"):
                fyers_symbol = "NSE:NIFTY50-INDEX"
            elif sym_upper in ("BANKNIFTY", "NIFTYBANK"):
                fyers_symbol = "NSE:NIFTYBANK-INDEX"
            elif sym_upper in ("FINNIFTY", "NIFTYFINSERVICE"):
                fyers_symbol = "NSE:FINNIFTY-INDEX"
            elif ":" in symbol:
                fyers_symbol = symbol
            else:
                fyers_symbol = f"{exchange}:{symbol}-EQ" if "-EQ" not in symbol else symbol

            payload = {
                "symbol": fyers_symbol,
                "strikecount": strike_count,
                "timestamp": timestamp,
            }
            data = await self._call(_data_limiter, client.optionchain, payload)
            if isinstance(data, dict) and data.get("s") == "ok":
                return data
            logger.warning("Fyers optionchain returned non-ok: %s", data)
            return data if isinstance(data, dict) else {"s": "error", "message": str(data)}
        except Exception as exc:
            logger.error("Failed to fetch Fyers option chain for %s: %s", symbol, exc, exc_info=True)
            return {"s": "error", "message": str(exc)}

    async def get_margin(self) -> Dict[str, float]:
        """Get available margin/funds from Fyers."""
        try:
            client = self._get_client()
            data = await self._call(_data_limiter, client.funds)
            if isinstance(data, dict) and data.get("s") == "ok":
                fund_limit = data.get("fund_limit", [])
                avail = 0.0
                used = 0.0
                for f in fund_limit:
                    if f.get("title") == "Total Balance":
                        avail = float(f.get("equityAmount", 0.0))
                    elif f.get("title") == "Utilized Amount":
                        used = float(f.get("equityAmount", 0.0))
                return {"available": avail, "used": used, "total": avail + used}
        except Exception as exc:
            logger.warning("Failed to fetch Fyers margin: %s", exc)
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
        """Place order on Fyers API v3."""
        try:
            client = self._get_client()
            fyers_type = 2 if order_type == "MARKET" else 1  # 1=Limit, 2=Market
            fyers_side = 1 if transaction_type.upper() == "BUY" else -1
            fyers_product = "INTRADAY" if product in ("MIS", "INTRADAY") else "CNC"
            # Fyers symbols are fully-qualified ("NSE:RELIANCE-EQ", "NSE:RELIANCE26AUG1410CE").
            # Option symbols from the chain ALREADY carry the "NSE:" prefix —
            # re-prefixing produced "NSE:NSE:RELIANCE26AUG1410CE" (rejected by Fyers).
            raw_symbol = str(symbol).upper()
            if ":" in raw_symbol:
                fyers_symbol = raw_symbol
            elif segment.upper() in ("FNO", "FUT", "OPT", "FUTURES", "OPTIONS"):
                fyers_symbol = f"{exchange}:{raw_symbol}"
            else:
                fyers_symbol = f"{exchange}:{raw_symbol}-EQ"

            payload = {
                "symbol": fyers_symbol,
                "qty": quantity,
                "type": fyers_type,
                "side": fyers_side,
                "productType": fyers_product,
                "limitPrice": price if fyers_type == 1 else 0,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
            }

            data = await self._call(_transactional_limiter, client.place_order, payload)
            if isinstance(data, dict) and data.get("s") == "ok":
                return {
                    "success": True,
                    "order_id": str(data.get("id", "")),
                    "message": "Order placed successfully on Fyers",
                    "data": data,
                }
            return {
                "success": False,
                "order_id": None,
                "message": f"Fyers order failed: {data}",
            }
        except Exception as exc:
            logger.error("Fyers place_order error: %s", exc, exc_info=True)
            return {"success": False, "order_id": None, "message": f"Fyers place_order error: {str(exc)}"}

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel pending order on Fyers."""
        try:
            client = self._get_client()
            data = await self._call(_transactional_limiter, client.cancel_order, {"id": order_id})
            if isinstance(data, dict) and data.get("s") == "ok":
                return {"success": True, "message": f"Order {order_id} cancelled on Fyers"}
            return {"success": False, "message": f"Fyers cancel failed: {data}"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions from Fyers."""
        try:
            client = self._get_client()
            data = await self._call(_data_limiter, client.positions)
            if isinstance(data, dict) and data.get("s") == "ok":
                res = []
                for p in data.get("netPositions", []):
                    qty = int(p.get("netQty", 0))
                    if qty != 0:
                        res.append({
                            "symbol": p.get("symbol", "UNKNOWN"),
                            "quantity": abs(qty),
                            "avg_price": float(p.get("avgPrice", 0.0)),
                            "pnl": float(p.get("pl", 0.0)),
                            "side": "LONG" if qty > 0 else "SHORT",
                        })
                return res
        except Exception as exc:
            logger.warning("Failed to fetch Fyers positions: %s", exc)
        return []

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get order status from Fyers."""
        try:
            client = self._get_client()
            data = await self._call(_data_limiter, client.orderbook)
            if isinstance(data, dict) and data.get("s") == "ok":
                for o in data.get("orderBook", []):
                    if str(o.get("id")) == str(order_id):
                        status_str = "COMPLETE" if o.get("status") == 2 else "PENDING"
                        filled_qty = int(o.get("filledQty", 0))
                        traded_price = float(o.get("tradedPrice", 0.0))
                        return {
                            "success": True,
                            "order_id": order_id,
                            "status": status_str,
                            "filled_qty": filled_qty,
                            "filled_price": traded_price,
                            "avg_price": traded_price,
                        }
            return {
                "success": False,
                "order_id": order_id,
                "status": "UNKNOWN",
                "message": f"Order {order_id} not found in Fyers orderbook",
            }
        except Exception as exc:
            logger.warning("Failed to fetch Fyers order status: %s", exc)
            return {
                "success": False,
                "order_id": order_id,
                "status": "ERROR",
                "message": str(exc),
            }

    def get_name(self) -> str:
        return "fyers"

    @staticmethod
    def rate_limit_status() -> Dict[str, Any]:
        """Expose current rate-limiter usage, e.g. for a Settings diagnostics panel."""
        return {
            "transactional": _transactional_limiter.status(),
            "data": _data_limiter.status(),
        }

