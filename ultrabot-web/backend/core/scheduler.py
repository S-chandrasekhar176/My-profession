"""Market Lifecycle Scheduler for UltraBot.

Orchestrates all Indian market lifecycle events using APScheduler / asyncio cron:
  - 08:45 AM IST: Pre-market initialization & daily counters reset
  - 09:15 AM IST: Market open & scan loop activation
  - 15:15 PM IST: Auto-squareoff warning alert (10 mins to EOD)
  - 15:20 PM IST: Auto-squareoff execution for all MIS positions
  - 15:30 PM IST: Market close & DailySummary persistence
  - 15:35 IST: EOD PDF render + archive + Telegram document push (v0.4.8 P2)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date, time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scanner.watchlist_builder import WatchlistBuilder

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")



class MarketLifecycleScheduler:
    """Automates Indian stock market (NSE) daily lifecycle routines."""

    def __init__(self, engine: Any, repository_getter: Callable):
        self.engine = engine
        self._get_repo = repository_getter
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self._is_running = False

    def start(self) -> None:
        """Register all daily cron triggers and start scheduler."""
        if self._is_running:
            return

        # 1. Pre-market initialization: 08:45 AM Mon-Fri
        self.scheduler.add_job(
            self.on_pre_market_init,
            CronTrigger(hour=8, minute=45, day_of_week="mon-fri", timezone=IST),
            id="pre_market_init",
            replace_existing=True,
        )

        # 2. Market Open: 09:15 AM Mon-Fri
        self.scheduler.add_job(
            self.on_market_open,
            CronTrigger(hour=9, minute=15, day_of_week="mon-fri", timezone=IST),
            id="market_open",
            replace_existing=True,
        )

        # 2b. Midday Watchlist Refresh: 12:30 PM Mon-Fri (P2-d)
        self.scheduler.add_job(
            self.on_midday_watchlist_refresh,
            CronTrigger(hour=12, minute=30, day_of_week="mon-fri", timezone=IST),
            id="midday_watchlist_refresh",
            replace_existing=True,
        )

        # 3. Squareoff Warning: 15:15 PM Mon-Fri
        self.scheduler.add_job(
            self.on_squareoff_warning,
            CronTrigger(hour=15, minute=15, day_of_week="mon-fri", timezone=IST),
            id="squareoff_warning",
            replace_existing=True,
        )

        # 4. Auto-Squareoff Execution: 15:20 PM Mon-Fri
        self.scheduler.add_job(
            self.on_auto_squareoff,
            CronTrigger(hour=15, minute=20, day_of_week="mon-fri", timezone=IST),
            id="auto_squareoff",
            replace_existing=True,
        )

        # 5. Market Close & Daily Summary: 15:30 PM Mon-Fri
        self.scheduler.add_job(
            self.on_market_close,
            CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=IST),
            id="market_close",
            replace_existing=True,
        )

        # 6. EOD PDF report: 15:35 IST Mon-Fri (v0.4.8 P2 — delivery Option A:
        # auto-generate the daily PDF, archive it, push it to Telegram)
        self.scheduler.add_job(
            self.on_eod_pdf,
            CronTrigger(hour=15, minute=35, day_of_week="mon-fri", timezone=IST),
            id="eod_pdf_report",
            replace_existing=True,
        )

        self.scheduler.start()
        self._is_running = True
        logger.info("MarketLifecycleScheduler started with 6 scheduled lifecycle jobs (IST)")

    def stop(self) -> None:
        """Stop scheduler."""
        if self._is_running:
            self.scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("MarketLifecycleScheduler stopped")

    async def run_startup_catchup(self) -> bool:
        """Run pre-market init at backend boot if the 08:45 cron was missed today.

        APScheduler cron triggers never backfill missed runs: if the backend
        boots at, say, 10:30 AM on a trading day, the 08:45 job next fires
        TOMORROW and today's Top-10 watchlist would never be generated (the
        engine would then scan a stale/empty watchlist all day). This catch-up
        detects that situation at boot and runs the same pre-market
        initialization immediately.

        Runs only when ALL of the following hold:
          - today is a trading day (weekday, not an NSE holiday)
          - current IST time is in [08:45, 15:30) — before 08:45 the cron
            will fire on its own; after market close it is pointless
          - no trading session AND no closed trade exists yet today — either
            means the bot was already up earlier today (init already ran),
            and a mid-day restart must NOT wipe the morning's watchlist or
            daily-risk state

        Returns True if the catch-up ran.
        """
        try:
            now = datetime.now(IST)
            if not self._is_trading_day():
                logger.info("[startup catch-up] Skipped: not a trading day (weekend/NSE holiday).")
                return False
            if now.time() < time(8, 45):
                logger.info("[startup catch-up] Skipped: before 08:45 IST — the scheduled cron will run pre-market init.")
                return False
            if now.time() >= time(15, 30):
                logger.info("[startup catch-up] Skipped: market already closed for the day.")
                return False

            # Fresh-day detection: any session or closed trade today means
            # today is already in progress — do not rebuild the watchlist or
            # reset daily risk counters mid-day.
            sessions_today: list = []
            trades_today: list = []
            repo = await self._get_repo()
            try:
                if repo is not None:
                    if hasattr(repo, "get_sessions_by_date"):
                        sessions_today = await repo.get_sessions_by_date(now.date().isoformat())
                    if hasattr(repo, "get_todays_closed_trades"):
                        trades_today = await repo.get_todays_closed_trades()
            finally:
                if repo is not None and hasattr(repo, "close"):
                    try:
                        res = repo.close()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

            if sessions_today or trades_today:
                logger.info(
                    "[startup catch-up] Skipped: today already in progress "
                    "(%d session(s), %d closed trade(s)) — preserving existing watchlist and daily-risk state.",
                    len(sessions_today), len(trades_today),
                )
                return False

            logger.info(
                "[startup catch-up] Late start detected at %s IST (past 08:45, no sessions/trades yet today) "
                "— the 08:45 pre-market cron was missed; running pre-market initialization now.",
                now.strftime("%H:%M:%S"),
            )
            await self.on_pre_market_init(force=True)
            return True
        except Exception as exc:
            logger.error("Startup catch-up failed: %s", exc, exc_info=True)
            return False

    def _is_trading_day(self) -> bool:
        """Check if today is a regular NSE trading day."""
        today = datetime.now(IST).date()
        if today.weekday() >= 5:
            return False
        mh = getattr(self.engine, "market_hours", None)
        if mh and hasattr(mh, "is_market_holiday"):
            try:
                res = mh.is_market_holiday(today)
                if isinstance(res, bool):
                    return not res
            except Exception:
                pass
        return True

    # ─────────────────────────────────────────────
    # Lifecycle Handlers
    # ─────────────────────────────────────────────

    async def on_pre_market_init(self, force: bool = False) -> None:
        """08:45 AM: Reset daily risk counters, calibrate market parameters,
        and automatically generate and persist the daily Top-10 Watchlist."""
        if not force and not self._is_trading_day():
            logger.info("[08:45 AM IST] Skipping Pre-Market Init: today is an NSE market holiday or weekend.")
            return
        logger.info("[08:45 AM IST] Running Pre-Market Initialization...")
        try:
            # 1. Reset daily risk counters
            daily_mgr = getattr(self.engine, "daily_risk", None) or getattr(self.engine, "daily_risk_manager", None)
            if daily_mgr and hasattr(daily_mgr, "reset_daily"):
                daily_mgr.reset_daily()
                logger.info("Daily risk counters reset for new trading session.")

            # 1b. Re-login pre-flight: verify the active broker's daily session
            # BEFORE the market opens, so an expired/missing token surfaces as
            # an actionable alert instead of mid-session rejected orders.
            try:
                from brokers.relogin import preflight_session_check

                active_broker = (getattr(self.engine, "broker_name", "") or "paper").lower()
                repo = await self._get_repo()
                try:
                    preflight = await preflight_session_check(active_broker, repo)
                finally:
                    if repo is not None and hasattr(repo, "close"):
                        try:
                            res = repo.close()
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            pass

                if preflight.get("level") in ("warning", "critical"):
                    level = preflight["level"].upper()
                    logger.warning(
                        "[08:45 AM IST] Re-login pre-flight %s: %s",
                        level, preflight.get("message"),
                    )
                    if hasattr(self.engine, "_route_alert"):
                        await self.engine._route_alert("risk_warning", {
                            "type": "relogin_preflight",
                            "level": preflight["level"],
                            "broker": preflight.get("broker"),
                            "message": preflight.get("message"),
                            "timestamp": datetime.now(IST).isoformat(),
                        })
                    if hasattr(self.engine, "_broadcast"):
                        await self.engine._broadcast("market", {
                            "type": "relogin_preflight_warning",
                            "timestamp": datetime.now(IST).isoformat(),
                            "level": preflight["level"],
                            "broker": preflight.get("broker"),
                            "message": preflight.get("message"),
                        })
                else:
                    logger.info(
                        "[08:45 AM IST] Re-login pre-flight OK: %s",
                        preflight.get("message"),
                    )
            except Exception as pf_exc:
                logger.error("Re-login pre-flight check failed: %s", pf_exc, exc_info=True)

            # 2. Automated Pre-market Watchlist Generation (config top_n)
            try:
                await self._build_and_persist_watchlist(source_label="08:45 AM IST")
            except Exception as wl_exc:
                logger.error("Failed to auto-generate pre-market watchlist: %s", wl_exc, exc_info=True)

            if hasattr(self.engine, "_broadcast"):
                await self.engine._broadcast("market", {
                    "type": "pre_market_initialized",
                    "timestamp": datetime.now(IST).isoformat(),
                    "message": "Daily risk limits reset & Daily Watchlist prepared for 09:15 AM market open.",
                })
        except Exception as exc:
            logger.error("Pre-market initialization error: %s", exc, exc_info=True)

    def _watchlist_top_n(self) -> int:
        """Configured watchlist size (P2-d: 20 — doubled opportunity surface).
        Reads config.watchlist.final_top_n with a sane floor."""
        cfg = getattr(self.engine, "config", None)
        try:
            wl_cfg = cfg.get_watchlist_config() if cfg is not None and hasattr(cfg, "get_watchlist_config") else {}
        except Exception:
            wl_cfg = {}
        if not isinstance(wl_cfg, dict):
            wl_cfg = {}
        try:
            top_n = int(wl_cfg.get("final_top_n", 20))
        except (TypeError, ValueError):
            top_n = 20
        return max(5, min(50, top_n))

    async def _build_and_persist_watchlist(self, source_label: str = "scheduler") -> list:
        """Rank the 51-symbol F&O universe and persist the top-N watchlist.

        Shared by the 08:45 pre-market job and the 12:30 midday refresh
        (P2-d): the morning build sets the day's list; the midday rebuild
        swaps in afternoon movers while positions inflight keep their
        open-position skip guard (the engine never scans symbols it already
        holds). Returns the persisted items.
        """
        feed = getattr(self.engine, "feed", None)
        regime = getattr(self.engine, "current_regime", "Sideways") or "Sideways"

        news_items = []
        news_engine = getattr(self.engine, "news_engine", None)
        if news_engine and hasattr(news_engine, "get_recent_news"):
            try:
                news_items = news_engine.get_recent_news(limit=20)
            except Exception:
                pass

        top_n = self._watchlist_top_n()
        builder = WatchlistBuilder()
        top_items = await builder.build_daily_watchlist(
            feed=feed,
            news_items=news_items,
            regime=regime,
            final_top_n=top_n,
        )

        # Persist to Watchlist DB table
        persisted_symbols = []
        repo = await self._get_repo()
        try:
            active_items = await repo.get_active_watchlist()
            for old_item in active_items:
                await repo.update_watchlist_item(old_item.id, is_active=False)

            for item in top_items:
                sym = item["symbol"]
                existing = await repo.get_watchlist_item_by_symbol(sym)
                if existing:
                    await repo.update_watchlist_item(
                        existing.id,
                        name=item.get("name", existing.name),
                        sector=item.get("sector", existing.sector),
                        lot_size=item.get("lot_size", existing.lot_size),
                        is_fno=item.get("is_fno", True),
                        is_active=True,
                        extra=item,
                    )
                else:
                    await repo.add_watchlist_item(
                        symbol=sym,
                        name=item.get("name", sym),
                        sector=item.get("sector", "Unknown"),
                        lot_size=item.get("lot_size", 1),
                        is_fno=item.get("is_fno", True),
                        is_active=True,
                        extra=item,
                    )
                persisted_symbols.append(sym)
        finally:
            if repo is not None and hasattr(repo, "close"):
                try:
                    res = repo.close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        logger.info(
            "[%s] Auto-generated Top %d Daily Watchlist (Regime=%s): %s",
            source_label, top_n, regime, persisted_symbols,
        )

        if hasattr(self.engine, "_broadcast"):
            await self.engine._broadcast("watchlist", {
                "type": "daily_watchlist_updated",
                "timestamp": datetime.now(IST).isoformat(),
                "regime": regime,
                "count": len(top_items),
                "symbols": persisted_symbols,
                "items": top_items,
            })

        return top_items

    async def on_midday_watchlist_refresh(self) -> None:
        """12:30 PM IST: midday watchlist refresh (P2-d).

        Re-ranks the F&O universe so afternoon movers enter the scan list
        even when they were quiet at 08:45. Open positions are unaffected —
        the engine skips symbols it already holds, so a rebuild can never
        orphan an open position's management.
        """
        if not self._is_trading_day():
            logger.info("[12:30 PM IST] Skipping midday watchlist refresh: not a trading day.")
            return
        logger.info("[12:30 PM IST] Midday watchlist refresh — re-ranking universe for afternoon movers...")
        try:
            items = await self._build_and_persist_watchlist(source_label="12:30 PM IST")
            if hasattr(self.engine, "_route_alert"):
                await self.engine._route_alert("engine_status", {
                    "state": "watchlist_refreshed",
                    "count": len(items),
                    "message": f"Midday watchlist refresh: {len(items)} symbols re-ranked for the afternoon session.",
                })
        except Exception as exc:
            logger.error("Midday watchlist refresh failed: %s", exc, exc_info=True)


    async def on_market_open(self) -> None:
        """09:15 AM: NSE Market Open event."""
        if not self._is_trading_day():
            logger.info("[09:15 AM IST] Skipping Market Open: today is an NSE market holiday or weekend.")
            return
        logger.info("[09:15 AM IST] Market Open - Activating live strategy scanning...")
        try:
            await self.engine._broadcast("market", {
                "type": "market_opened",
                "timestamp": datetime.now(IST).isoformat(),
                "message": "NSE regular trading hours commenced (09:15 - 15:30 IST).",
            })
        except Exception as exc:
            logger.error("Market open notification error: %s", exc)

    async def on_squareoff_warning(self) -> None:
        """15:15 PM: Squareoff Warning Alert (10 mins to EOD auto-squareoff)."""
        if not self._is_trading_day():
            return
        logger.warning("[15:15 PM IST] Intraday auto-squareoff warning (5 minutes remaining).")
        try:
            await self.engine._broadcast("risk_event", {
                "type": "squareoff_warning",
                "timestamp": datetime.now(IST).isoformat(),
                "message": "Intraday (MIS) positions will be auto-squared off at 15:20 IST.",
            })
            if hasattr(self.engine, "_route_alert"):
                await self.engine._route_alert("risk_event", {
                    "message": "Intraday (MIS) positions will be auto-squared off at 15:20 IST (5 minutes remaining).",
                    "rule": "AUTO_SQUAREOFF_WARNING",
                })
        except Exception as exc:
            logger.error("Squareoff warning error: %s", exc)

    async def on_auto_squareoff(self) -> None:
        """15:20 PM: Force close all open intraday positions."""
        if not self._is_trading_day():
            return
        logger.warning("[15:20 PM IST] Executing Intraday Auto-Squareoff for all open positions...")
        try:
            open_positions = []
            repo = await self._get_repo()
            try:
                open_positions = await repo.get_open_positions()
            finally:
                if repo is not None and hasattr(repo, "close"):
                    try:
                        res = repo.close()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

            for pos in open_positions:
                try:
                    # Fetch fresh LTP from feed or broker immediately before calculating square-off P&L
                    fresh_price = None
                    if self.engine and self.engine.feed and hasattr(self.engine.feed, "get_latest_price"):
                        try:
                            fresh_price = await self.engine.feed.get_latest_price(pos.symbol)
                        except Exception as feed_err:
                            logger.warning("Could not fetch fresh feed price for %s auto-squareoff: %s", pos.symbol, feed_err)

                    if not fresh_price and self.engine and self.engine.broker and hasattr(self.engine.broker, "get_latest_price"):
                        try:
                            fresh_price = await self.engine.broker.get_latest_price(pos.symbol)
                        except Exception as broker_err:
                            logger.warning("Could not fetch fresh broker price for %s auto-squareoff: %s", pos.symbol, broker_err)

                    current_price = float(fresh_price) if fresh_price and fresh_price > 0 else (pos.current_price or pos.entry_price)
                    # v0.4.4 (audit round 2): positions carry BUY/SELL from the
                    # strategies — the raw ``pos.direction == "LONG"`` comparison
                    # here silently INVERTED the square-off P&L for every BUY
                    # position (a +₹50 gain was passed to _close_position as −₹50,
                    # feeding the daily-risk circuit breaker a fake loss).
                    # Normalize through the shared helper; _close_position also
                    # recomputes from the fill (defense in depth).
                    from utils.direction import is_long_direction
                    if is_long_direction(pos.direction):
                        pnl_amount = (current_price - pos.entry_price) * pos.quantity
                    else:
                        pnl_amount = (pos.entry_price - current_price) * pos.quantity
                    _sq_cost_basis = (pos.entry_price or 0) * (pos.quantity or 0)
                    pnl_pct = (pnl_amount / _sq_cost_basis) * 100 if _sq_cost_basis > 0 else 0.0

                    await self.engine._close_position(
                        position=pos,
                        exit_price=current_price,
                        close_reason="auto_squareoff",
                        pnl_amount=pnl_amount,
                        pnl_pct=pnl_pct,
                    )
                    logger.info("Auto-squared off position %s (%s) @ INR %.2f", pos.id, pos.symbol, current_price)
                except Exception as pos_err:
                    logger.error("Failed to auto-squareoff position %s: %s", pos.id, pos_err)

            await self.engine._broadcast("market", {
                "type": "auto_squareoff_completed",
                "closed_count": len(open_positions),
                "timestamp": datetime.now(IST).isoformat(),
                "message": f"Successfully auto-squared off {len(open_positions)} open intraday positions.",
            })
        except Exception as exc:
            logger.error("Auto squareoff routine error: %s", exc, exc_info=True)

    async def on_market_close(self) -> None:
        """15:30 PM: Market Close & Save Daily Summary to DB."""
        if not self._is_trading_day():
            return
        logger.info("[15:30 PM IST] Market Close - Generating Daily Summary...")
        try:
            today_str = datetime.now(IST).date().isoformat()
            total_trades = 0
            total_net_pnl = 0.0

            repo = await self._get_repo()
            try:
                todays_trades = await repo.get_todays_closed_trades()
                wins = sum(1 for t in todays_trades if t.net_pnl > 0)
                losses = sum(1 for t in todays_trades if t.net_pnl <= 0)
                total_trades = len(todays_trades)
                win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
                total_net_pnl = sum(t.net_pnl for t in todays_trades)

                # v0.4.8 HF-9: surface gross P&L, fees and best/worst in the
                # EOD alert — the previous payload carried ONLY net_pnl, so
                # Telegram showed "Total Fees: Rs 0.00" every single day.
                total_gross_pnl = round(sum(float(t.pnl or 0.0) for t in todays_trades), 2)
                total_fees_paid = round(sum(float(t.fees or 0.0) for t in todays_trades), 2)
                best_trade_pnl = round(max((float(t.net_pnl or 0.0) for t in todays_trades), default=0.0), 2)
                worst_trade_pnl = round(min((float(t.net_pnl or 0.0) for t in todays_trades), default=0.0), 2)

                # Resolve configured starting capital from engine or config
                cap_cfg = self.engine.config.get_capital_config() if (self.engine and hasattr(self.engine, "config") and hasattr(self.engine.config, "get_capital_config")) else {}
                starting_capital = float(
                    (self.engine.initial_capital if self.engine and hasattr(self.engine, "initial_capital") and self.engine.initial_capital is not None else None)
                    or cap_cfg.get("virtual_capital")
                    or 500000.0
                )
                ending_capital = round(starting_capital + total_net_pnl, 2)

                # Persist summary
                await repo.create_daily_summary(
                    date=today_str,
                    total_trades=total_trades,
                    wins=wins,
                    losses=losses,
                    win_rate=win_rate,
                    net_pnl=total_net_pnl,
                    starting_capital=starting_capital,
                    ending_capital=ending_capital,
                    max_drawdown_pct=await repo.get_max_drawdown_pct(),
                    regime=getattr(self.engine, "current_regime", None),
                    vix_close=getattr(self.engine, "vix", None),
                )
            finally:
                if repo is not None and hasattr(repo, "close"):
                    try:
                        res = repo.close()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass
            logger.info("DailySummary saved for %s: %d trades, Net PnL: INR %.2f", today_str, total_trades, total_net_pnl)

            await self.engine._broadcast("market", {
                "type": "market_closed",
                "date": today_str,
                "total_trades": total_trades,
                "net_pnl": total_net_pnl,
                "win_rate": win_rate,
                "timestamp": datetime.now(IST).isoformat(),
            })

            if hasattr(self.engine, "_route_alert"):
                await self.engine._route_alert("eod_report", {
                    "daily_summary": {
                        "date": today_str,
                        "total_trades": total_trades,
                        "wins": wins,
                        "losses": losses,
                        "win_rate": win_rate,
                        "net_pnl": total_net_pnl,
                        "gross_pnl": total_gross_pnl,
                        "total_fees": total_fees_paid,
                        "best_trade": best_trade_pnl,
                        "worst_trade": worst_trade_pnl,
                    },
                    "trades": todays_trades,
                })
        except Exception as exc:
            logger.error("Market close routine error: %s", exc, exc_info=True)

    async def on_eod_pdf(self) -> None:
        """15:35 IST: render the EOD PDF, archive it, push to Telegram (v0.4.8 P2)."""
        if not self._is_trading_day():
            return
        logger.info("[15:35 IST] Generating EOD PDF report...")
        try:
            from notifications.eod_pdf import generate_eod_pdf
            from notifications.eod_report import EODReportGenerator

            repo = await self._get_repo()
            try:
                generator = EODReportGenerator(repo)
                report = await generator.generate(
                    session_id=str(getattr(self.engine, "session_id", "") or "")
                )
            finally:
                if repo is not None and hasattr(repo, "close"):
                    try:
                        res = repo.close()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

            out_dir = Path(__file__).resolve().parent.parent / "reports"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"EOD_{report.get('date', 'unknown')}.pdf"
            generate_eod_pdf(report, str(out_path))
            logger.info("EOD PDF archived: %s", out_path)

            pnl = report.get("pnl_summary", {}) or {}
            caption = (
                f"EOD Report {report.get('date', '')} — Net {float(pnl.get('net_pnl', 0.0)):+.2f} INR, "
                f"{pnl.get('total', 0)} trades (W{pnl.get('wins', 0)}/L{pnl.get('losses', 0)})"
            )
            bot = getattr(getattr(self.engine, "alert_manager", None), "telegram_bot", None)
            if bot is not None:
                sent = await bot.send_document(str(out_path), caption=caption)
                logger.info("EOD PDF Telegram push: %s", "OK" if sent else "SKIPPED/FAILED (archived locally)")
            else:
                logger.info("EOD PDF Telegram push: no bot configured — archived locally only")
        except Exception as exc:
            logger.error("EOD PDF routine error: %s", exc, exc_info=True)
