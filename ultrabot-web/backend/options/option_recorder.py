"""Option Chain Recorder & Snapshot Engine (Phase 2: F&O Data Foundation).

Periodically polls NIFTY & BANKNIFTY option chains from Fyers API (with greeks=1),
validates Greeks against Black-Scholes theoretical benchmarks, and records
tiered snapshots (tradable ATM+-3 strikes and full chain) into SQLite.
Runs in pure read-only ingestion mode with zero trading execution risk.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.market_hours import MarketHours, IST
from options.option_chain import OptionChainFetcher
from options.greeks import GreeksCalculator

logger = logging.getLogger(__name__)


class OptionChainRecorder:
    """Background service for systematic F&O chain ingestion and Greek verification."""

    def __init__(
        self,
        broker: Any,
        repo_getter: Optional[Callable[[], Any]] = None,
        symbols: Optional[List[str]] = None,
        poll_interval_fast: float = 5.0,   # ATM+-3 tradable strikes interval (seconds)
        poll_interval_full: float = 60.0,  # Full chain snapshot interval (seconds)
        market_hours: Optional[MarketHours] = None,
    ):
        self.broker = broker
        self._repo_getter = repo_getter
        self.symbols = symbols or ["NIFTY", "BANKNIFTY"]
        self.poll_interval_fast = poll_interval_fast
        self.poll_interval_full = poll_interval_full
        self.market_hours = market_hours or MarketHours()

        self.fetcher = OptionChainFetcher(broker=broker)
        self.greeks_calc = GreeksCalculator()

        self._running = False
        self._fast_task: Optional[asyncio.Task] = None
        self._full_task: Optional[asyncio.Task] = None
        self._last_full_poll: Dict[str, float] = {}

    def start(self) -> None:
        """Start the background recording workers."""
        if self._running:
            return
        self._running = True
        self._fast_task = asyncio.create_task(self._fast_poll_loop())
        logger.info("OptionChainRecorder started for symbols: %s", self.symbols)

    async def stop(self) -> None:
        """Gracefully stop the background recorder workers."""
        if not self._running:
            return
        self._running = False
        for task in (self._fast_task, self._full_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._fast_task = None
        self._full_task = None
        logger.info("OptionChainRecorder stopped.")

    async def poll_and_record_once(self, symbol: str, full_chain: bool = False) -> Dict[str, Any]:
        """Fetch, verify Greeks, and persist a single option chain snapshot."""
        try:
            strike_count = 25 if full_chain else 8
            parsed_chain = await self.fetcher.fetch_option_chain(
                symbol=symbol,
                strikecount=strike_count,
            )
            if not parsed_chain or not parsed_chain.get("calls"):
                logger.debug("Option chain empty for %s", symbol)
                return {"status": "empty", "symbol": symbol}

            spot = float(parsed_chain.get("spot_price", 0.0) or 0.0)
            atm_strike = float(parsed_chain.get("atm_strike", 0.0) or spot)
            expiry = str(parsed_chain.get("expiry", "") or "")
            expiry_epoch = parsed_chain.get("expiry_epoch")
            pcr = float(parsed_chain.get("pcr", 1.0) or 1.0)
            max_pain = parsed_chain.get("max_pain")
            total_ce_oi = int(parsed_chain.get("total_ce_oi", 0) or 0)
            total_pe_oi = int(parsed_chain.get("total_pe_oi", 0) or 0)

            # Filter tradable strikes (ATM +- 3 strikes)
            all_calls = parsed_chain.get("calls", [])
            all_puts = parsed_chain.get("puts", [])

            strikes = sorted(set([c["strike"] for c in all_calls] + [p["strike"] for p in all_puts]))
            if not full_chain and strikes and atm_strike in strikes:
                atm_idx = strikes.index(atm_strike)
                start_idx = max(0, atm_idx - 3)
                end_idx = min(len(strikes), atm_idx + 4)
                tradable_strikes = set(strikes[start_idx:end_idx])
                calls_to_record = [c for c in all_calls if c["strike"] in tradable_strikes]
                puts_to_record = [p for p in all_puts if p["strike"] in tradable_strikes]
                tier = "tradable"
            else:
                calls_to_record = all_calls
                puts_to_record = all_puts
                tier = "full"

            # Black-Scholes Greeks Verification for ATM Call
            atm_call = next((c for c in all_calls if c["strike"] == atm_strike), None)
            verification = {"valid": True, "details": "No ATM call found"}
            if atm_call and spot > 0:
                tte_years = max(float(atm_call.get("expiry_epoch", 0) - datetime.now(IST).timestamp()) / (365.0 * 86400.0), 0.001) if atm_call.get("expiry_epoch") else 0.02
                iv = float(atm_call.get("iv", 0.0) or 0.15)
                theo_greeks = self.greeks_calc.all_greeks(
                    S=spot,
                    K=atm_strike,
                    T=tte_years,
                    sigma=iv if iv > 0 else 0.15,
                    option_type="CE",
                )
                verification = self.greeks_calc.verify_greeks(
                    broker_greeks=atm_call,
                    theoretical_greeks=theo_greeks,
                    tolerance=0.25,
                )

            # Persist to database if repository available
            snapshot_obj = None
            if self._repo_getter:
                try:
                    getter_res = self._repo_getter()
                    repo = await getter_res if asyncio.iscoroutine(getter_res) else getter_res
                    if repo and hasattr(repo, "create_option_snapshot"):
                        chain_payload = calls_to_record + puts_to_record
                        snapshot_obj = await repo.create_option_snapshot(
                            underlying_symbol=symbol,
                            spot_price=spot,
                            expiry=expiry,
                            expiry_epoch=expiry_epoch,
                            atm_strike=atm_strike,
                            max_pain=max_pain,
                            pcr=pcr,
                            total_ce_oi=total_ce_oi,
                            total_pe_oi=total_pe_oi,
                            tier=tier,
                            chain_data=chain_payload,
                        )
                except Exception as db_err:
                    logger.warning("Failed to persist option snapshot for %s: %s", symbol, db_err)

            return {
                "status": "success",
                "symbol": symbol,
                "spot_price": spot,
                "atm_strike": atm_strike,
                "pcr": pcr,
                "max_pain": max_pain,
                "tier": tier,
                "calls_count": len(calls_to_record),
                "puts_count": len(puts_to_record),
                "greeks_verification": verification,
                "snapshot_id": getattr(snapshot_obj, "id", None) if snapshot_obj else None,
            }

        except Exception as err:
            logger.error("Error polling option chain for %s: %s", symbol, err, exc_info=True)
            return {"status": "error", "symbol": symbol, "error": str(err)}

    async def _fast_poll_loop(self) -> None:
        """Background continuous worker polling tradable strikes and periodic full chain."""
        while self._running:
            try:
                # Outside market hours, sleep longer unless forced for offline testing
                market_open = True
                try:
                    if hasattr(self.market_hours, "is_market_open"):
                        market_open = self.market_hours.is_market_open()
                except Exception:
                    market_open = True

                if not market_open:
                    await asyncio.sleep(30.0)
                    continue

                now = datetime.now(IST).timestamp()
                for sym in self.symbols:
                    last_full = self._last_full_poll.get(sym, 0.0)
                    is_full = (now - last_full) >= self.poll_interval_full
                    res = await self.poll_and_record_once(sym, full_chain=is_full)
                    if is_full and res.get("status") == "success":
                        self._last_full_poll[sym] = now

                await asyncio.sleep(self.poll_interval_fast)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in option recorder loop: %s", e)
                await asyncio.sleep(5.0)
