"""UltraBotEngine – main orchestrator for the trading system.

The engine coordinates market hours checks, feed updates, strategy scanning,
risk gating, position sizing, opportunity creation, trade execution,
position management (SL/target/partial bookings), and WebSocket broadcasting.

Strategies are loaded from ``strategies.registry`` when available.  If that
module hasn't been built yet the engine still runs – it simply won't generate
signals.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.engine_state import EngineState, EngineMode
from core.market_hours import MarketHours
from options.option_chain import OptionChainFetcher
from options.strike_selector import StrikeSelector
from options.liquidity_filter import LiquidityFilter
from options.options_risk import OptionsRiskChecker
from options.greeks import GreeksCalculator
from utils.market_utils import get_stock_sector, get_last_candle_age_minutes
from utils.direction import is_long_direction as _is_long_direction
from core.capital_resolver import resolve_total_capital

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# _is_long_direction moved to utils/direction.py (v0.4.4) so scheduler /
# dashboard / partial_booker can share it without importing the engine
# (circular import). It is re-imported under its historical private name so
# every existing call site and test keeps working unchanged.
#
# CORRECTION (live-market validation run 2, 2026-08-28) — kept for history:
# _manage_position, _execute_partial_booking, _close_position and the status
# builder all compared ``direction == "LONG"`` EXACTLY while every real
# position carries "BUY"/"SELL" from the strategies — ALL of them took the
# SHORT branch: P&L sign inverted, SL/target triggers inverted (the first
# real managed trade — ASIANPAINT BUY 13:23 IST — "stopped out" at a price
# ABOVE its SL with a recorded +₹12 gross on a −₹12 fill), trailing SL never
# moved for BUY positions, and exit fees were computed on swapped legs (STT
# is sell-leg only). Test fixtures had used LONG/SHORT, which is why the
# suite never caught it. Every direction branch must go through the helper.


def _estimate_entry_round_trip_fees(
    filled_price: float,
    quantity: int,
    fees_config: Optional[dict] = None,
) -> float:
    """Entry-time fee estimate for a just-filled trade — FULL round trip.

    v0.4.9 wave-4 fee-truth fix: the previous inline formula charged ONE
    ₹20 brokerage leg and ONE leg of turnover fees (invested_amount on the
    buy side only, intraday STT wrongly applied to the buy leg, and GST
    levied on the entire fee stack including STT/stamp). Displayed
    "Estimated Fees" were therefore ~₹38-40 while the true round trip ran
    ~₹61-62 — live evidence: ASIANPAINT (2026-08-28) recorded ₹38.08 entry
    estimate vs ₹61.33 true round trip; the 2026-09-03 NTPC/DELHIVERY
    trades displayed ₹38.4x vs ₹61.61/₹61.74 actual.

    This helper delegates to the canonical NSEFeeCalculator — the exact
    model the close path, G17/G19 and the EOD reconciliation use —
    approximating the exit leg at the fill price until the real exit fill
    is known. The close path still overwrites ``fees`` with the exact
    fill-based round trip, so the estimate only has to be honest, not
    clairvoyant.
    """
    fees_cfg = fees_config or {}
    brokerage = float(fees_cfg.get("brokerage_per_order", 20.0))
    if quantity is None or int(quantity) <= 0:
        return 0.0
    try:
        from fees.nse_fee_calculator import NSEFeeCalculator

        breakdown = NSEFeeCalculator(brokerage_per_order=brokerage).calculate_equity_intraday(
            buy_price=filled_price,
            sell_price=filled_price,  # exit leg approximated at fill price
            quantity=int(quantity),
            brokerage_per_order=brokerage,
        )
        return float(breakdown.get("total", 0.0))
    except Exception:
        # Unreachable in practice (pure in-repo arithmetic). Honest floor:
        # BOTH brokerage legs + GST on them — never the old single-leg lie.
        return round(2.0 * brokerage * 1.18, 2)


# Try to import strategy registry – graceful fallback if not yet built
try:
    from strategies.registry import StrategyRegistry
    _STRATEGIES_AVAILABLE = True
    logger.info("Strategy registry loaded successfully")
except ImportError:
    _STRATEGIES_AVAILABLE = False
    logger.warning("strategies.registry not available – engine will run without signal generation")
    class StrategyRegistry:
        def __init__(self):
            self._strategies = {}
        def get_all(self):
            return self._strategies
        def get(self, name):
            return None
        def discover(self):
            pass


class UltraBotEngine:
    """The brain of UltraBot. Orchestrates all subsystems."""

    def __init__(
        self,
        config,
        repository_getter: Callable,
        error_engine,
        risk_engine,
        position_sizer,
        partial_booker,
        daily_risk_manager,
        broker_factory,
        feed_manager,
        session_manager,
        market_hours: Optional[MarketHours] = None,
        ws_manager=None,
        strategy_registry=None,
        adaptive_manager=None,
        regime_detector=None,
        performance_tracker=None,
        kronos_scanner=None,
        alert_manager=None,
    ):
        self.config = config
        self._repo_getter = repository_getter
        self.error_engine = error_engine
        self.risk_engine = risk_engine
        self.position_sizer = position_sizer
        self.partial_booker = partial_booker
        self.daily_risk = daily_risk_manager
        self.broker_factory = broker_factory
        self.feed_manager = feed_manager
        self.session_manager = session_manager
        self.market_hours = market_hours or MarketHours()
        self.ws_manager = ws_manager
        self.alert_manager = alert_manager
        self.strategy_registry = strategy_registry
        self.adaptive_manager = adaptive_manager
        self.regime_detector = regime_detector
        self.performance_tracker = performance_tracker
        self.kronos_scanner = kronos_scanner

        # Engine state
        self.state = EngineState.STOPPED
        self.mode: Optional[str] = None
        self.broker = None
        self.broker_name: str = "paper"
        self.feed = feed_manager
        self.session_id: Optional[str] = None
        self.initial_capital: Optional[float] = None
        self.pending_opportunities: Dict[str, dict] = {}  # opportunity_id -> opportunity data
        self.invalidated_opportunities: Dict[str, dict] = {}  # opportunity_id -> expired/invalidated data
        self._opportunities_lock: asyncio.Lock = asyncio.Lock()
        self._main_task: Optional[asyncio.Task] = None
        self._start_time: Optional[datetime] = None
        self.current_regime: str = "Sideways"
        self.vix: float = 15.0
        self.vix_updated_at: Optional[datetime] = None
        self.vix_stale_warning_logged: bool = False
        self.vix_critical_stale: bool = False
        risk_cfg = self.config.get_risk_config() if hasattr(self.config, "get_risk_config") else {}
        self.vix_staleness_warning_seconds: int = int(risk_cfg.get("vix_staleness_warning_seconds", 360))
        self.vix_staleness_critical_seconds: int = int(risk_cfg.get("vix_staleness_critical_seconds", 540))
        self.vix_stale_floor: float = float(risk_cfg.get("vix_stale_floor", 22.0))
        # Data-freshness guard (Phase 5): during open market hours, a scanned
        # symbol whose newest candle is older than this many minutes is
        # skipped with DATA_STALE_CANDLES telemetry — delisted/suspended symbols
        # can still serve OLD history through the feed, and running strategies
        # on it would generate phantom signals at stale prices. NOTE: this is a
        # data-hygiene SKIP, deliberately NOT numbered as a risk gate so it can
        # never collide with the real G16_MultiTimeframe trend-alignment gate.
        self.stale_candle_max_age_minutes: float = float(risk_cfg.get("stale_candle_max_age_minutes", 30))
        self._stale_data_symbols_warned: set = set()
        self.nifty_price: float = 0.0
        self.nifty_change: float = 0.0
        self._prev_nifty_close: float = 0.0
        self.banknifty_price: float = 0.0
        # Fallback symbol list used by _scan_watchlist() when the DB watchlist
        # is empty (must exist BEFORE first scan — was previously missing and
        # raised AttributeError on an empty DB watchlist).
        self.watchlist: List[str] = []
        # Real confidence from RegimeDetector.classify(); 0.0 until classified.
        self.regime_confidence: float = 0.0
        self.active_strategies: List[str] = []
        self._scan_count: int = 0
        self._symbols_scanned_count: int = 0
        self._signals_generated: int = 0
        self._signals_passed_count: int = 0
        self._signals_rejected_count: int = 0
        self._trades_executed: int = 0
        self._errors_count: int = 0
        self._feed_alerted_down: bool = False
        self._rejections_by_gate: Dict[str, int] = {}
        self._rejections_by_strategy: Dict[str, int] = {}
        self._recent_scan_telemetry: List[Dict[str, Any]] = []

        # ── Phase 1 robustness: exit management + shadow tracking ──────────
        # Shadow strategies: scanned + risk-gated + recorded, NEVER traded.
        # Names are normalised to UPPER CASE for the per-signal divert check
        # (strategy_name.upper() in shadow_strategies), while the original
        # casing is kept for registry lookups (registry keys are exact-match).
        try:
            _shadow_raw = list(self.config.get_shadow_strategies())
        except Exception:
            _shadow_raw = []
        self.shadow_strategies: set = {str(s).upper() for s in _shadow_raw}
        # P2: the shadow list doubles as the SCAN LIST — strategies listed in
        # strategy_shadow_mode are scanned in EVERY regime even when absent
        # from the regime activation map (this also fixes the dead TRS shadow:
        # TRS was in strategy_shadow_mode but paused in every regime map, so
        # it was never scanned at all).
        self._shadow_scan_strategies: List[str] = [str(s) for s in _shadow_raw if str(s)]
        # In-memory registry of unresolved shadow signals (signal_id -> data)
        self._shadow_signals: Dict[str, dict] = {}
        # Time stop config: {"default": 90, "PTC": 75, ...}
        _risk_cfg_init = self.config.get_risk_config() if hasattr(self.config, "get_risk_config") else {}
        _ts_cfg = _risk_cfg_init.get("time_stop_minutes", {}) or {}
        self._time_stop_map: Dict[str, float] = {
            str(k).upper(): float(v) for k, v in (_ts_cfg.items() if isinstance(_ts_cfg, dict) else [])
        }
        self._time_stop_default: float = float(self._time_stop_map.pop("DEFAULT", 90))
        # Fail-fast config: {"window_minutes": 15, "atr_mult": {"MB": 0.75}}
        _ff_cfg = _risk_cfg_init.get("fail_fast", {}) or {}
        _ff_mults = _ff_cfg.get("atr_mult", {}) or {}
        self._fail_fast_window_minutes: float = float(_ff_cfg.get("window_minutes", 15))
        self._fail_fast_atr_mults: Dict[str, float] = {
            str(k).upper(): float(v) for k, v in (_ff_mults.items() if isinstance(_ff_mults, dict) else [])
        }
        self._shadow_max_age_minutes: float = float(_risk_cfg_init.get("shadow_signal_max_age_minutes", 90))
    @asynccontextmanager
    async def _repo_context(self):
        """Context manager yielding repository and ensuring session cleanup."""
        if not self._repo_getter:
            yield None
            return
        getter_res = self._repo_getter()
        repo = await getter_res if asyncio.iscoroutine(getter_res) else getter_res
        try:
            yield repo
        finally:
            if hasattr(repo, "close") and callable(repo.close):
                try:
                    close_res = repo.close()
                    if asyncio.iscoroutine(close_res):
                        await close_res
                except Exception:
                    pass

    async def _route_alert(self, alert_type: str, data: Any) -> None:
        """Route an alert through the alert_manager to Telegram / WS / Logs."""
        if self.alert_manager is not None:
            try:
                res = self.alert_manager.route_alert(alert_type, data)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as alert_err:
                logger.error("Failed to route alert '%s': %s", alert_type, alert_err)

    # ------------------------------------------------------------------
    # Repository accessor
    # ------------------------------------------------------------------

    async def _get_repo(self):
        return await self._repo_getter()

    @asynccontextmanager
    async def _repo_context(self):
        """Context manager yielding repository and ensuring session cleanup."""
        getter_res = self._repo_getter()
        repo = await getter_res if asyncio.iscoroutine(getter_res) else getter_res
        try:
            yield repo
        finally:
            if hasattr(repo, "close") and callable(repo.close):
                try:
                    close_res = repo.close()
                    if asyncio.iscoroutine(close_res):
                        await close_res
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(
        self,
        mode: str = "paper",
        broker_name: str = "paper",
        strategy_names: Optional[List[str]] = None,
        initial_capital: Optional[float] = None,
        broker_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Start the trading engine.

        Args:
            mode: 'paper' or 'live'.
            broker_name: Broker identifier.
            strategy_names: Optional list of specific strategies to activate.
                If None, uses regime-based activation from config.
            initial_capital: Starting capital. Defaults to config value.
            broker_config: Credential overrides (dict of broker kwargs). When
                provided (e.g. decrypted credentials from the DB), these take
                precedence over the config-file `brokers:` section — this is
                how the UI-saved credentials reach a live engine session.

        Returns:
            Dict with session_id and status.
        """
        if self.state not in (EngineState.STOPPED, EngineState.ERROR):
            return {"status": "already_running", "state": self.state.value}

        self.state = EngineState.STARTING
        await self._broadcast("engine", {"type": "engine_state_change", "state": self.state.value})

        try:
            # Validate mode
            if mode not in ("paper", "live"):
                mode = "paper"
            self.mode = mode
            self.broker_name = broker_name or "paper"

            # Capital
            self.initial_capital = (
                float(initial_capital)
                if initial_capital is not None
                else resolve_total_capital(config=self.config)
            )

            # Create broker — DB credentials (decrypted by the API layer)
            # take precedence over the config-file `brokers:` section.
            file_config = self.config.get_broker_config(broker_name) or {}
            merged_config: Dict[str, Any] = dict(file_config)
            if broker_config:
                merged_config.update({k: v for k, v in broker_config.items() if v})
            # CORRECTION (live-market validation 2026-08-28): the PaperBroker
            # ledger must start from the SAME capital the engine/session use.
            # BrokerFactory's library default is 100000.0 — previously the
            # engine never passed its resolved capital, so every paper broker
            # ledger started at ₹100k while the engine/sizer/session used the
            # configured ₹500k (margin checks and capital arithmetic ran on
            # two different numbers). Pass the engine-resolved capital for
            # paper sessions; live brokers fetch real margin below.
            if mode == "paper":
                merged_config.setdefault("initial_capital", self.initial_capital)
            self.broker = self.broker_factory.create(broker_name, mode=mode, **(merged_config or {}))

            # Authenticate
            if hasattr(self.broker, "authenticate"):
                try:
                    await self.broker.authenticate()
                    logger.info("Broker '%s' authenticated successfully", broker_name)
                except Exception as auth_exc:
                    logger.error("Broker '%s' authentication failed: %s", broker_name, auth_exc, exc_info=True)
                    raise

            # Connect feed
            if self.feed_manager is not None:
                self.feed = self.feed_manager
                if hasattr(self.feed_manager, "connect"):
                    try:
                        await self.feed_manager.connect()
                        logger.info("Feed manager connected")
                    except Exception as feed_exc:
                        logger.error("Feed manager connection failed: %s", feed_exc, exc_info=True)
                        raise

            # Wire the live feed into brokers that support it (PaperBroker uses
            # it to fill MARKET orders at the REAL LTP instead of the signal's
            # stale entry price — previously .feed was declared "injected
            # externally" but nothing ever injected it).
            if self.broker is not None and self.feed_manager is not None \
                    and hasattr(self.broker, "feed") and getattr(self.broker, "feed", None) is None:
                try:
                    self.broker.feed = self.feed_manager
                    logger.info("Injected feed manager into broker '%s' for live-LTP paper fills", broker_name)
                except Exception as inject_exc:
                    logger.warning("Could not inject feed into broker: %s", inject_exc)

            # Check for same-day session / mid-day restart before creating a new session
            same_day_session = None
            if hasattr(self.session_manager, "get_same_day_session"):
                same_day_session = await self.session_manager.get_same_day_session()

            # Safety guard: Refuse to resume cross-mode/cross-broker state
            if same_day_session and (
                same_day_session.get("mode") != mode or same_day_session.get("broker") != broker_name
            ):
                old_sess_id = same_day_session.get("session_id")
                logger.warning(
                    "Same-day session %s mode/broker mismatch (existing: mode=%s, broker=%s; requested: mode=%s, broker=%s). "
                    "Closing previous session as 'stopped' (superseded_mode_mismatch) and creating fresh session.",
                    old_sess_id,
                    same_day_session.get("mode"),
                    same_day_session.get("broker"),
                    mode,
                    broker_name,
                )
                if old_sess_id and hasattr(self.session_manager, "close_session"):
                    try:
                        await self.session_manager.close_session(
                            old_sess_id,
                            final_capital=float(same_day_session.get("initial_capital", 0.0)),
                            status="stopped",
                        )
                    except Exception as close_exc:
                        logger.warning("Failed to mark mismatched session %s as stopped: %s", old_sess_id, close_exc)
                same_day_session = None

            if same_day_session:
                # Same-day restart: preserve session_id and canonical starting state
                self.session_id = same_day_session["session_id"]
                await self.session_manager.resume_session(self.session_id)
                try:
                    recovered = await self.session_manager.recover_state(self.session_id)
                    if isinstance(recovered, dict):
                        self.current_regime = recovered.get("current_regime", "Sideways")
                        self.vix = recovered.get("vix", 15.0)
                        self.nifty_price = recovered.get("nifty_price", 0.0)
                        self.active_strategies = recovered.get("active_strategies", [])
                        if initial_capital is None and recovered.get("initial_capital"):
                            self.initial_capital = float(recovered["initial_capital"])
                        # Keep the paper-broker ledger aligned with the
                        # recovered session capital (broker was created before
                        # recovery could adjust self.initial_capital).
                        self._sync_paper_broker_capital()
                    logger.info(
                        "Resumed same-day session %s: regime=%s, vix=%.1f, starting_capital=%.2f",
                        self.session_id, self.current_regime, self.vix, self.initial_capital,
                    )
                except Exception as exc:
                    logger.warning("Could not recover state for same-day session %s: %s", self.session_id, exc)
            else:
                # Genuinely new trading day session (or fresh session after mode switch)
                if initial_capital is not None:
                    self.initial_capital = float(initial_capital)
                elif self.mode == "live":
                    # Live mode: attempt to fetch margin from broker (get_margin)
                    try:
                        if self.broker and hasattr(self.broker, "get_margin"):
                            margin_info = await self.broker.get_margin()
                            avail = None
                            if isinstance(margin_info, dict):
                                # Real broker return keys across Angel, Kite, Dhan, Fyers, Shoonya
                                # Checked with explicit None-check to preserve legitimate 0.0 margin
                                for k in (
                                    "available",
                                    "total",
                                    "net",
                                    "availablecash",
                                    "availMargin",
                                    "cashBalance",
                                    "available_cash",
                                ):
                                    v = margin_info.get(k)
                                    if v is not None:
                                        try:
                                            avail = float(v)
                                            break
                                        except (TypeError, ValueError):
                                            pass
                            elif isinstance(margin_info, (int, float)):
                                avail = float(margin_info)

                            if avail is not None and avail >= 0:
                                self.initial_capital = avail
                                self._sync_paper_broker_capital()
                                logger.info("Fetched live broker margin for %s: ₹%.2f", self.broker_name, self.initial_capital)
                            else:
                                logger.warning(
                                    "Live broker %s returned invalid margin (%s). Falling back to configured capital.",
                                    self.broker_name,
                                    margin_info,
                                )
                                self.initial_capital = resolve_total_capital(config=self.config)
                        else:
                            self.initial_capital = resolve_total_capital(config=self.config)
                    except Exception as margin_exc:
                        logger.critical(
                            "Failed to fetch live broker margin on engine start: %s. Falling back to configured capital.",
                            margin_exc,
                            exc_info=True,
                        )
                        self.initial_capital = resolve_total_capital(config=self.config)
                else:
                    # Paper mode: check carry_forward_capital setting
                    cap_cfg = self.config.get_capital_config() if hasattr(self.config, "get_capital_config") else {}
                    carry_forward = bool(cap_cfg.get("carry_forward_capital", False)) if isinstance(cap_cfg, dict) else False
                    if carry_forward:
                        try:
                            async with self._repo_context() as repo:
                                prior_summary = None
                                if repo is not None:
                                    if hasattr(repo, "get_latest_prior_daily_summary"):
                                        prior_summary = await repo.get_latest_prior_daily_summary()
                                    elif hasattr(repo, "get_latest_daily_summary"):
                                        prior_summary = await repo.get_latest_daily_summary()

                                if prior_summary and getattr(prior_summary, "ending_capital", None) and prior_summary.ending_capital > 0:
                                    self.initial_capital = float(prior_summary.ending_capital)
                                    logger.info(
                                        "Carried forward starting capital ₹%.2f from prior session (%s)",
                                        self.initial_capital,
                                        getattr(prior_summary, "date", "prior"),
                                    )
                                else:
                                    self.initial_capital = resolve_total_capital(config=self.config)
                        except Exception as cf_exc:
                            logger.warning(
                                "Could not carry forward capital from prior summary: %s. Using configured total capital.",
                                cf_exc,
                            )
                            self.initial_capital = resolve_total_capital(config=self.config)
                    else:
                        self.initial_capital = resolve_total_capital(config=self.config)

                # Sync paper broker's internal capital if running in paper mode
                if self.mode == "paper" and self.broker:
                    if hasattr(self.broker, "capital"):
                        self.broker.capital = self.initial_capital
                    if hasattr(self.broker, "initial_capital"):
                        self.broker.initial_capital = self.initial_capital

                self.session_id = await self.session_manager.create_session(
                    mode=mode,
                    broker=broker_name,
                    initial_capital=self.initial_capital,
                )

            # Initialize active strategies
            if strategy_names:
                self.active_strategies = list(strategy_names)
            elif not self.active_strategies:
                activation_config = self.config.get_strategy_activation(self.current_regime)
                self.active_strategies = list(activation_config.get("active", []))
                logger.info(
                    "Activated strategies for regime '%s': %s",
                    self.current_regime,
                    self.active_strategies,
                )

            # Reset counters
            self._scan_count = 0
            self._signals_generated = 0
            self._trades_executed = 0
            self._errors_count = 0
            self._start_time = datetime.now(IST)

            if same_day_session is None:
                # Genuinely new trading day: reset daily opportunities and telemetry
                self.pending_opportunities = {}
                # CORRECTION (live-market validation run 2, 2026-08-28): this
                # was reset to a LIST here while __init__ and every consumer
                # (invalidation paths: `self.invalidated_opportunities[opp_id]
                # = opp`, len() cap, pop()) use DICT semantics — the first
                # opportunity invalidation on a fresh session raised
                # TypeError("list indices must be integers or slices, not
                # str") and aborted the scan cycle (error_logs 13:10/13:19
                # IST today). Reset to an empty dict like __init__ does.
                self.invalidated_opportunities = {}
                self._recent_scan_telemetry = []
                self._symbols_scanned_count = 0
                self._signals_passed_count = 0
                self._signals_rejected_count = 0
                self._rejections_by_gate = {}
                self._rejections_by_strategy = {}

            # Rehydrate today's daily-risk state from the DB closed-trades
            # ledger. Without this, a mid-day stop/restart would zero
            # daily_pnl / daily_trades / consecutive_losses and the engine
            # could blow past the daily-loss, max-trades and consecutive-loss
            # limits a SECOND time in the same trading day.
            try:
                await self._rehydrate_daily_risk()
            except Exception as dr_exc:
                logger.warning(
                    "Could not rehydrate daily risk from today's ledger: %s. "
                    "Daily risk counters start from zero for this run.",
                    dr_exc,
                    exc_info=True,
                )

            # Rehydrate today's unresolved SHADOW signals so a mid-day restart
            # does not orphan them (they would never resolve to an outcome).
            try:
                async with self._repo_context() as repo:
                    if repo is not None:
                        for sig in await repo.get_todays_shadow_signals():
                            try:
                                sig_data = json.loads(sig.signal_data) if sig.signal_data else {}
                            except Exception:
                                sig_data = {}
                            self._shadow_signals[sig.id] = {
                                "signal_id": sig.id,
                                "symbol": sig.symbol,
                                "direction": sig.direction,
                                "strategy": sig.strategy,
                                "entry_price": float(sig.entry_price or 0.0),
                                "stop_loss": float(sig.stop_loss or 0.0),
                                "target": float(sig.target or 0.0),
                                "created_at": sig.created_at,
                                "signal_data": sig_data,
                            }
                if self._shadow_signals:
                    logger.info(
                        "Rehydrated %d unresolved shadow signal(s) from today's ledger",
                        len(self._shadow_signals),
                    )
            except Exception as shadow_exc:
                logger.warning("Could not rehydrate shadow signals: %s", shadow_exc, exc_info=True)

            # CORRECTION (live-market validation run 2, 2026-08-28):
            # pending opportunities live ONLY in engine memory — when the
            # process restarts mid-day they die silently, orphaning their
            # DB signals at status 'pending' forever (observed live:
            # HCLTECH 09:44, DABUR/HINDALCO from the prior day, MRF 11:33).
            # At start() time pending_opportunities is by definition empty,
            # so ANY pre-existing 'pending' signal is an orphan — expire it
            # with an honest reason so the ledger stays truthful.
            try:
                await self._expire_orphaned_pending_signals()
            except Exception as orphan_exc:
                logger.warning(
                    "Could not expire orphaned pending signals: %s", orphan_exc, exc_info=True
                )

            # Set running
            self.state = EngineState.RUNNING
            await self._broadcast("engine", {
                "type": "engine_state_change",
                "state": self.state.value,
                "session_id": self.session_id,
                "mode": self.mode,
            })
            await self._route_alert("engine_status", {
                "state": "running",
                "mode": mode,
                "broker": broker_name,
                "details": f"Session {self.session_id[:8]} started with {len(self.active_strategies)} active strategies ({self.current_regime} regime)",
            })

            # Start main loop as background task
            self._main_task = asyncio.create_task(self._main_loop())
            logger.info(
                "Engine started: mode=%s, broker=%s, session=%s, strategies=%s",
                mode,
                broker_name,
                self.session_id,
                self.active_strategies,
            )

            return {
                "status": "started",
                "session_id": self.session_id,
                "mode": mode,
                "broker": broker_name,
                "regime": self.current_regime,
                "strategies": self.active_strategies,
            }

        except Exception as exc:
            self.state = EngineState.ERROR
            await self.error_engine.handle_error(
                exc,
                context={"action": "engine_start", "mode": mode, "broker": broker_name},
                session_id=self.session_id,
            )
            await self._broadcast("engine", {"type": "engine_state_change", "state": "error"})
            await self._route_alert("engine_status", {
                "state": "error",
                "mode": mode,
                "broker": broker_name,
                "details": f"Engine failed to start: {exc}",
            })
            self._errors_count += 1
            logger.error("Engine start failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop(self) -> Dict[str, Any]:
        """Gracefully stop the engine.

        Saves state, cancels the main loop, disconnects broker/feed,
        and closes the session.
        """
        if self.state == EngineState.STOPPED:
            return {"status": "already_stopped"}

        logger.info("Engine stopping...")
        prev_state = self.state.value

        try:
            # Cancel main loop
            if self._main_task is not None and not self._main_task.done():
                self._main_task.cancel()
                try:
                    await self._main_task
                except asyncio.CancelledError:
                    pass
                self._main_task = None

            # Save state before shutting down
            if self.session_id:
                try:
                    await self.session_manager.save_state(self.session_id, self)
                except Exception as exc:
                    logger.warning("Failed to save session state on stop: %s", exc)

            # Close positions via broker if live mode
            final_capital = self.initial_capital or 0
            if self.broker is not None:
                try:
                    # Get current capital / P&L
                    if hasattr(self.broker, "get_balance"):
                        balance = await self.broker.get_balance()
                        if isinstance(balance, (int, float)):
                            final_capital = balance
                        elif isinstance(balance, dict):
                            final_capital = balance.get("available_cash", balance.get("net", self.initial_capital or 0))

                    # Disconnect broker
                    if hasattr(self.broker, "disconnect"):
                        await self.broker.disconnect()
                except Exception as exc:
                    logger.warning("Error during broker shutdown: %s", exc)

            # Disconnect feed
            if self.feed is not None and hasattr(self.feed, "disconnect"):
                try:
                    await self.feed.disconnect()
                except Exception as exc:
                    logger.warning("Error during feed disconnect: %s", exc)
                self.feed = None

            # Close session
            if self.session_id:
                try:
                    await self.session_manager.close_session(
                        self.session_id,
                        final_capital=final_capital,
                        status="stopped",
                    )
                except Exception as exc:
                    logger.warning("Failed to close session: %s", exc)

            self.state = EngineState.STOPPED
            self.broker = None

            await self._broadcast("engine", {
                "type": "engine_state_change",
                "state": self.state.value,
                "previous_state": prev_state,
            })
            await self._route_alert("engine_status", {
                "state": "stopped",
                "mode": self.mode or "",
                "broker": self.broker_name or "",
                "details": f"Trades executed: {self._trades_executed}, Scans completed: {self._scan_count}",
            })

            logger.info(
                "Engine stopped. Scans: %d, Signals: %d, Trades: %d, Errors: %d",
                self._scan_count, self._signals_generated, self._trades_executed, self._errors_count,
            )
            return {"status": "stopped", "final_capital": round(final_capital, 2)}

        except Exception as exc:
            self.state = EngineState.ERROR
            await self.error_engine.handle_error(
                exc,
                context={"action": "engine_stop", "previous_state": prev_state},
                session_id=self.session_id,
            )
            await self._route_alert("engine_status", {
                "state": "error",
                "mode": self.mode or "",
                "broker": self.broker_name or "",
                "details": f"Engine error on stop: {exc}",
            })
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Pause / Resume
    # ------------------------------------------------------------------

    async def pause(self) -> Dict[str, Any]:
        """Pause the engine scanning loop. Position management continues."""
        if self.state != EngineState.RUNNING:
            return {"status": "not_running", "state": self.state.value}

        self.state = EngineState.PAUSED
        await self._broadcast("engine", {"type": "engine_state_change", "state": "paused"})
        await self._route_alert("engine_status", {
            "state": "paused",
            "mode": self.mode or "",
            "broker": self.broker_name or "",
            "details": "Engine paused by user. Position risk management is still active.",
        })
        logger.info("Engine paused")
        return {"status": "paused"}

    async def resume(self) -> Dict[str, Any]:
        """Resume the engine scanning loop."""
        if self.state != EngineState.PAUSED:
            return {"status": "not_paused", "state": self.state.value}

        self.state = EngineState.RUNNING
        await self._broadcast("engine", {"type": "engine_state_change", "state": "running"})
        await self._route_alert("engine_status", {
            "state": "running",
            "mode": self.mode or "",
            "broker": self.broker_name or "",
            "details": "Engine scanning resumed.",
        })
        logger.info("Engine resumed")
        return {"status": "running"}

    # ------------------------------------------------------------------
    # Main Loop
    # ------------------------------------------------------------------

    async def _rehydrate_daily_risk(self) -> None:
        """Rebuild today's daily-risk state from the DB closed-trades ledger.

        DailyRiskManager is in-memory only: its counters die with the process.
        On a same-day engine restart this replays today's CLOSED trades
        (source of truth = DB ``trades`` ledger, plus each trade's position
        ``extra.partial_realized_pnl`` for partial-booking legs) so that
        daily_pnl, trade count, consecutive losses and peak capital are
        restored exactly as they were before the restart.

        Mirrors the accounting semantics of DailyRiskManager:
          - record_trade_result(): pnl>0 -> win (streak reset), pnl<0 -> loss
            (streak +1), pnl==0 -> breakeven (streak reset); one trade per
            closed trade row; peak capital tracks running max of
            total_capital + daily_pnl.
          - record_pnl(): partial-booking legs add to daily_pnl WITHOUT
            counting a trade (hence the position extra lookup).

        Cooloff timers are deliberately NOT reconstructed: a streak at/above
        max_consecutive_losses blocks new trades by itself (max_consec_hit in
        check_daily_limits), and re-arming a cooloff "now" would mis-date it.

        No-op on a fresh trading day (no closed trades yet) and when the
        engine runs without a repository.
        """
        daily_risk = getattr(self, "daily_risk", None)
        if daily_risk is None:
            return

        async with self._repo_context() as repo:
            if repo is None or not hasattr(repo, "get_todays_closed_trades"):
                return
            trades = await repo.get_todays_closed_trades()

            if not trades:
                return  # fresh trading day — nothing to restore

            # Partial-booking legs: the trade row's net_pnl covers only the
            # final leg; each partial leg's realized P&L lives in the linked
            # position's extra JSON (written by _execute_partial_booking).
            partial_by_trade: Dict[str, float] = {}
            for t in trades:
                pos_id = getattr(t, "position_id", None)
                if not pos_id or not hasattr(repo, "get_position"):
                    continue
                pos = await repo.get_position(pos_id)
                if pos is None:
                    continue
                raw = getattr(pos, "extra", None)
                extra: Dict[str, Any] = {}
                if isinstance(raw, dict):
                    extra = raw
                elif isinstance(raw, str) and raw.strip():
                    try:
                        extra = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        extra = {}
                try:
                    partial_by_trade[t.id] = float(extra.get("partial_realized_pnl", 0.0) or 0.0)
                except (TypeError, ValueError):
                    partial_by_trade[t.id] = 0.0

        # Ledger returns newest-first; replay chronologically instead.
        def _exit_key(t):
            v = getattr(t, "exit_time", None) or getattr(t, "updated_at", None) or ""
            return str(v)

        trades = sorted(trades, key=_exit_key)

        total_capital = float(getattr(daily_risk, "total_capital", 0.0) or 0.0)
        daily_pnl = 0.0
        wins = losses = breakeven = 0
        consecutive_losses = 0
        peak_capital = total_capital

        for t in trades:
            pnl = float(getattr(t, "net_pnl", 0.0) or 0.0)
            partial = float(partial_by_trade.get(t.id, 0.0) or 0.0)

            # Streak/counters follow record_trade_result() semantics, driven
            # by the trade's final-leg result (exactly what the live engine
            # would have fed it on close).
            if pnl > 0:
                wins += 1
                consecutive_losses = 0
            elif pnl < 0:
                losses += 1
                consecutive_losses += 1
            else:
                breakeven += 1
                consecutive_losses = 0

            # P&L includes partial legs (record_pnl semantics).
            daily_pnl += pnl + partial
            current_capital = total_capital + daily_pnl
            if current_capital > peak_capital:
                peak_capital = current_capital

        daily_risk.daily_pnl = round(daily_pnl, 2)
        daily_risk.daily_trades = len(trades)
        daily_risk.wins = wins
        daily_risk.losses = losses
        daily_risk.breakeven = breakeven
        daily_risk.consecutive_losses = consecutive_losses
        daily_risk.peak_capital = peak_capital

        logger.info(
            "Rehydrated daily risk from %d closed trade(s) today: "
            "pnl=₹%.2f, trades=%d, W/L/B=%d/%d/%d, consecutive_losses=%d",
            len(trades), daily_pnl, len(trades), wins, losses, breakeven,
            consecutive_losses,
        )

    async def _main_loop(self) -> None:
        """Core scanning loop. Runs while state is RUNNING or PAUSED.

        Each iteration:
        1. Check market open / session
        2. Update position prices
        3. Check partial bookings & trailing SLs on open positions
        4. Check daily risk status
        5. If market open AND trade window AND not paused AND risk OK:
           a. Fetch watchlist
           b. Run strategy scans per symbol
           c. Run risk gates on signals
           d. Calculate position size
           e. Create opportunity, push to WS
        6. Sleep for scan_interval
        """
        scan_interval = self.config.get_engine_config().get("scan_interval_seconds", 180)
        # P1: when a realtime feed (Fyers 1m) is the active source, the loop
        # tightens to scan_interval_realtime_seconds (default 60) — fresh
        # data makes faster scans meaningful. Yahoo mode keeps 180s to stay
        # rate-limit friendly. Evaluated per-iteration so a mid-session feed
        # failover (Fyers → Yahoo backup) automatically relaxes the cadence.
        realtime_scan_interval = self.config.get_engine_config().get("scan_interval_realtime_seconds", 60)
        max_retries = self.config.get_engine_config().get("max_scan_retries", 10)
        retry_count = 0
        _last_success_time = datetime.now(IST)
        _RETRY_RESET_SECONDS = 300  # Reset retry counter after 5 min of uptime

        while self.state in (EngineState.RUNNING, EngineState.PAUSED, EngineState.SCANNING):
            iteration_start = datetime.now(IST)

            try:
                # --- Step 1: Market check ---
                market_status = self.market_hours.get_market_status()
                await self._broadcast("market", {
                    "type": "market_status",
                    "is_open": market_status["is_open"],
                    "session": market_status["session"],
                })

                # --- Step 2: Update position prices ---
                try:
                    await self._update_position_prices()
                except Exception as pos_price_exc:
                    logger.warning("Could not update position prices: %s", pos_price_exc, exc_info=True)
                    self._errors_count += 1

                # --- Step 3: Manage open positions (SL, target, partial bookings, trailing SL) ---
                try:
                    await self._manage_all_positions()
                except Exception as manage_exc:
                    logger.warning("Could not manage positions: %s", manage_exc, exc_info=True)
                    self._errors_count += 1

                # --- Step 3b: Validate pending opportunities against live prices & TTL ---
                try:
                    await self._validate_pending_opportunities()
                except Exception as validate_exc:
                    logger.warning("Could not validate pending opportunities: %s", validate_exc, exc_info=True)
                    self._errors_count += 1

                # --- Step 3c: Resolve SHADOW signal outcomes against live prices ---
                try:
                    await self._evaluate_shadow_signals()
                except Exception as shadow_eval_exc:
                    logger.warning("Could not evaluate shadow signals: %s", shadow_eval_exc, exc_info=True)

                # --- Step 4: Check daily risk ---
                risk_ok = False
                can_trade = False
                try:
                    # HOTFIX #7: feed the real live position count + capital
                    # usage into the daily-risk status so the /status banner
                    # reflects reality (previously hardcoded to 0; the G1 risk
                    # GATE already received the true count via
                    # _build_risk_context and was never affected).
                    _live_open_positions = []
                    async with self._repo_context() as _risk_repo:
                        if _risk_repo:
                            _live_open_positions = await _risk_repo.get_open_positions()
                    _capital_in_use = sum(
                        float(getattr(p, "entry_price", 0.0) or 0.0)
                        * float(getattr(p, "remaining_qty", getattr(p, "quantity", 0)) or 0)
                        for p in _live_open_positions
                    )
                    risk_status = await self.daily_risk.get_daily_risk_status(
                        open_positions_count=len(_live_open_positions),
                        capital_in_use=_capital_in_use,
                    )
                    risk_ok = risk_status.can_take_new_trades

                    await self._broadcast("risk", {
                        "type": "daily_risk_update",
                        "can_take_new_trades": risk_ok,
                        "block_reason": risk_status.block_reason,
                        "net_pnl": risk_status.net_pnl,
                        "open_positions": risk_status.open_positions,
                        "consecutive_losses": risk_status.consecutive_losses,
                    })
                except Exception as risk_exc:
                    risk_ok = False
                    logger.error("Could not check daily risk (failing closed): %s", risk_exc, exc_info=True)
                    self._errors_count += 1

                # --- Steps 5+: Only scan if conditions are met ---
                can_trade = (
                    self.state == EngineState.RUNNING
                    and market_status["is_open"]
                    and self.market_hours.is_new_trade_window()
                    and risk_ok
                )

                if can_trade:
                    self.state = EngineState.SCANNING
                    await self._broadcast("engine", {"type": "engine_state_change", "state": "scanning"})

                    try:
                        await self._scan_watchlist()
                    except Exception as scan_exc:
                        logger.error("Watchlist scan error: %s", scan_exc, exc_info=True)
                        try:
                            await self.error_engine.handle_error(
                                scan_exc,
                                context={"action": "watchlist_scan"},
                                session_id=self.session_id,
                            )
                        except Exception:
                            logger.debug("Failed to report watchlist scan error to error engine")
                        self._errors_count += 1
                    finally:
                        if self.state == EngineState.SCANNING:
                            self.state = EngineState.RUNNING

                    self._scan_count += 1

                # Auto-save state periodically (every 10 scans or iterations)
                if self.session_id and (self._scan_count > 0 and self._scan_count % 10 == 0):
                    try:
                        await self.session_manager.save_state(self.session_id, self)
                    except Exception as save_exc:
                        logger.warning("Periodic state save failed: %s", save_exc, exc_info=True)

                # Reset consecutive failure counter on successful cycle
                retry_count = 0
                _last_success_time = datetime.now(IST)

            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                return
            except Exception as loop_exc:
                retry_count += 1
                self._errors_count += 1
                logger.error("Main loop error (attempt %d/%d): %s", retry_count, max_retries, loop_exc, exc_info=True)

                # Protect error reporting — it must never crash the loop
                try:
                    await self.error_engine.handle_error(
                        loop_exc,
                        context={"action": "main_loop", "attempt": retry_count},
                        session_id=self.session_id,
                    )
                except Exception as err_exc:
                    logger.debug("Failed to report main loop error to error engine: %s", err_exc)

                # Reset retry counter if engine has been running long enough
                # (prevents transient blips from accumulating over hours)
                elapsed_since_success = (datetime.now(IST) - _last_success_time).total_seconds()
                if elapsed_since_success > _RETRY_RESET_SECONDS and retry_count < max_retries:
                    logger.info(
                        "Resetting retry counter (was %d) — engine ran successfully for %.0fs before this error",
                        retry_count, elapsed_since_success,
                    )
                    retry_count = 1
                    _last_success_time = datetime.now(IST)

                if retry_count >= max_retries:
                    logger.critical("Max retries (%d) exceeded, stopping engine", max_retries)
                    self.state = EngineState.ERROR
                    try:
                        await self._broadcast("engine", {"type": "engine_state_change", "state": "error"})
                    except Exception:
                        pass
                    return

            # Sleep for scan interval (cancellable) — realtime-aware:
            # 60s on Fyers 1m feed, 180s (config) on Yahoo.
            try:
                effective_interval = scan_interval
                try:
                    active_feed = (
                        self.feed.get_active_feed()
                        if self.feed is not None and hasattr(self.feed, "get_active_feed")
                        else self.feed
                    )
                    if active_feed is not None and bool(getattr(active_feed, "is_realtime", False)):
                        effective_interval = min(scan_interval, realtime_scan_interval)
                except Exception:
                    pass
                await asyncio.sleep(effective_interval)
            except asyncio.CancelledError:
                logger.info("Main loop sleep cancelled")
                return

        logger.info("Main loop exited (state=%s)", self.state.value)

    # ------------------------------------------------------------------
    # Scanning & Telemetry
    # ------------------------------------------------------------------

    def _record_telemetry_event(
        self,
        symbol: str,
        strategy: str,
        status: str,
        direction: str = "—",
        price: float = 0.0,
        confidence: float = 0.0,
        gate: Optional[str] = None,
        reason: str = "",
    ) -> None:
        """Record a scan telemetry event and broadcast it via WebSocket in real-time."""
        event = {
            "time": datetime.now(IST).strftime("%H:%M:%S"),
            "symbol": symbol,
            "strategy": strategy,
            "status": status,
            "direction": direction,
            "price": round(float(price if price is not None else 0.0), 2),
            "confidence": round(float(confidence if confidence is not None else 0.0), 3),
            "gate": gate or ("ALL_GATES_PASSED" if status == "PASSED" else "—"),
            "reason": reason or ("Passed" if status == "PASSED" else "No setup trigger"),
        }
        self._recent_scan_telemetry.append(event)
        if len(self._recent_scan_telemetry) > 100:
            self._recent_scan_telemetry.pop(0)

        # Broadcast individual event for real-time WebSocket subscribers
        try:
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                task = loop.create_task(self._broadcast("telemetry", {
                    "type": "scan_telemetry_event",
                    "event": event,
                }))
                # Attach done callback to catch and log task errors
                def _log_broadcast_err(t: asyncio.Task) -> None:
                    try:
                        if not t.cancelled() and t.exception():
                            logger.debug("Telemetry broadcast error: %s", t.exception())
                    except Exception:
                        pass
                task.add_done_callback(_log_broadcast_err)
        except Exception as exc:
            logger.debug("Failed to schedule telemetry broadcast: %s", exc)

    def get_scan_telemetry(self) -> Dict[str, Any]:
        """Return aggregated scan metrics and recent scan events with explicit idle status."""
        market_status = self.market_hours.get_market_status() if hasattr(self, "market_hours") else {"is_open": True, "session": "regular"}
        is_market_open = market_status.get("is_open", True)
        is_trade_win = self.market_hours.is_new_trade_window() if hasattr(self, "market_hours") else True

        scanning_status = "scanning_active"
        idle_reason = ""

        if self.state == EngineState.STOPPED:
            scanning_status = "engine_stopped"
            idle_reason = "Engine is stopped — click Start Engine to activate scanning."
        elif self.state == EngineState.PAUSED:
            scanning_status = "paused"
            idle_reason = "Engine is paused — resume engine to continue scanning."
        elif not is_market_open:
            scanning_status = "market_closed"
            session = market_status.get("session", "closed")
            idle_reason = f"Market is closed ({session}). Live scanner automatically resumes during market hours (09:15-15:30 IST)."
        elif not is_trade_win:
            scanning_status = "outside_trade_window"
            idle_reason = "Scanner idle — outside trade window (09:15-15:15 IST). New trade entries are blocked during initial market opening and closing rush."
        elif hasattr(self, "daily_risk") and self.daily_risk is not None:
            risk_state = getattr(self.daily_risk, "_state", None)
            if risk_state and getattr(risk_state, "block_reason", None):
                scanning_status = "risk_blocked"
                idle_reason = f"Scanner idle — blocked by daily risk limits: {risk_state.block_reason}"

        return {
            "total_scans": self._scan_count,
            "symbols_scanned": self._symbols_scanned_count,
            "signals_generated": self._signals_generated,
            "signals_passed": self._signals_passed_count,
            "signals_rejected": self._signals_rejected_count,
            "rejections_by_gate": dict(self._rejections_by_gate),
            "rejections_by_strategy": dict(self._rejections_by_strategy),
            "active_strategies": list(self.active_strategies),
            "broker": self.broker_name or "paper",
            "mode": self.mode or "paper",
            "state": self.state.value,
            "scanning_status": scanning_status,
            "idle_reason": idle_reason,
            "recent_events": list(self._recent_scan_telemetry[-50:]),
        }

    async def _scan_watchlist(self) -> None:
        """Fetch watchlist and run strategy scans for each symbol."""
        async with self._repo_context() as repo:
            watchlist_items = await repo.get_active_watchlist()

            if not watchlist_items:
                if self.watchlist:
                    class _WatchlistItem:
                        def __init__(self, sym):
                            self.symbol = sym
                    watchlist_items = [_WatchlistItem(s) for s in self.watchlist]
                else:
                    logger.debug("Watchlist is empty, skipping scan")
                    self._record_telemetry_event(
                        symbol="WATCHLIST",
                        strategy="ALL",
                        status="NO_SETUP",
                        reason="Watchlist empty (populated automatically at pre-market or via manual watchlist)",
                    )
                    return

            # Get current VIX and regime from feed/broker if available (updates self.active_strategies)
            await self._update_market_context()

            # Halt new signal generation if VIX data is critically stale
            if self.vix_critical_stale:
                logger.critical(
                    "Halting new signal generation: VIX data is critically stale (last updated: %s)",
                    self.vix_updated_at.isoformat() if self.vix_updated_at else "NEVER",
                )
                self._record_telemetry_event(
                    symbol="WATCHLIST",
                    strategy="ALL",
                    status="HALTED",
                    gate="G7_VIX_CRITICAL_STALE",
                    reason=f"New signals halted: VIX critically stale (age > {self.vix_staleness_critical_seconds}s)",
                )
                return

            if not _STRATEGIES_AVAILABLE:
                logger.debug("No strategy registry available, skipping signal generation")
                return

            if not self.active_strategies:
                logger.debug("No active strategies, skipping scan")
                return

            self._symbols_scanned_count += len(watchlist_items)

            # Prune any stale or invalidated opportunities before watchlist iteration
            await self._validate_pending_opportunities()

            # Fetch open positions once per scan cycle to avoid redundant per-symbol DB queries
            open_positions = await repo.get_open_positions() if repo else []
            open_position_symbols = {
                getattr(p, "symbol", "") for p in open_positions
                if getattr(p, "symbol", None)
            }

            for item in watchlist_items:
                symbol = item.symbol

                # Check 1: Symbol already has an active open position
                if symbol in open_position_symbols:
                    logger.debug("Skipping %s: open position already active", symbol)
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy="ALL",
                        status="SKIPPED",
                        gate="OpenPosition",
                        reason=f"Symbol {symbol} already has an active open position",
                    )
                    continue

                # Check 2: Symbol already has an active pending opportunity
                async with self._opportunities_lock:
                    has_pending = any(
                        opp.get("symbol") == symbol
                        for opp in self.pending_opportunities.values()
                    )
                if has_pending:
                    logger.debug("Skipping %s: pending opportunity exists", symbol)
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy="ALL",
                        status="SKIPPED",
                        gate="PendingOpportunity",
                        reason=f"Pending opportunity already exists for {symbol}",
                    )
                    continue

                try:
                    await self._scan_symbol(symbol, repo, open_positions=open_positions)
                except Exception as sym_exc:
                    logger.warning("Error scanning %s: %s", symbol, sym_exc)
                    self._errors_count += 1
                    await self.error_engine.handle_error(
                        sym_exc,
                        context={"action": "scan_symbol", "symbol": symbol},
                        session_id=self.session_id,
                    )

            # Broadcast telemetry update to WebSocket subscribers
            try:
                await self._broadcast("telemetry", {
                    "type": "scan_telemetry",
                    "telemetry": self.get_scan_telemetry(),
                })
            except Exception:
                pass

    async def _scan_symbol(self, symbol: str, repo, open_positions: Optional[list] = None) -> None:
        """Run all active strategies on a single symbol."""
        if self.vix_critical_stale:
            logger.debug("Skipping symbol scan for %s: VIX data is critically stale", symbol)
            return

        # Fetch candles from feed
        candles = []
        if self.feed is not None and hasattr(self.feed, "get_candles"):
            try:
                candles = await self.feed.get_candles(symbol, timeframe="5m", count=100)
            except TypeError:
                candles = await self.feed.get_candles(symbol, timeframe="5min", limit=100)
        elif self.broker is not None and hasattr(self.broker, "get_candles"):
            try:
                candles = await self.broker.get_candles(symbol, timeframe="5m", count=100)
            except TypeError:
                candles = await self.broker.get_candles(symbol, timeframe="5min", limit=100)

        if not candles or len(candles) < 20:
            logger.debug("Insufficient candles for %s: %d", symbol, len(candles) if candles else 0)
            self._record_telemetry_event(
                symbol=symbol,
                strategy="ALL",
                status="NO_SETUP",
                reason=f"Insufficient candles ({len(candles) if candles else 0}/20)",
            )
            return

        # ------------------------------------------------------------------
        # Data-freshness guard (DATA_STALE_CANDLES, Phase 5)
        # A delisted/suspended symbol (e.g. TATAMOTORS after the Oct-2025
        # demerger) or a degraded feed can still return OLD candles. Running
        # strategies on them generates phantom signals at stale prices, so
        # during open market hours the newest 5m candle must be recent.
        # Outside market hours the check is skipped (pre-market/after-hours
        # scans legitimately see yesterday's bars). This is a data-hygiene
        # skip — intentionally NOT using a G<number> id (the G16 namespace
        # belongs to the G16_MultiTimeframe risk gate).
        # ------------------------------------------------------------------
        if self.stale_candle_max_age_minutes > 0:
            try:
                market_open = bool(self.market_hours.is_market_open()) if self.market_hours else True
            except Exception:
                market_open = True
            if market_open:
                age_minutes = get_last_candle_age_minutes(candles)
                if age_minutes is not None and age_minutes > self.stale_candle_max_age_minutes:
                    if symbol not in self._stale_data_symbols_warned:
                        self._stale_data_symbols_warned.add(symbol)
                        logger.warning(
                            "Stale data guard: skipping %s — newest candle is %.0f min old "
                            "(max %d). Symbol may be delisted/suspended or the feed is degraded.",
                            symbol, age_minutes, int(self.stale_candle_max_age_minutes),
                        )
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy="ALL",
                        status="SKIPPED",
                        gate="DATA_STALE_CANDLES",
                        reason=(
                            f"Stale candles: newest bar {int(age_minutes)}m old "
                            f"(max {int(self.stale_candle_max_age_minutes)}m) — possible delisting/suspension"
                        ),
                    )
                    return

        # Get current price
        current_price = 0.0
        if candles:
            last_candle = candles[-1]
            if isinstance(last_candle, dict):
                current_price = last_candle.get("close", 0)
            else:
                current_price = getattr(last_candle, "close", 0)

        if current_price <= 0:
            return

        # P2: scan set = trading strategies (regime map) + shadow-tracked
        # strategies (scanned every regime, signals recorded, never traded).
        # Inlined (not via _scan_strategy_list) so partially-mocked engines
        # in tests keep working when the helper itself is mocked out.
        scan_strategies = list(self.active_strategies or [])
        _seen_scan = {str(s).upper() for s in scan_strategies}
        for _shadow_name in (getattr(self, "_shadow_scan_strategies", None) or []):
            if _shadow_name and str(_shadow_name).upper() not in _seen_scan:
                scan_strategies.append(_shadow_name)
                _seen_scan.add(str(_shadow_name).upper())

        # Run each strategy in the scan set
        for strategy_name in scan_strategies:
            try:
                signal = await self._execute_strategy_scan(
                    symbol=symbol,
                    candles=candles,
                    strategy_name=strategy_name,
                    regime=self.current_regime,
                    vix=self.vix,
                )

                if signal is None:
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy=strategy_name,
                        status="NO_SETUP",
                        price=current_price,
                        reason="Strategy entry criteria not met",
                    )
                    continue

                self._signals_generated += 1
                logger.info(
                    "Signal from %s on %s: %s @ %.2f (conf=%.2f)",
                    strategy_name, symbol, signal.get("direction", "?"),
                    signal.get("entry_price", 0), signal.get("confidence", 0),
                )

                # ----------------------------------------------------------
                # Attach REAL volume ratio so G15_VolumeLiquidity evaluates
                # actual liquidity instead of its 1.0 fallback no-op.
                #
                # CORRECTION (live-market validation, 2026-08-28): the raw
                # metric compared the FORMING candle's partial volume against
                # completed-bar averages — a 1-minute-old bar scored ~0.2x of
                # its own run-rate, so ultra-liquid names (BAJFINANCE,
                # JSWSTEEL) read an absurd 0.03-0.05x when the 180s scan
                # timer landed early in a bar. G15's verdict was a scan-timing
                # lottery. The corrected metric takes the MAX of:
                #   1. last COMPLETED bar vs the prior 19 completed bars
                #      (definitive, apples-to-apples), and
                #   2. the forming bar's run-rate (volume prorated by elapsed
                #      bar time, floor 20% to bound extrapolation)
                # Both components use only real feed data — no synthetic
                # values. Quiet tape still fails (e.g. a genuine 0.76x day).
                # ----------------------------------------------------------
                try:
                    if isinstance(candles, list) and len(candles) >= 21:
                        _tail = [c for c in candles[-21:] if isinstance(c, dict)]
                        _vols = [float(c.get("volume", 0) or 0) for c in _tail]
                        if len(_vols) >= 21 and sum(_vols[:-2]) > 0:
                            _avg_completed = sum(_vols[:-2]) / (len(_vols) - 2)
                            if _avg_completed > 0:
                                # 1) last completed bar (index -2): definitive ratio
                                _ratio_completed = _vols[-2] / _avg_completed
                                # 2) forming bar (index -1): prorated run-rate
                                _ratio_forming = 0.0
                                if _vols[-1] > 0:
                                    _elapsed_frac = 1.0
                                    try:
                                        _ts = _tail[-1].get("timestamp")
                                        if _ts:
                                            _bar_start = datetime.fromisoformat(str(_ts))
                                            _frac = (datetime.now(tz=_bar_start.tzinfo) - _bar_start).total_seconds() / 300.0
                                            _elapsed_frac = min(1.0, max(0.2, _frac))
                                    except Exception:
                                        pass
                                    _ratio_forming = (_vols[-1] / _elapsed_frac) / _avg_completed
                                signal.setdefault(
                                    "volume_ratio",
                                    round(max(_ratio_completed, _ratio_forming), 4),
                                )
                except Exception:
                    pass

                # ----------------------------------------------------------
                # Pre-gate orientation validation
                # Strategies emit sl_price / target_price. Reject any signal
                # where these are missing, zero, or directionally wrong before
                # wasting a risk-gate evaluation cycle.
                # ----------------------------------------------------------
                _entry  = float(signal.get("entry_price") or 0)
                _sl     = float(signal.get("sl_price") or 0)
                _target = float(signal.get("target_price") or 0)
                _dir    = signal.get("direction", "")

                _orientation_ok = False
                if _entry > 0 and _sl > 0 and _target > 0:
                    if _dir in ("BUY", "LONG"):
                        _orientation_ok = _sl < _entry < _target
                    elif _dir in ("SELL", "SHORT"):
                        _orientation_ok = _sl > _entry > _target

                if not _orientation_ok:
                    _inv_reason = (
                        f"Invalid signal geometry: entry={_entry} sl={_sl} "
                        f"target={_target} dir={_dir} — signal discarded before risk gates"
                    )
                    logger.warning(
                        "PRE-GATE REJECT %s/%s: %s",
                        strategy_name, symbol, _inv_reason,
                    )
                    self._signals_rejected_count += 1
                    self._rejections_by_gate["PRE_GATE_ORIENTATION"] = (
                        self._rejections_by_gate.get("PRE_GATE_ORIENTATION", 0) + 1
                    )
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy=strategy_name,
                        status="REJECTED",
                        direction=_dir,
                        price=current_price,
                        confidence=float(signal.get("confidence", 0.0)),
                        gate="PRE_GATE_ORIENTATION",
                        reason=_inv_reason,
                    )
                    continue

                # ----------------------------------------------------------
                # Pre-gate Opposing Pending Opportunity Conflict Checks
                # ----------------------------------------------------------
                _sig_dir = str(signal.get("direction", "BUY")).upper()
                _is_long = _sig_dir in ("BUY", "LONG")

                # Reject if an opposing pending opp exists with equal/higher conviction
                _opposing_conflict = False
                _conflict_opp = None

                async with self._opportunities_lock:
                    for _p_id, _p_opp in list(self.pending_opportunities.items()):
                        if _p_opp.get("symbol") == symbol:
                            _p_dir = str(_p_opp.get("direction", "BUY")).upper()
                            _p_is_long = _p_dir in ("BUY", "LONG")
                            if _is_long != _p_is_long:
                                _old_conf = float(_p_opp.get("confidence", 0.0))
                                _new_conf = float(signal.get("confidence", 0.0))
                                if _new_conf <= _old_conf:
                                    _opposing_conflict = True
                                    _conflict_opp = _p_opp
                                    break

                if _opposing_conflict and _conflict_opp:
                    _c_strat = _conflict_opp.get("strategy", "Unknown")
                    _c_dir = _conflict_opp.get("direction", "—")
                    _c_conf = float(_conflict_opp.get("confidence", 0.0))
                    _n_conf = float(signal.get("confidence", 0.0))
                    _block_reason = (
                        f"Opposing pending opportunity on {symbol} with equal/higher conviction "
                        f"({_c_strat} {_c_dir} conf={_c_conf:.2f} >= {_n_conf:.2f})"
                    )
                    logger.info("Signal rejected for %s: %s", symbol, _block_reason)
                    self._signals_rejected_count += 1
                    self._rejections_by_gate["OPPOSING_SIGNAL_CONFLICT"] = (
                        self._rejections_by_gate.get("OPPOSING_SIGNAL_CONFLICT", 0) + 1
                    )
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy=strategy_name,
                        status="REJECTED",
                        direction=_sig_dir,
                        price=current_price,
                        confidence=float(signal.get("confidence", 0.0)),
                        gate="OPPOSING_SIGNAL_CONFLICT",
                        reason=_block_reason,
                    )
                    continue

                # Run risk gates
                risk_result = await self._run_risk_gates(signal, symbol, current_price, open_positions=open_positions)
                if not risk_result.get("passed", False):
                    self._signals_rejected_count += 1
                    block_gate = risk_result.get("blocked_by") or risk_result.get("block_reason") or "RiskGate"
                    reason_msg = risk_result.get("block_reason") or f"Blocked by {block_gate}"
                    self._rejections_by_gate[block_gate] = self._rejections_by_gate.get(block_gate, 0) + 1
                    self._rejections_by_strategy[strategy_name] = self._rejections_by_strategy.get(strategy_name, 0) + 1
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy=strategy_name,
                        status="REJECTED",
                        direction=signal.get("direction", "—"),
                        price=current_price,
                        confidence=float(signal.get("confidence", 0.0)),
                        gate=block_gate,
                        reason=reason_msg,
                    )
                    logger.info(
                        "Signal from %s on %s blocked by risk: %s",
                        strategy_name, symbol, reason_msg,
                    )
                    continue

                self._signals_passed_count += 1
                self._record_telemetry_event(
                    symbol=symbol,
                    strategy=strategy_name,
                    status="PASSED",
                    direction=signal.get("direction", "—"),
                    price=current_price,
                    confidence=float(signal.get("confidence", 0.0)),
                    gate="ALL_GATES_PASSED",
                    reason="Passed all risk gates - opportunity created",
                )

                # ----------------------------------------------------------
                # SHADOW MODE (Phase 1): strategies listed in
                # strategy_shadow_mode (default: TRS) are scanned and
                # risk-gated like any other, and their signals are RECORDED
                # with live outcome tracking — but they NEVER create
                # opportunities or orders. This builds a real signal track
                # record before capital is committed.
                # ----------------------------------------------------------
                if strategy_name.upper() in self.shadow_strategies:
                    try:
                        sig_obj = await repo.create_signal(
                            symbol=symbol,
                            direction=signal.get("direction", "LONG"),
                            strategy=strategy_name,
                            confidence=signal.get("confidence", 0),
                            entry_price=signal.get("entry_price", current_price),
                            stop_loss=signal.get("sl_price", 0),
                            target=signal.get("target_price", 0),
                            risk_reward=signal.get("risk_reward"),
                            status="SHADOW",
                            signal_data=signal,
                            risk_gate_results=risk_result.get("all_gates", []),
                            session_id=self.session_id,
                            regime_at_signal=self.current_regime,
                            vix_at_signal=self.vix,
                        )
                        if sig_obj is not None:
                            self._shadow_signals[sig_obj.id] = {
                                "signal_id": sig_obj.id,
                                "symbol": symbol,
                                "direction": signal.get("direction", "LONG"),
                                "strategy": strategy_name,
                                "entry_price": float(signal.get("entry_price") or current_price),
                                "stop_loss": float(signal.get("sl_price") or 0.0),
                                "target": float(signal.get("target_price") or 0.0),
                                "created_at": sig_obj.created_at,
                                "signal_data": signal,
                            }
                    except Exception as shadow_rec_err:
                        logger.warning(
                            "Shadow signal recording failed for %s/%s: %s",
                            strategy_name, symbol, shadow_rec_err,
                        )
                    self._record_telemetry_event(
                        symbol=symbol,
                        strategy=strategy_name,
                        status="SHADOW_PASSED",
                        direction=signal.get("direction", "—"),
                        price=current_price,
                        confidence=float(signal.get("confidence", 0.0)),
                        gate="SHADOW_MODE",
                        reason="Passed all gates — recorded as SHADOW signal (no order will be placed)",
                    )
                    await self._broadcast("shadow_signal", {
                        "type": "shadow_signal",
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "direction": signal.get("direction", "—"),
                        "entry_price": signal.get("entry_price", current_price),
                        "stop_loss": signal.get("sl_price", 0),
                        "target": signal.get("target_price", 0),
                        "confidence": signal.get("confidence", 0),
                        "note": f"{strategy_name} runs in shadow mode — signal recorded, no order placed",
                    })
                    logger.info(
                        "SHADOW signal from %s on %s: %s @ %.2f (recorded, not traded)",
                        strategy_name, symbol, signal.get("direction", "?"),
                        signal.get("entry_price", 0),
                    )
                    continue

                # ----------------------------------------------------------
                # Invalidate any older weaker opposing pending opportunity
                # ----------------------------------------------------------
                _opposing_opp_id = None
                async with self._opportunities_lock:
                    for _p_id, _p_opp in list(self.pending_opportunities.items()):
                        if _p_opp.get("symbol") == symbol:
                            _p_dir = str(_p_opp.get("direction", "BUY")).upper()
                            _p_is_long = _p_dir in ("BUY", "LONG")
                            if _is_long != _p_is_long:
                                _old_conf = float(_p_opp.get("confidence", 0.0))
                                _new_conf = float(signal.get("confidence", 0.0))
                                if _new_conf > _old_conf:
                                    _opposing_opp_id = _p_id
                                    del self.pending_opportunities[_p_id]
                                    self.invalidated_opportunities[_p_id] = {
                                        **_p_opp,
                                        "invalidated_at": datetime.now(IST).isoformat(),
                                        "invalidation_code": "OPPOSING_SIGNAL_SUPERSEDED",
                                        "invalidation_reason": (
                                            f"Superseded by higher conviction opposing signal "
                                            f"({strategy_name} {_sig_dir} conf={_new_conf:.2f} > "
                                            f"{_p_opp.get('strategy')} {_p_dir} conf={_old_conf:.2f})"
                                        ),
                                    }
                                break

                if _opposing_opp_id is not None:
                    # Broadcast invalidation event
                    await self._broadcast("opportunity", {
                        "type": "opportunity_invalidated",
                        "opportunity_id": _opposing_opp_id,
                        "reason": self.invalidated_opportunities[_opposing_opp_id].get("invalidation_reason"),
                        "invalidation_code": "OPPOSING_SIGNAL_SUPERSEDED",
                    })
                    # CORRECTION (live-market validation run 2, 2026-08-28):
                    # every OTHER invalidation path resolves the linked
                    # signal in the DB (status=EXPIRED, see
                    # _validate_pending_opportunities) — this path did not,
                    # so the superseded signal stayed 'pending' forever
                    # (observed live: MRF BUY 11:33 IST orphaned when a
                    # higher-conviction SIC SELL superseded its opportunity
                    # 44ms later). Resolve it the same way.
                    _superseded_opp = self.invalidated_opportunities.get(_opposing_opp_id, {})
                    _superseded_sig_id = _superseded_opp.get("signal_id")
                    if _superseded_sig_id:
                        try:
                            await repo.update_signal(
                                _superseded_sig_id,
                                status="EXPIRED",
                                # Signal has no `notes` column — the honest
                                # reason goes to rejection_reason (the same
                                # field the other expiry paths should use).
                                rejection_reason=(
                                    "Superseded by higher-conviction opposing signal "
                                    "before entry — opportunity invalidated"
                                ),
                            )
                        except Exception as _superseded_sig_err:
                            logger.warning(
                                "Could not expire superseded signal %s in DB: %s",
                                _superseded_sig_id, _superseded_sig_err,
                            )

                # Calculate position size
                sizing = await self._calculate_position_size(signal, current_price, segment="EQ")

                # ----------------------------------------------------------
                # CORRECTION (live-market validation run 2, 2026-08-28):
                # G17_CostPreCheck runs BEFORE the sizer and estimates the
                # fee/risk ratio at the FULL hard-risk budget quantity.
                # The sizer, however, is Kelly-based and often allocates
                # far less (observed live: BHARTIARTL budget implied ~796
                # shares → G17 passed at ~9% fee/risk; the sizer gave 21
                # shares where flat brokerage + turnover fees = 46.65% of
                # the ACTUAL ₹131.88 risk). Re-check the ceiling at the
                # real order size — the trade then needs the full target
                # move + ~47% of its risk just to break even, which is
                # exactly the "classic intraday cost trap" G17 exists to
                # block. Reject honestly at the gate's own threshold.
                # ----------------------------------------------------------
                try:
                    _actual_qty = int((sizing or {}).get("quantity") or 0)
                    _sl_raw = float(signal.get("sl_price") or 0.0)
                    _entry_for_fee = float(signal.get("entry_price") or current_price or 0.0)
                    if _actual_qty > 0 and _sl_raw > 0 and _entry_for_fee > 0:
                        _sl_dist_actual = abs(_entry_for_fee - _sl_raw)
                        if _sl_dist_actual > 0:
                            from fees.nse_fee_calculator import NSEFeeCalculator

                            _fees_cfg_actual = (
                                self.config.get_fees_config()
                                if hasattr(self, "config") and self.config is not None
                                else {}
                            ) or {}
                            _brokerage_actual = float(_fees_cfg_actual.get("brokerage_per_order", 20.0))
                            _fee_bd_actual = NSEFeeCalculator(
                                brokerage_per_order=_brokerage_actual
                            ).calculate_equity_intraday(
                                buy_price=_entry_for_fee,
                                sell_price=_entry_for_fee,
                                quantity=_actual_qty,
                                brokerage_per_order=_brokerage_actual,
                            )
                            _fees_actual = float(_fee_bd_actual.get("total", 0.0))
                            _risk_actual = _sl_dist_actual * _actual_qty
                            if _risk_actual > 0 and _fees_actual > 0:
                                _fee_pct_actual = _fees_actual / _risk_actual * 100.0
                                _max_fee_pct_cfg = (
                                    self.config.get_risk_config()
                                    if hasattr(self, "config") and self.config is not None
                                    else {}
                                ) or {}
                                _max_fee_pct_actual = float(_max_fee_pct_cfg.get("max_fee_pct_of_risk", 30.0))
                                if _fee_pct_actual > _max_fee_pct_actual:
                                    _cost_reason = (
                                        f"Actual-size cost re-check: round-trip costs ₹{_fees_actual:,.0f} = "
                                        f"{_fee_pct_actual:.1f}% of the real ₹{_risk_actual:,.0f} risk at the "
                                        f"sized quantity ({_actual_qty}) — above the "
                                        f"{_max_fee_pct_actual:.0f}% ceiling (G17 pre-check passed only at "
                                        f"the full-budget estimate). Trade needs an oversized move to break even."
                                    )
                                    logger.info(
                                        "Signal from %s on %s rejected by actual-size cost re-check: %s",
                                        strategy_name, symbol, _cost_reason,
                                    )
                                    # Truthful accounting: it passed the 19 gates but
                                    # failed post-sizing validation.
                                    if self._signals_passed_count > 0:
                                        self._signals_passed_count -= 1
                                    self._signals_rejected_count += 1
                                    self._rejections_by_gate["G17_CostPreCheck"] = (
                                        self._rejections_by_gate.get("G17_CostPreCheck", 0) + 1
                                    )
                                    self._rejections_by_strategy[strategy_name] = (
                                        self._rejections_by_strategy.get(strategy_name, 0) + 1
                                    )
                                    self._record_telemetry_event(
                                        symbol=symbol,
                                        strategy=strategy_name,
                                        status="REJECTED",
                                        direction=signal.get("direction", "—"),
                                        price=current_price,
                                        confidence=float(signal.get("confidence", 0.0)),
                                        gate="G17_CostPreCheck",
                                        reason=_cost_reason,
                                    )
                                    continue

                                # -------------------------------------------------
                                # v0.4.9 wave-4: G19 fee-aware minimum-move
                                # re-check at the ACTUAL sized quantity. Only
                                # bites when risk.g19_mode == "enforce"; in the
                                # default log_only mode it shadow-logs the
                                # verdict so live days build the evidence base
                                # before enforcement is switched on. (The G19
                                # gate itself already ran pre-sizer at the
                                # budget quantity — see risk_engine.)
                                # -------------------------------------------------
                                _g19_mode = str(_max_fee_pct_cfg.get("g19_mode", "log_only")).strip().lower()
                                _g19_min_multiple = float(_max_fee_pct_cfg.get("min_move_fee_multiple", 2.0))
                                _target_for_g19 = float(signal.get("target_price") or 0.0)
                                _reward_actual = (
                                    abs(_target_for_g19 - _entry_for_fee) * _actual_qty
                                    if _target_for_g19 > 0 else 0.0
                                )
                                if _fees_actual > 0 and _reward_actual > 0:
                                    _g19_multiple_actual = _reward_actual / _fees_actual
                                    if _g19_multiple_actual < _g19_min_multiple:
                                        if _g19_mode == "enforce":
                                            _g19_reason = (
                                                f"G19 minimum-move: target move ₹{_reward_actual:,.0f} is only "
                                                f"{_g19_multiple_actual:.2f}× the round-trip costs "
                                                f"₹{_fees_actual:,.0f} at the sized quantity "
                                                f"({_actual_qty}) — below the {_g19_min_multiple:.1f}× "
                                                f"minimum; fees would eat most of the reward."
                                            )
                                            logger.info(
                                                "Signal from %s on %s rejected by actual-size G19 re-check: %s",
                                                strategy_name, symbol, _g19_reason,
                                            )
                                            if self._signals_passed_count > 0:
                                                self._signals_passed_count -= 1
                                            self._signals_rejected_count += 1
                                            self._rejections_by_gate["G19_MinMove"] = (
                                                self._rejections_by_gate.get("G19_MinMove", 0) + 1
                                            )
                                            self._rejections_by_strategy[strategy_name] = (
                                                self._rejections_by_strategy.get(strategy_name, 0) + 1
                                            )
                                            self._record_telemetry_event(
                                                symbol=symbol,
                                                strategy=strategy_name,
                                                status="REJECTED",
                                                direction=signal.get("direction", "—"),
                                                price=current_price,
                                                confidence=float(signal.get("confidence", 0.0)),
                                                gate="G19_MinMove",
                                                reason=_g19_reason,
                                            )
                                            continue
                                        # log_only shadow (default): observe, never block.
                                        logger.info(
                                            "G19 SHADOW (actual size): %s/%s move multiple %.2f× < %.1f× "
                                            "(reward ₹%.0f vs fees ₹%.0f, qty %d) — would block in enforce mode.",
                                            strategy_name, symbol, _g19_multiple_actual,
                                            _g19_min_multiple, _reward_actual, _fees_actual, _actual_qty,
                                        )
                except Exception as _cost_check_exc:
                    # A calculator bug must never block trading (same policy as G17).
                    logger.warning(
                        "Actual-size cost re-check skipped for %s/%s: %s",
                        strategy_name, symbol, _cost_check_exc,
                    )

                # Save signal to DB
                # Strategies emit sl_price / target_price — use those canonical keys.
                sig_obj = await repo.create_signal(
                    symbol=symbol,
                    direction=signal.get("direction", "LONG"),
                    strategy=strategy_name,
                    confidence=signal.get("confidence", 0),
                    entry_price=signal.get("entry_price", current_price),
                    stop_loss=signal.get("sl_price", 0),
                    target=signal.get("target_price", 0),
                    status="pending",
                    signal_data=signal,
                    risk_gate_results=risk_result.get("all_gates", []),
                    session_id=self.session_id,
                )
                sig_id = sig_obj.id if sig_obj and hasattr(sig_obj, "id") else str(uuid.uuid4())

                # Build opportunity
                opportunity = self._build_opportunity(
                    signal, strategy_name, symbol, current_price, sizing, risk_result, signal_id=sig_id
                )

                # Store in pending
                opp_id = opportunity["id"]
                async with self._opportunities_lock:
                    self.pending_opportunities[opp_id] = opportunity

                # Push to WebSocket
                await self._broadcast("opportunity", {
                    "type": "new_opportunity",
                    "opportunity": opportunity,
                })

                # v0.4.8 P1: Telegram ping for the human-in-the-loop flow.
                # Execution is confirm-only by design, so a pending
                # opportunity is INVISIBLE outside the dashboard — live
                # session 2026-09-01 lost EICHERMOT to TTL expiry while the
                # confirmation daemon was down. Ping on creation.
                await self._route_alert("opportunity_created", {
                    "opportunity_id": opp_id,
                    "symbol": opportunity.get("symbol", ""),
                    "direction": opportunity.get("direction", ""),
                    "strategy": opportunity.get("strategy", ""),
                    "entry_price": opportunity.get("entry_price", 0.0),
                    "stop_loss": opportunity.get("stop_loss", 0.0),
                    "target": opportunity.get("target", 0.0),
                    "confidence": opportunity.get("confidence", 0.0),
                    "ttl_seconds": opportunity.get("ttl_seconds", 360),
                })

            except Exception as strat_exc:
                self._errors_count += 1
                logger.warning(
                    "Error running strategy %s on %s: %s",
                    strategy_name, symbol, strat_exc,
                    exc_info=True,
                )
                self._record_telemetry_event(
                    symbol=symbol,
                    strategy=strategy_name,
                    status="ERROR",
                    price=current_price,
                    gate="STRATEGY_EXCEPTION",
                    reason=f"Scan error: {strat_exc}",
                )

    def _scan_strategy_list(self) -> List[str]:
        """Strategies to scan this cycle: active (trading) + shadow-tracked.

        Shadow strategies are appended in every regime — their signals are
        diverted to the SHADOW ledger by the per-signal check in
        _scan_symbol, so they never create opportunities or orders even when
        a name collides with the active list. Deduplicated, order-stable.
        """
        combined = list(self.active_strategies or [])
        seen = {str(s).upper() for s in combined}
        # Defensive getattr: test stubs (MagicMock spec) and older session
        # recovery paths may not carry the attribute — degrade to active-only.
        shadow_scan = getattr(self, "_shadow_scan_strategies", None) or []
        for name in shadow_scan:
            if name and str(name).upper() not in seen:
                combined.append(name)
                seen.add(str(name).upper())
        return combined

    async def _execute_strategy_scan(
        self,
        symbol: str,
        candles: list,
        strategy_name: str,
        regime: str,
        vix: float,
    ) -> Optional[dict]:
        """Execute a strategy's scan method on incoming candle data."""
        if not hasattr(self, "strategy_registry") or not self.strategy_registry:
            return None
        strat = self.strategy_registry.get(strategy_name)
        if not strat:
            return None

        try:
            import inspect
            from utils.candle_utils import candles_to_dataframe

            df_candles = candles_to_dataframe(candles)

            res = None
            if hasattr(strat, "scan"):
                if inspect.iscoroutinefunction(strat.scan):
                    res = await strat.scan(symbol=symbol, candles=df_candles, regime=regime, vix=vix)
                else:
                    res = strat.scan(symbol=symbol, candles=df_candles, regime=regime, vix=vix)
            elif hasattr(strat, "generate_signals"):
                if inspect.iscoroutinefunction(strat.generate_signals):
                    res = await strat.generate_signals(candles=df_candles, symbol=symbol)
                else:
                    res = strat.generate_signals(candles=df_candles, symbol=symbol)

            if res and isinstance(res, list) and len(res) > 0:
                res = res[0]
            if res and isinstance(res, dict):
                res.setdefault("strategy", strategy_name)
                res.setdefault("symbol", symbol)
                return res
        except Exception as scan_err:
            logger.warning("Strategy %s scan exception on %s: %s", strategy_name, symbol, scan_err, exc_info=True)
            raise scan_err
        return None

    # ------------------------------------------------------------------
    # Risk Gates
    # ------------------------------------------------------------------

    def _regime_to_trend(self) -> str:
        """Map the engine's live market regime onto G16's trend vocabulary.

        WIRING CONTRACT (v0.4.3 fix, audit claim #3): G16MultiTimeframe reads
        ``context["trend"]`` (fallbacks: ``nifty_trend``, then ``regime``).
        Before this fix NO production code ever populated those keys, so G16
        ran in permanent "neutral" mode — its counter-trend protection
        (BUY-in-bear / SELL-in-bull blocks) could never fire in live trading.
        The engine now supplies "trend" on every risk context, derived from
        the live regime ("Bull" / "Bear" / "Sideways" / "Volatile").

        "Volatile" maps to "neutral": volatility is a state, not a
        direction, and neutral triggers G16's STRICTEST branch (breakout /
        momentum / trend setups require confidence >= 0.60) instead of
        silently passing every check.
        """
        r = str(self.current_regime or "").strip().lower()
        if r in ("bull", "bullish", "up"):
            return "bull"
        if r in ("bear", "bearish", "down"):
            return "bear"
        return "neutral"  # sideways / volatile / unknown → strictest branch

    async def _build_risk_context(
        self, signal: dict, symbol: str, current_price: float, open_positions: Optional[list] = None
    ) -> dict:
        """Assemble all 12+ context parameters required by Risk Gates G1-G16."""
        if open_positions is None:
            open_positions = []
            async with self._repo_context() as repo:
                if repo:
                    open_positions = await repo.get_open_positions()

        # Group positions by sector for G2 / G6
        positions_by_sector: Dict[str, int] = {}
        for pos in open_positions:
            sec = get_stock_sector(pos.symbol)
            positions_by_sector[sec] = positions_by_sector.get(sec, 0) + 1

        # HOTFIX #7: pass live position data into the daily-risk snapshot so
        # any consumer of daily_status sees true open count / capital usage.
        _capital_in_use_ctx = sum(
            float(getattr(p, "entry_price", 0.0) or 0.0)
            * float(getattr(p, "remaining_qty", getattr(p, "quantity", 0)) or 0)
            for p in open_positions
        )
        daily_status = (
            self.daily_risk.check_daily_limits(
                open_positions_count=len(open_positions),
                capital_in_use=_capital_in_use_ctx,
            )
            if self.daily_risk
            else None
        )
        daily_loss = abs(daily_status.net_pnl) if daily_status and daily_status.net_pnl < 0 else 0.0
        daily_pnl = float(daily_status.net_pnl) if daily_status else -daily_loss
        daily_trades = daily_status.total_trades if daily_status else 0
        consecutive_losses = daily_status.consecutive_losses if daily_status else 0

        margin_avail = resolve_total_capital(engine=self)
        if self.broker and hasattr(self.broker, "get_margins"):
            try:
                margins = await self.broker.get_margins()
                margin_avail = float(margins.get("available_cash", margin_avail))
            except Exception:
                pass

        total_cap = resolve_total_capital(engine=self)
        drawdown_pct = (daily_loss / total_cap) * 100.0 if total_cap > 0 else 0.0
        open_syms = [pos.symbol for pos in open_positions]

        # Real per-strategy performance stats for G14 — computed LIVE from the
        # trades ledger (status=CLOSED). No fabricated defaults and NO reads
        # from the legacy strategy_performance cache: win rates come only
        # from real executed trades.
        strategy_stats: Optional[dict] = None
        strategy_name = ""
        try:
            _sraw = signal.get("strategy") if isinstance(signal, dict) else None
            strategy_name = str(_sraw or "").strip()
        except Exception:
            strategy_name = ""

        # Per-strategy guard inputs for G18 (daily P&L + consecutive losses)
        strategy_daily_pnl: Optional[float] = None
        strategy_consecutive_losses: int = 0
        strategy_last_loss_at: Optional[str] = None

        if strategy_name:
            try:
                async with self._repo_context() as repo:
                    if repo:
                        computed = await repo.compute_strategy_stats(strategy_name)
                        if computed and int(computed.get("total_trades", 0) or 0) > 0:
                            strategy_stats = {
                                "win_rate": float(computed.get("win_rate", 0.0)),
                                "profit_factor": float(computed.get("profit_factor", 0.0)),
                                "total_trades": int(computed.get("total_trades", 0) or 0),
                                "sharpe": 0.0,  # not fabricated; computed only where meaningful
                                "source": "trades_ledger",
                            }
                        today_trades = await repo.get_today_closed_trades_by_strategy(strategy_name)
                        if today_trades:
                            strategy_daily_pnl = round(
                                sum(float(t.net_pnl or 0.0) for t in today_trades), 2
                            )
                            _consec = 0
                            for t in reversed(today_trades):
                                if float(t.net_pnl or 0.0) < 0:
                                    _consec += 1
                                else:
                                    break
                            strategy_consecutive_losses = _consec
                            for t in reversed(today_trades):
                                if float(t.net_pnl or 0.0) < 0:
                                    strategy_last_loss_at = t.exit_time
                                    break
            except Exception as stats_err:
                logger.debug("Could not load strategy stats for %s: %s", strategy_name, stats_err)

        return {
            "symbol": symbol,
            "current_price": current_price,
            "ltp": current_price,
            "broker_ltp": current_price,
            "vix": self.vix,
            "india_vix": self.vix,
            "regime": self.current_regime,
            # G16 wiring (v0.4.3): canonical higher-timeframe trend key read
            # by G16MultiTimeframe — derived from the live regime via
            # _regime_to_trend(). nifty_trend kept as an alias for backward
            # compatibility with older gate versions / custom callers.
            "trend": self._regime_to_trend(),
            "nifty_trend": self._regime_to_trend(),
            "open_positions": len(open_positions),
            "open_positions_count": len(open_positions),
            "open_position_symbols": open_syms,
            "open_positions_list": open_syms,
            "positions_by_sector": positions_by_sector,
            "daily_loss": daily_loss,
            "daily_loss_rupees": daily_loss,
            "daily_pnl": daily_pnl,
            "daily_trades": daily_trades,
            "daily_trade_count": daily_trades,
            "consecutive_losses": consecutive_losses,
            "current_drawdown_pct": drawdown_pct,
            "capital": total_cap,
            "total_capital": total_cap,
            "margin_available": margin_avail,
            "available_capital": margin_avail,
            "available_margin": margin_avail,
            "session_id": self.session_id,
            "current_time": datetime.now(IST),
            "time_of_day": datetime.now(IST).strftime("%H:%M"),
            "strategy_stats": strategy_stats,
            "strategy_daily_pnl": strategy_daily_pnl,
            "strategy_consecutive_losses": strategy_consecutive_losses,
            "strategy_last_loss_at": strategy_last_loss_at,
        }

    async def _run_risk_gates(
        self, signal: dict, symbol: str, current_price: float, open_positions: Optional[list] = None
    ) -> dict:
        """Run all risk gates on a signal.

        Returns dict with: passed, all_gates, blocked_by, block_reason, severity,
        reduced_size, notes.
        """
        try:
            context = await self._build_risk_context(signal, symbol, current_price, open_positions=open_positions)
            risk_result = await self.risk_engine.evaluate(
                signal=signal,
                symbol=symbol,
                current_price=current_price,
                context=context,
                session_id=self.session_id,
            )
            if hasattr(risk_result, "model_dump"):
                return risk_result.model_dump()
            if isinstance(risk_result, dict):
                return risk_result
            return {"passed": False, "block_reason": "Unknown risk result type", "all_gates": []}
        except Exception as exc:
            logger.error("Risk gate evaluation failed: %s", exc)
            return {"passed": False, "block_reason": f"Risk engine error: {exc}", "all_gates": [], "severity": "error"}

    # ------------------------------------------------------------------
    # Position Sizing
    # ------------------------------------------------------------------

    async def _calculate_position_size(self, signal: dict, current_price: float, segment: str = "EQ") -> dict:
        """Calculate position size for a signal.

        The segment MUST be threaded through to the PositionSizer: without it
        the sizer falls back to is_fno_stock(symbol), and equity trades on
        F&O-listed underlyings (e.g. RELIANCE) get lot-constrained to 0 lots
        because a Kelly allocation cannot buy a whole futures lot.
        """
        try:
            res = self.position_sizer.calculate(
                signal=signal,
                context={
                    "current_price": current_price,
                    "regime": self.current_regime,
                    "vix": self.vix,
                    "session_id": self.session_id,
                    "segment": str(segment or "EQ").upper(),
                    "available_capital": getattr(self, "available_capital", resolve_total_capital(engine=self)),
                },
                current_price=current_price,
                regime=self.current_regime,
                vix=self.vix,
                session_id=self.session_id,
            )
            if asyncio.iscoroutine(res):
                res = await res
            if hasattr(res, "model_dump"):
                return res.model_dump()
            if isinstance(res, dict):
                return res
            return {"quantity": 0, "position_size": 0, "method": "unknown"}
        except Exception as exc:
            logger.error("Position sizing failed: %s", exc)
            return {"quantity": 0, "position_size": 0, "method": "error", "notes": str(exc)}

    # ------------------------------------------------------------------
    # Order routing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_order_exchange(segment: str = "EQ") -> str:
        """Map the trading segment to the broker exchange code.

        'FNO'/'FUT'/'OPT' route to NFO; everything else is cash equity on NSE.
        """
        return "NFO" if str(segment or "").upper() in ("FNO", "FUT", "OPT", "FUTURES", "OPTIONS") else "NSE"

    @staticmethod
    def _position_segment(position) -> str:
        """Read the segment a DB position was opened on (extra JSON), defaulting to EQ."""
        extra = getattr(position, "extra", None)
        if isinstance(extra, dict):
            seg = extra.get("segment")
            return str(seg) if seg else "EQ"
        if isinstance(extra, str) and extra:
            try:
                parsed = json.loads(extra)
                if isinstance(parsed, dict) and parsed.get("segment"):
                    return str(parsed["segment"])
            except Exception:
                pass
        return "EQ"

    @staticmethod
    def _position_extra_dict(position) -> Dict[str, Any]:
        """Parse a position's extra JSON into a dict ({} on any failure)."""
        extra = getattr(position, "extra", None)
        if isinstance(extra, dict):
            return extra
        if isinstance(extra, str) and extra:
            try:
                parsed = json.loads(extra)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    def _time_stop_for(self, strategy: str) -> float:
        """Configured time-stop (minutes) for a strategy (0 disables)."""
        if not self._time_stop_map and self._time_stop_default <= 0:
            return 0.0
        return float(self._time_stop_map.get(str(strategy or "").upper(), self._time_stop_default))

    @staticmethod
    def _position_age_minutes(position) -> Optional[float]:
        """Minutes since the position was opened (None if unknowable)."""
        entry_time = getattr(position, "entry_time", None)
        if not entry_time:
            return None
        try:
            entry_dt = datetime.fromisoformat(str(entry_time))
            now = datetime.now(IST)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=IST)
            return max(0.0, (now - entry_dt).total_seconds() / 60.0)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Continuous Opportunity Validation
    # ------------------------------------------------------------------

    async def _expire_orphaned_pending_signals(self) -> int:
        """Resolve signals stuck at status 'pending' from previous runs.

        Pending opportunities live ONLY in engine memory (the session
        state persists just their IDs). When the process restarts
        mid-day, those opportunities die silently and their DB signals
        stay 'pending' forever — corrupting the signal ledger with
        entries that can never resolve. Called once from start(), where
        ``pending_opportunities`` is by definition empty, so every
        remaining 'pending' signal is an orphan by construction.

        Returns the number of signals expired.
        """
        expired = 0
        try:
            async with self._repo_context() as repo:
                if repo is None or not hasattr(repo, "get_signals_by_status"):
                    return 0
                orphan_ids: list = []
                # create_signal writes lowercase 'pending'; be defensive
                # about casing in case anything ever wrote 'PENDING'.
                for status_val in ("pending", "PENDING"):
                    try:
                        for sig in await repo.get_signals_by_status(status_val):
                            if getattr(sig, "id", None) and sig.id not in orphan_ids:
                                orphan_ids.append(sig.id)
                    except Exception:
                        continue
                for sig_id in orphan_ids:
                    try:
                        await repo.update_signal(
                            sig_id,
                            status="EXPIRED",
                            rejection_reason=(
                                "Pending opportunity lost on engine restart — "
                                "signal expired unresolved"
                            ),
                        )
                        expired += 1
                    except Exception as sig_err:
                        logger.warning(
                            "Could not expire orphaned pending signal %s: %s",
                            sig_id, sig_err,
                        )
        except Exception as exc:
            logger.warning("Could not sweep orphaned pending signals: %s", exc, exc_info=True)
        if expired:
            logger.info(
                "Expired %d orphaned pending signal(s) left by previous engine run(s)",
                expired,
            )
        return expired

    async def _validate_pending_opportunities(self) -> None:
        """Validate pending opportunities continuously against live price action and TTL.
        
        Prunes opportunities if:
        1. Target reached before entry (move finished — prevents buying top / selling bottom)
        2. Stop-loss breached before entry (support broken — setup failed)
        3. Price drift exceeds maximum slippage tolerance (unfavorable Risk-Reward)
        4. Setup timeout expired (momentum setup older than TTL, e.g. 15 minutes)
        """
        async with self._opportunities_lock:
            if not self.pending_opportunities:
                return
            items_snapshot = list(self.pending_opportunities.items())

        now = datetime.now(IST)
        risk_config = self.config.get_risk_config() if hasattr(self.config, "get_risk_config") else {}
        mismatch_threshold = risk_config.get("price_mismatch_threshold_pct", 0.6)
        strategy_ttl_map = risk_config.get("strategy_ttl_seconds", {})
        default_ttl_seconds = float(risk_config.get("opportunity_ttl_seconds", risk_config.get("opportunity_ttl_minutes", 5) * 60))

        invalidated_items: List[tuple] = []

        # Check 0: Market Hours Check — If market closed, all intraday pending setups expire
        if self.market_hours and not self.market_hours.is_market_open():
            for opp_id, _ in items_snapshot:
                invalidated_items.append((
                    opp_id,
                    "MARKET_SESSION_CLOSED",
                    "Market session is closed (09:15 - 15:30 IST) — Intraday setup expired with market close to prevent overnight risk"
                ))

        for opp_id, opp in items_snapshot:
            if any(item[0] == opp_id for item in invalidated_items):
                continue

            symbol = opp.get("symbol", "")
            direction = opp.get("direction", "BUY").upper()
            strategy = opp.get("strategy", "")
            entry_price = float(opp.get("entry_price", 0.0))
            stop_loss = float(opp.get("stop_loss", 0.0))
            target = float(opp.get("target", 0.0))
            created_at_str = opp.get("created_at")

            # Check 1: Strategy-Aware TTL Expiry (Momentum window)
            strat_upper = (strategy or "").upper()
            strat_ttl = float(strategy_ttl_map.get(strat_upper, default_ttl_seconds))
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str)
                    age_seconds = (now - created_at).total_seconds()
                    if age_seconds > strat_ttl:
                        invalidated_items.append((
                            opp_id,
                            "SETUP_TIMEOUT_EXPIRED",
                            f"Opportunity expired after {int(strat_ttl)}s without execution (momentum window closed for {strategy})"
                        ))
                        continue
                except Exception:
                    pass

            # Check 2: Live Price Query
            current_price = 0.0
            if self.feed is not None and hasattr(self.feed, "get_latest_price"):
                try:
                    current_price = await self.feed.get_latest_price(symbol)
                except Exception:
                    current_price = 0.0

            if current_price <= 0 and self.broker is not None and hasattr(self.broker, "get_latest_price"):
                try:
                    current_price = await self.broker.get_latest_price(symbol)
                except Exception:
                    current_price = 0.0

            if current_price <= 0:
                continue

            # Update live metrics
            opp["current_price"] = current_price
            price_mismatch_pct = abs(current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            opp["price_mismatch_pct"] = round(price_mismatch_pct, 2)

            # Check 3: Target Hit / Move Already Completed (Chasing Block)
            if direction in ("BUY", "LONG"):
                if target > 0 and current_price >= target:
                    invalidated_items.append((
                        opp_id,
                        "TARGET_ACHIEVED_BEFORE_ENTRY",
                        f"Target ₹{target:.2f} reached before entry (LTP: ₹{current_price:.2f}). Move finished — invalidated to prevent buying top."
                    ))
                    continue
                elif stop_loss > 0 and current_price <= stop_loss:
                    invalidated_items.append((
                        opp_id,
                        "STOP_LOSS_BREACHED",
                        f"Stop loss ₹{stop_loss:.2f} breached before entry (LTP: ₹{current_price:.2f}). Setup invalidated to prevent buying falling knife."
                    ))
                    continue
                elif target > 0 and stop_loss > 0:
                    remaining_gain = target - current_price
                    remaining_risk = current_price - stop_loss
                    if remaining_gain > 0 and remaining_risk > 0:
                        live_rr = remaining_gain / remaining_risk
                        if live_rr < 0.8:
                            invalidated_items.append((
                                opp_id,
                                "UNFAVORABLE_RISK_REWARD",
                                f"Risk-Reward deteriorated to 1:{live_rr:.2f} (LTP ₹{current_price:.2f} moved too close to target). Profit potential exhausted."
                            ))
                            continue
            elif direction in ("SELL", "SHORT"):
                if target > 0 and current_price <= target:
                    invalidated_items.append((
                        opp_id,
                        "TARGET_ACHIEVED_BEFORE_ENTRY",
                        f"Target ₹{target:.2f} reached before entry (LTP: ₹{current_price:.2f}). Move finished — invalidated to prevent selling bottom."
                    ))
                    continue
                elif stop_loss > 0 and current_price >= stop_loss:
                    invalidated_items.append((
                        opp_id,
                        "STOP_LOSS_BREACHED",
                        f"Stop loss ₹{stop_loss:.2f} breached before entry (LTP: ₹{current_price:.2f}). Setup invalidated."
                    ))
                    continue
                elif target > 0 and stop_loss > 0:
                    remaining_gain = current_price - target
                    remaining_risk = stop_loss - current_price
                    if remaining_gain > 0 and remaining_risk > 0:
                        live_rr = remaining_gain / remaining_risk
                        if live_rr < 0.8:
                            invalidated_items.append((
                                opp_id,
                                "UNFAVORABLE_RISK_REWARD",
                                f"Risk-Reward deteriorated to 1:{live_rr:.2f} (LTP ₹{current_price:.2f} moved too close to target). Profit potential exhausted."
                            ))
                            continue

            # Check 4: Market Regime / Strategy Compatibility Check
            if self.current_regime and hasattr(self.config, "get_strategy_activation"):
                regime_cfg = self.config.get_strategy_activation(self.current_regime)
                paused_strategies = regime_cfg.get("paused", [])
                if strategy and strategy in paused_strategies:
                    invalidated_items.append((
                        opp_id,
                        "REGIME_TREND_SHIFT",
                        f"Market regime shifted to {self.current_regime}; strategy '{strategy}' paused. Setup invalidated to protect capital."
                    ))
                    continue

            # Check 5: Price Drift Slippage Tolerance
            if price_mismatch_pct > mismatch_threshold * 1.5:
                invalidated_items.append((
                    opp_id,
                    "PRICE_DRIFT_EXCEEDED",
                    f"Price drifted {price_mismatch_pct:.2f}% from entry ₹{entry_price:.2f} (exceeds {mismatch_threshold * 1.5:.2f}% limit)."
                ))
                continue

        # Prune and notify
        if invalidated_items:
            async with self._repo_context() as repo:
                for opp_id, reason_code, reason_desc in invalidated_items:
                    async with self._opportunities_lock:
                        opp = self.pending_opportunities.pop(opp_id, None)
                        if not opp:
                            continue

                        opp["status"] = "expired"
                        opp["invalidation_code"] = reason_code
                        opp["invalidation_reason"] = reason_desc
                        opp["invalidated_at"] = now.isoformat()

                        self.invalidated_opportunities[opp_id] = opp
                        if len(self.invalidated_opportunities) > 50:
                            oldest_key = next(iter(self.invalidated_opportunities))
                            self.invalidated_opportunities.pop(oldest_key, None)

                    logger.info(
                        "Invalidated opportunity %s (%s): %s - %s",
                        opp_id, opp.get("symbol"), reason_code, reason_desc
                    )

                    await self._broadcast("opportunity", {
                        "type": "opportunity_invalidated",
                        "opportunity_id": opp_id,
                        "symbol": opp.get("symbol"),
                        "reason_code": reason_code,
                        "reason": reason_desc,
                        "invalidated_at": now.isoformat(),
                    })

                    # v0.4.8 P1: mirror the lifecycle event to Telegram so
                    # the operator knows why a setup never became a trade.
                    await self._route_alert("opportunity_expired", {
                        "opportunity_id": opp_id,
                        "symbol": opp.get("symbol"),
                        "direction": opp.get("direction", ""),
                        "strategy": opp.get("strategy", ""),
                        "reason_code": reason_code,
                        "reason": reason_desc,
                    })

                    if repo is not None and opp.get("signal_id"):
                        try:
                            await repo.update_signal(
                                opp.get("signal_id"),
                                status="EXPIRED",
                                # rejection_reason (NOT notes — the Signal
                                # model has no notes column, so a notes=
                                # kwarg is silently dropped by update_signal)
                                rejection_reason=reason_desc
                            )
                        except Exception as sig_err:
                            logger.warning("Could not update signal status in DB for %s: %s", opp.get("signal_id"), sig_err, exc_info=True)

    # ------------------------------------------------------------------
    # Shadow Signal Outcome Tracking (Phase 1)
    # ------------------------------------------------------------------

    async def _evaluate_shadow_signals(self) -> None:
        """Resolve open SHADOW signals against live prices.

        Shadow signals never place orders, but their hypothetical outcomes are
        tracked HONESTLY against the same live prices real trades would face:
          - SHADOW_TARGET: price reached the signal's target
          - SHADOW_SL:     price hit the signal's stop
          - SHADOW_EXPIRED: signal aged out (or market closed) unresolved,
                            recorded at the prevailing price
        Outcomes are per-share (no quantity was ever sized) and are stored on
        the signal row. These stats stay SEPARATE from real trade win rates.
        """
        if not self._shadow_signals:
            return

        resolved: list = []
        for sig_id, sig in list(self._shadow_signals.items()):
            symbol = sig.get("symbol", "")
            entry = float(sig.get("entry_price") or 0.0)
            sl = float(sig.get("stop_loss") or 0.0)
            target = float(sig.get("target") or 0.0)
            direction = str(sig.get("direction") or "").upper()

            if entry <= 0 or sl <= 0 or target <= 0:
                # Unusable geometry — drop silently rather than fabricate an outcome
                self._shadow_signals.pop(sig_id, None)
                continue

            price = 0.0
            if self.feed is not None and hasattr(self.feed, "get_latest_price"):
                try:
                    price = await self.feed.get_latest_price(symbol)
                except Exception:
                    price = 0.0
            if price <= 0 and self.broker is not None and hasattr(self.broker, "get_latest_price"):
                try:
                    price = await self.broker.get_latest_price(symbol)
                except Exception:
                    price = 0.0
            if price <= 0:
                continue  # no live price this cycle — retry next loop

            is_long = direction in ("BUY", "LONG")
            outcome = None
            exit_price = price

            if is_long:
                if price >= target:
                    outcome, exit_price = "SHADOW_TARGET", target
                elif price <= sl:
                    outcome, exit_price = "SHADOW_SL", sl
            else:
                if price <= target:
                    outcome, exit_price = "SHADOW_TARGET", target
                elif price >= sl:
                    outcome, exit_price = "SHADOW_SL", sl

            if outcome is None:
                # Age-based expiry (mirrors the real time-stop discipline)
                try:
                    created = datetime.fromisoformat(str(sig.get("created_at")))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=IST)
                    age_min = (datetime.now(IST) - created).total_seconds() / 60.0
                except Exception:
                    age_min = None

                eod = bool(self.market_hours.is_safe_exit_time()) if self.market_hours else False
                if (age_min is not None and age_min >= self._shadow_max_age_minutes) or eod:
                    outcome = "SHADOW_EXPIRED"
                    exit_price = price

            if outcome is None:
                continue

            # Per-share hypothetical result (gross; quantity was never sized)
            if is_long:
                pnl_per_share = exit_price - entry
            else:
                pnl_per_share = entry - exit_price

            async with self._repo_context() as repo:
                if repo is not None:
                    try:
                        _existing = sig.get("signal_data") if isinstance(sig.get("signal_data"), dict) else {}
                        _updated_data = dict(_existing)
                        _updated_data["shadow_result"] = {
                            "outcome": outcome,
                            "exit_price": round(float(exit_price), 2),
                            "pnl_per_share": round(float(pnl_per_share), 2),
                            "resolved_at": datetime.now(IST).isoformat(),
                        }
                        await repo.update_signal(
                            sig_id,
                            status=outcome,
                            notes=(
                                f"Shadow outcome {outcome} at ₹{exit_price:.2f} "
                                f"(per-share P&L ₹{pnl_per_share:+.2f})"
                            ),
                            signal_data=_updated_data,
                        )
                    except Exception as sig_up_err:
                        logger.warning("Shadow signal update failed for %s: %s", sig_id, sig_up_err)

            resolved.append((sig_id, sig, outcome, exit_price, pnl_per_share))
            self._shadow_signals.pop(sig_id, None)

        for sig_id, sig, outcome, exit_price, pnl_per_share in resolved:
            await self._broadcast("shadow_signal", {
                "type": "shadow_signal_resolved",
                "signal_id": sig_id,
                "symbol": sig.get("symbol"),
                "strategy": sig.get("strategy"),
                "outcome": outcome,
                "exit_price": round(float(exit_price), 2),
                "pnl_per_share": round(float(pnl_per_share), 2),
            })

    # ------------------------------------------------------------------
    # Build Opportunity
    # ------------------------------------------------------------------

    def _build_opportunity(
        self,
        signal: dict,
        strategy_name: str,
        symbol: str,
        current_price: float,
        sizing: dict,
        risk_result: dict,
        signal_id: Optional[str] = None,
    ) -> dict:
        """Build a full opportunity dict from signal + risk + sizing."""
        entry_price = signal.get("entry_price", current_price)
        # Strategies emit sl_price / target_price — use those canonical keys.
        stop_loss = signal.get("sl_price", 0)
        target = signal.get("target_price", 0)

        # Calculate risk/reward
        # v0.4.3 (audit claim #4): route through _is_long_direction — the
        # live-run-2 correction rule is "EVERY direction branch must go
        # through the helper" (strategies emit BUY/SELL, not LONG/SHORT).
        # Behavior here was unchanged by the abs() wrapping, but one future
        # edit removing the abs() would have reactivated the inverted-sign
        # bug class for every BUY/SELL opportunity.
        sig_is_long = _is_long_direction(signal.get("direction"))
        sl_distance = entry_price - stop_loss if sig_is_long else stop_loss - entry_price
        sl_distance = abs(sl_distance)
        target_distance = target - entry_price if sig_is_long else entry_price - target
        target_distance = abs(target_distance)
        risk_reward = round(target_distance / sl_distance, 2) if sl_distance > 0 else 0.0

        # Price mismatch check
        price_mismatch_pct = abs(current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

        # Partial booking levels
        booking_levels = []
        if self.partial_booker is not None:
            try:
                booking_config = self.config.get_partial_booking_config()
                booking_levels = self.partial_booker.get_booking_levels(
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target=target,
                    direction=signal.get("direction", "LONG"),
                    config=booking_config,
                )
                if hasattr(booking_levels, "model_dump"):
                    booking_levels = booking_levels.model_dump().get("levels", [])
                elif isinstance(booking_levels, list):
                    # List of Pydantic BookingLevels models — serialize each to a
                    # plain dict so WS broadcasts / JSON responses never fail.
                    booking_levels = [
                        lvl.model_dump(mode="json") if hasattr(lvl, "model_dump") else dict(lvl)
                        for lvl in booking_levels
                    ]
                else:
                    booking_levels = []
            except Exception:
                booking_levels = []

        opportunity_id = str(uuid.uuid4())
        created_dt = datetime.now(IST)

        # Standardize 1-5 Conviction Score
        # Use explicit None-check: confidence=0.0 is a valid (low) value, not a missing one.
        # The old `signal.get("confidence", 0.6) or 0.6` pattern bumped 0.0 → 0.6.
        _raw_conf_val = signal.get("confidence")
        raw_conf = float(_raw_conf_val if _raw_conf_val is not None else 0.0)
        rr_bonus = 0.05 if risk_reward >= 2.0 else 0.0
        all_gates = risk_result.get("all_gates", [])
        passed_gates = sum(1 for g in all_gates if (isinstance(g, dict) and g.get("passed", False)) or (hasattr(g, "passed") and getattr(g, "passed", False)))
        gate_ratio = (passed_gates / len(all_gates)) if all_gates else 1.0
        
        composite_score = min(1.0, max(0.0, raw_conf * 0.7 + (gate_ratio * 0.2) + rr_bonus))
        conviction_stars = max(1, min(5, int(round(1 + 4 * composite_score))))
        
        conviction_labels = {
            1: "1 Star - Low Conviction",
            2: "2 Stars - Moderate Setup",
            3: "3 Stars - Standard Setup",
            4: "4 Stars - High Probability",
            5: "5 Stars - A+ Institutional Grade",
        }
        conviction_label = conviction_labels.get(conviction_stars, "3 Stars - Standard Setup")

        # Resolve strategy-aware TTL
        risk_cfg = self.config.get_risk_config() if hasattr(self.config, "get_risk_config") else {}
        strategy_ttl_map = risk_cfg.get("strategy_ttl_seconds", {})
        default_ttl_seconds = float(risk_cfg.get("opportunity_ttl_seconds", risk_cfg.get("opportunity_ttl_minutes", 5) * 60))
        strat_upper = (strategy_name or "").upper()
        strat_ttl = int(strategy_ttl_map.get(strat_upper, default_ttl_seconds))
        expiry_dt = created_dt + timedelta(seconds=strat_ttl)

        resolved_signal_id = signal_id or signal.get("signal_id") or str(uuid.uuid4())

        # Dynamic duration estimate (ATR-velocity based — never hardcoded).
        # Uses the strategy's own ATR when available, else the SL-distance
        # proxy; both are realtime market measurements, and the basis is
        # surfaced so the UI can be honest about the estimate's quality.
        expected_duration = None
        try:
            from core.duration import estimate_trade_duration

            expected_duration = estimate_trade_duration(
                entry_price=entry_price,
                target_price=target if target and target > 0 else None,
                stop_loss=stop_loss if stop_loss and stop_loss > 0 else None,
                direction=signal.get("direction", "LONG"),
                regime=self.current_regime,
                atr=signal.get("atr"),
                now_ist=created_dt,
            )
        except Exception as dur_exc:
            logger.debug("Duration estimate unavailable for %s: %s", symbol, dur_exc)
            expected_duration = None

        # Phase 1: real round-trip cost estimate at the ACTUAL sized quantity
        # (brokerage + STT + exchange txn + GST + SEBI + stamp duty), shown
        # alongside gross risk so the cost drag is visible pre-confirmation.
        estimated_costs = None
        try:
            _qty = int(sizing.get("quantity") or 0)
            if _qty > 0 and entry_price > 0 and sl_distance > 0:
                from fees.nse_fee_calculator import NSEFeeCalculator

                _fees_cfg = self.config.get_fees_config() if hasattr(self.config, "get_fees_config") else {}
                _brokerage = float(_fees_cfg.get("brokerage_per_order", 20.0))
                _calc = NSEFeeCalculator(brokerage_per_order=_brokerage)
                _fee_bd = _calc.calculate_equity_intraday(
                    buy_price=entry_price,
                    sell_price=entry_price,
                    quantity=_qty,
                    brokerage_per_order=_brokerage,
                )
                _fees = float(_fee_bd.get("total", 0.0))
                _gross_risk = sl_distance * _qty
                estimated_costs = {
                    "round_trip_fees": round(_fees, 2),
                    "gross_risk": round(_gross_risk, 2),
                    "fee_pct_of_risk": round(_fees / _gross_risk * 100.0, 2) if _gross_risk > 0 else None,
                }
        except Exception:
            estimated_costs = None

        return {
            "id": opportunity_id,
            "signal_id": resolved_signal_id,
            "created_at": created_dt.isoformat(),
            "created_at_time": created_dt.strftime("%I:%M:%S %p"),
            "ttl_seconds": strat_ttl,
            "expiry_at": expiry_dt.isoformat(),
            "symbol": symbol,
            "name": signal.get("name", ""),
            "direction": signal.get("direction", "LONG"),
            "strategy": strategy_name,
            "confidence": signal.get("confidence", 0),
            "conviction_score": conviction_stars,
            "conviction_stars": conviction_stars,
            "conviction_label": conviction_label,
            "composite_score": round(composite_score * 100, 1),
            "conviction_breakdown": {
                "technical_confidence": round(raw_conf * 100, 1),
                "risk_gates_passed": f"{passed_gates}/{len(all_gates)}",
                "risk_reward": risk_reward,
                "composite_score": round(composite_score * 100, 1),
            },
            "entry_price": entry_price,
            "current_price": current_price,
            "stop_loss": stop_loss,
            "target": target,
            "risk_reward": risk_reward,
            "sl_distance_pct": round(sl_distance / entry_price * 100, 2) if entry_price > 0 else 0,
            "target_pct": round(target_distance / entry_price * 100, 2) if entry_price > 0 else 0,
            "price_mismatch_pct": round(price_mismatch_pct, 2),
            "quantity": sizing.get("quantity", 0),
            "position_size": sizing.get("position_size", 0),
            "position_size_pct": sizing.get("position_size_pct", 0),
            "risk_amount": sizing.get("risk_amount", 0),
            "estimated_costs": estimated_costs,
            "sizing_method": sizing.get("method", ""),
            "capital_required": sizing.get("position_size", 0),
            "kelly_fraction": sizing.get("adjusted_fraction", sizing.get("kelly_fraction")),
            "volatility_tier": sizing.get("volatility_tier", ""),
            "drawdown_tier": sizing.get("drawdown_tier", ""),
            "confidence_tier": sizing.get("confidence_tier", ""),
            "is_equity": signal.get("is_equity", True),
            "lot_size": signal.get("lot_size"),
            "expiry_date": signal.get("expiry_date"),
            "strike": signal.get("strike"),
            "option_type": signal.get("option_type"),
            "is_reduced_size": sizing.get("reduced_size", risk_result.get("reduced_size", False)),
            "risk_gate_passed": risk_result.get("passed", True),
            "risk_gates": risk_result.get("all_gates", []),
            "vix": self.vix,
            "regime": self.current_regime,
            "booking_levels": booking_levels,
            "expected_duration": expected_duration,
            "signal_data": signal,
            "kronos_score": signal.get("kronos_score"),
            "win_rate": signal.get("win_rate"),
            "avg_rr": signal.get("avg_rr"),
            "notes": risk_result.get("notes", ""),
        }

    # ------------------------------------------------------------------
    # Confirm / Skip Opportunity
    # ------------------------------------------------------------------

    async def confirm_opportunity(self, opportunity_id: str, segment: str = "EQ") -> Dict[str, Any]:
        """User confirms an opportunity – execute the trade.

        Args:
            opportunity_id: The pending opportunity ID.
            segment: Market segment ('EQ', 'FNO', etc.).

        Returns:
            Dict with trade details.
        """
        async with self._opportunities_lock:
            opportunity = self.pending_opportunities.pop(opportunity_id, None)
        if opportunity is None:
            return {"status": "not_found", "error": f"Opportunity {opportunity_id} not in pending list"}

        # --- TTL Expiry Check ---
        created_at_str = opportunity.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                age_seconds = (datetime.now(IST) - created_at).total_seconds()
                risk_config = self.config.get_risk_config() if hasattr(self.config, "get_risk_config") else {}
                ttl_seconds = risk_config.get("opportunity_ttl_seconds", 120)
                if age_seconds > ttl_seconds:
                    return {
                        "status": "rejected",
                        "reason": f"Opportunity expired after {int(ttl_seconds)}s (momentum window closed). Execution aborted to prevent stale trade.",
                    }
            except Exception:
                pass

        symbol = opportunity["symbol"]
        direction = opportunity["direction"]
        entry_price = opportunity["entry_price"]
        quantity = opportunity["quantity"]
        stop_loss = opportunity["stop_loss"]
        target = opportunity["target"]
        strategy = opportunity["strategy"]

        # --- Re-verify risk gates ---
        current_price = entry_price
        if self.feed is not None and hasattr(self.feed, "get_latest_price"):
            try:
                current_price = await self.feed.get_latest_price(symbol)
            except Exception:
                pass
        elif self.broker is not None and hasattr(self.broker, "get_latest_price"):
            try:
                current_price = await self.broker.get_latest_price(symbol)
            except Exception:
                pass

        # --- Target hit or SL breached pre-execution check ---
        dir_upper = direction.upper()
        if dir_upper in ("BUY", "LONG"):
            if target > 0 and current_price >= target:
                return {
                    "status": "rejected",
                    "reason": f"Target ₹{target:.2f} reached before execution (LTP: ₹{current_price:.2f}). Move finished — trade rejected to prevent buying top.",
                    "current_price": current_price,
                    "target": target,
                }
            if stop_loss > 0 and current_price <= stop_loss:
                return {
                    "status": "rejected",
                    "reason": f"Stop loss ₹{stop_loss:.2f} breached (LTP: ₹{current_price:.2f}). Setup invalidated.",
                    "current_price": current_price,
                    "stop_loss": stop_loss,
                }
        elif dir_upper in ("SELL", "SHORT"):
            if target > 0 and current_price <= target:
                return {
                    "status": "rejected",
                    "reason": f"Target ₹{target:.2f} reached before execution (LTP: ₹{current_price:.2f}). Move finished — trade rejected to prevent selling bottom.",
                    "current_price": current_price,
                    "target": target,
                }
            if stop_loss > 0 and current_price >= stop_loss:
                return {
                    "status": "rejected",
                    "reason": f"Stop loss ₹{stop_loss:.2f} breached (LTP: ₹{current_price:.2f}). Setup invalidated.",
                    "current_price": current_price,
                    "stop_loss": stop_loss,
                }

        # --- Price mismatch re-check ---
        price_mismatch_pct = abs(current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
        risk_config = self.config.get_risk_config()
        mismatch_threshold = risk_config.get("price_mismatch_threshold_pct", 0.5)

        if price_mismatch_pct > mismatch_threshold:
            return {
                "status": "rejected",
                "reason": f"Price mismatch too large: {price_mismatch_pct:.2f}% > {mismatch_threshold}%",
                "current_price": current_price,
                "original_entry": entry_price,
            }

        # --- Re-run risk gates ---
        signal_data = opportunity.get("signal_data", opportunity)
        signal_data["entry_price"] = current_price

        risk_result = await self._run_risk_gates(signal_data, symbol, current_price)
        if not risk_result.get("passed", False):
            return {
                "status": "rejected",
                "reason": risk_result.get("block_reason", "Risk gates failed on re-check"),
                "risk_gates": risk_result.get("all_gates", []),
            }

        # --- Recalculate position size with current price ---
        # Pass the CONFIRMED segment so equity trades on F&O underlyings are
        # sized as equity (not lot-constrained as futures).
        sizing = await self._calculate_position_size(signal_data, current_price, segment=segment)
        quantity = sizing.get("quantity", quantity)

        # --- FNO Options Execution Branch ---
        trade_symbol = symbol
        trade_price = current_price
        trade_quantity = quantity
        trade_direction = direction
        option_metadata = {}

        if segment == "FNO":
            try:
                # F&O execution REQUIRES a broker serving a real-time option
                # chain (Fyers). Without one there is no real strike premium
                # or tradeable contract symbol — refuse honestly instead of
                # fabricating an option symbol and estimating its premium.
                if self.broker is None or not hasattr(self.broker, "get_option_chain"):
                    return {
                        "status": "rejected",
                        "reason": (
                            "F&O execution requires a broker with real-time option chain support "
                            "(currently Fyers). Connect such a broker or trade this setup in the equity segment."
                        ),
                    }

                chain_fetcher = OptionChainFetcher(broker=self.broker)
                chain_data = await chain_fetcher.fetch_option_chain(symbol)

                if not chain_data or (not chain_data.get("calls") and not chain_data.get("puts")):
                    return {
                        "status": "rejected",
                        "reason": f"No live option chain data for {symbol} — execution aborted (no synthetic option prices).",
                    }

                strike_selector = StrikeSelector()
                strike_info = strike_selector.select_strike(
                    symbol=symbol,
                    direction=direction,
                    entry_price=current_price,
                    sl=stop_loss,
                    target=target,
                    vix=self.vix,
                    option_chain=chain_data,
                )

                option_type = strike_info.get("option_type", "CE")
                selected_strike = strike_info.get("strike", current_price)
                lot_size = int(strike_info.get("lot_size", 1) or 1)
                premium = float(strike_info.get("premium") or 0.0)
                option_symbol = strike_info.get("option_symbol", "")

                # Lot-size sanity: every NSE F&O instrument trades in lots > 1.
                # A lot of 1 means the symbol has NO verified F&O lot-size entry
                # (indices are absent from the verified stock map) — refuse
                # rather than send a wrong-quantity order to the exchange.
                if lot_size <= 1:
                    return {
                        "status": "rejected",
                        "reason": (
                            f"No verified F&O lot size for '{symbol}' (mapped lot={lot_size}). "
                            "Add a verified lot-size entry in utils/market_utils.py before trading "
                            "this instrument in the F&O segment."
                        ),
                    }

                # A tradeable contract symbol with a LIVE premium is mandatory —
                # the old fallback invented "NSE:SYM{YYMMM}{STRIKE}{CE/PE}" with
                # an estimated premium (current_price * 0.012), which is not a
                # real price. No chain match → reject.
                if not option_symbol or premium <= 0:
                    return {
                        "status": "rejected",
                        "reason": (
                            f"Selected {symbol} {selected_strike:.0f}{option_type} strike has no live "
                            "premium / tradeable contract symbol — execution aborted."
                        ),
                    }

                # 1. Hard Liquidity Gate
                matched_contract = None
                side_contracts = chain_data.get("calls", []) if option_type == "CE" else chain_data.get("puts", [])
                for c in side_contracts:
                    if c.get("symbol") == option_symbol or abs(float(c.get("strike", 0)) - selected_strike) < 0.1:
                        matched_contract = c
                        break

                if matched_contract:
                    liquidity_filter = LiquidityFilter()
                    liq_passed, liq_msg = liquidity_filter.validate_strike_liquidity(matched_contract)
                    if not liq_passed:
                        return {
                            "status": "rejected",
                            "reason": f"Options Liquidity Gate failed: {liq_msg}",
                            "symbol": symbol,
                            "option_symbol": option_symbol,
                        }

                # 2. Options-BUYER quantity sizing on the REAL premium.
                # The generic sizer sizes on the UNDERLYING (futures-style), so
                # its quantity is unusable for option buying. Instead: take the
                # Kelly ₹ allocation and divide by (premium × lot), then CLAMP
                # to the options risk limits (2% max-loss / 5% per-trade for
                # buyers, whose full premium is at risk).
                allocation = float(sizing.get("position_size", 0) or 0)
                lots_by_allocation = (
                    int(allocation / (premium * lot_size)) if premium > 0 and lot_size > 0 else 0
                )
                total_cap = resolve_total_capital(engine=self)
                options_risk = OptionsRiskChecker()
                risk_lot_budget = min(
                    total_cap * options_risk.max_loss_per_trade_pct,
                    total_cap * options_risk.max_capital_per_trade_pct,
                )
                lots_by_risk = (
                    int(risk_lot_budget / (premium * lot_size)) if premium > 0 and lot_size > 0 else 0
                )
                if lots_by_risk < 1:
                    return {
                        "status": "rejected",
                        "reason": (
                            f"One lot of {option_symbol} costs ₹{premium * lot_size:,.0f} (premium ₹{premium:.2f} × "
                            f"lot {lot_size}), exceeding the options risk budget ₹{risk_lot_budget:,.0f} "
                            f"(max-loss/per-trade limits on ₹{total_cap:,.0f} capital)."
                        ),
                    }
                planned_lots = max(1, min(lots_by_allocation, lots_by_risk))
                trade_quantity = planned_lots * lot_size

                # 3. Options-Specific Capital & Risk Gate (actual planned qty)
                risk_check = options_risk.check_capital_limits(
                    entry_price=current_price,
                    lot_size=lot_size,
                    premium=premium,
                    total_capital=total_cap,
                    quantity=trade_quantity,
                )
                if not risk_check.get("passed", False):
                    return {
                        "status": "rejected",
                        "reason": f"Options Risk Gate failed: {', '.join(risk_check.get('reasons', []))}",
                        "risk_details": risk_check,
                    }

                # 4. Greeks Calculation
                greeks_calc = GreeksCalculator()
                greeks = greeks_calc.all_greeks(
                    S=current_price,
                    K=selected_strike,
                    T=30.0 / 365.0,
                    sigma=strike_info.get("iv", 0.18) or 0.18,
                    option_type=option_type,
                )

                trade_symbol = option_symbol
                trade_price = premium
                trade_direction = "BUY"  # Directional options buying (Long CE for BUY, Long PE for SELL)

                option_metadata = {
                    "segment": "FNO",
                    "underlying_symbol": symbol,
                    "option_symbol": option_symbol,
                    "strike": selected_strike,
                    "option_type": option_type,
                    "expiry": chain_data.get("expiry", ""),
                    "premium": premium,
                    "iv": strike_info.get("iv") or greeks.get("iv"),
                    "delta": greeks.get("delta"),
                    "gamma": greeks.get("gamma"),
                    "theta": greeks.get("theta"),
                    "vega": greeks.get("vega"),
                    "lot_size": lot_size,
                    "planned_lots": planned_lots,
                    "kelly_allocation": round(allocation, 2),
                }
                logger.info(
                    "Resolved FNO Option Contract: %s @ ₹%.2f (Lots: %d × %d = %d qty, Delta: %.2f, Expiry: %s)",
                    option_symbol, premium, planned_lots, lot_size, trade_quantity,
                    greeks.get("delta", 0), chain_data.get("expiry", ""),
                )
            except Exception as fno_err:
                logger.error("Failed to resolve FNO options pipeline: %s", fno_err, exc_info=True)
                return {"status": "error", "error": f"FNO options resolution failed: {str(fno_err)}"}

        if trade_quantity <= 0:
            return {"status": "rejected", "reason": "Position size calculated as 0"}

        # --- Execute order via broker ---
        trade_id = str(uuid.uuid4())
        order_result = {}
        try:
            if self.broker is not None and hasattr(self.broker, "place_order"):
                order_tx_type = "BUY" if str(trade_direction).upper() in ("LONG", "BUY") else "SELL"
                order_exchange = self._derive_order_exchange(segment)
                order_result = await self.broker.place_order(
                    symbol=trade_symbol,
                    exchange=order_exchange,
                    transaction_type=order_tx_type,
                    quantity=trade_quantity,
                    price=trade_price,
                    order_type="MARKET",
                    segment=segment,
                    stop_loss=stop_loss,
                    target=target,
                )
            else:
                # Paper simulation
                order_result = {
                    "order_id": f"PAPER-{trade_id[:8]}",
                    "status": "FILLED",
                    "filled_price": trade_price,
                    "filled_quantity": trade_quantity,
                }
        except Exception as order_exc:
            await self.error_engine.handle_error(
                order_exc,
                context={"action": "place_order", "symbol": trade_symbol, "direction": trade_direction, "quantity": trade_quantity},
                session_id=self.session_id,
            )
            self._errors_count += 1
            return {"status": "order_failed", "error": str(order_exc)}

        # --- Determine fill price ---
        filled_price = trade_price
        filled_qty = trade_quantity
        order_status = "OPEN"
        broker_order_id = ""

        if isinstance(order_result, dict):
            broker_order_id = order_result.get("order_id", "")
            if order_result.get("filled_price"):
                filled_price = order_result["filled_price"]
            if order_result.get("filled_quantity"):
                filled_qty = order_result["filled_quantity"]
            if order_result.get("status") == "FILLED":
                order_status = "OPEN"  # Position is now open

        invested_amount = filled_price * filled_qty
        sl_distance = abs(filled_price - stop_loss)
        target_distance = abs(target - filled_price)

        # --- Save trade and position to DB ---
        async with self._repo_context() as repo:
            # v0.4.9 wave-4 fee-truth fix: entry-time estimate now uses the
            # canonical FULL round-trip NSE model (both brokerage legs, both
            # turnover legs, correct GST base) — the same calculator the
            # close path and EOD reconciliation use. The old inline formula
            # showed ~₹38-40 for what actually costs ~₹61-62.
            fees_config = self.config.get_fees_config()
            brokerage = float(fees_config.get("brokerage_per_order", 20))
            total_fees = _estimate_entry_round_trip_fees(filled_price, filled_qty, fees_config)

            trade_extra = {
                "opportunity_id": opportunity_id,
                "broker_order_id": broker_order_id,
                "sizing_method": opportunity.get("sizing_method", ""),
                "regime": self.current_regime,
                "vix": self.vix,
                "kronos_score": opportunity.get("kronos_score"),
            }
            trade_extra.update(option_metadata)

            trade = await repo.create_trade(
                id=trade_id,
                symbol=trade_symbol,
                direction=trade_direction,
                strategy=strategy,
                entry_price=filled_price,
                exit_price=0,
                quantity=filled_qty,
                invested_amount=round(invested_amount, 2),
                stop_loss=stop_loss,
                target=target,
                status=order_status,
                session_id=self.session_id,
                signal_confidence=opportunity.get("confidence", 0),
                risk_reward=opportunity.get("risk_reward", 0),
                brokerage=brokerage,
                fees=total_fees,
                net_pnl=0,
                pnl=0,
                tags=[strategy, segment],
                extra=trade_extra,
            )

            # --- Create position ---
            # Phase 1 exit-management metadata: persist the ATR at entry and
            # the strategy's time-stop / fail-fast budgets so _manage_position
            # can enforce them without re-fetching candles.
            _sig_extra = {}
            try:
                _sd = signal_data if isinstance(signal_data, dict) else {}
                _sig_extra = _sd.get("extra_details") if isinstance(_sd.get("extra_details"), dict) else {}
            except Exception:
                _sig_extra = {}
            _entry_atr = float(_sig_extra.get("atr") or 0.0)
            _strategy_upper = str(strategy or "").upper()
            _pos_time_stop = self._time_stop_for(strategy)
            _pos_ff_mult = self._fail_fast_atr_mults.get(_strategy_upper)

            # Dynamic duration estimate re-anchored at the ACTUAL fill price
            # (the signal-time estimate used entry_price; fills can differ).
            # Persisted so the Live Trade Plan card and future analytics can
            # compare elapsed vs expected without recomputation drift.
            _entry_duration = None
            try:
                from core.duration import estimate_trade_duration as _etd

                _dur_atr = None
                _sd_dur = signal_data if isinstance(signal_data, dict) else {}
                _raw_atr = _sd_dur.get("atr")
                if isinstance(_raw_atr, (int, float)) and _raw_atr > 0:
                    _dur_atr = float(_raw_atr)
                elif _entry_atr > 0:
                    _dur_atr = _entry_atr
                _entry_duration = _etd(
                    entry_price=filled_price,
                    target_price=target if target and target > 0 else None,
                    stop_loss=stop_loss if stop_loss and stop_loss > 0 else None,
                    direction=trade_direction,
                    regime=self.current_regime,
                    atr=_dur_atr,
                    now_ist=datetime.now(IST),
                )
            except Exception:
                _entry_duration = None

            position_extra = {
                "opportunity_id": opportunity_id,
                "broker_order_id": broker_order_id,
                "booking_levels": opportunity.get("booking_levels", []),
                "expected_duration": _entry_duration,
                "strategy": strategy,
                "session_id": self.session_id,
                "entry_regime": self.current_regime,
                "entry_vix": self.vix,
                "entry_atr": round(_entry_atr, 4) if _entry_atr > 0 else None,
                "time_stop_minutes": _pos_time_stop if _pos_time_stop > 0 else None,
                "fail_fast_atr_mult": _pos_ff_mult,
                "fail_fast_window_minutes": self._fail_fast_window_minutes if _pos_ff_mult is not None else None,
            }
            position_extra.update(option_metadata)

            await repo.create_position(
                trade_id=trade_id,
                symbol=trade_symbol,
                direction=trade_direction,
                strategy=strategy,
                entry_price=filled_price,
                quantity=filled_qty,
                # remaining_qty defaults to 0 in the model — always set it
                # explicitly so partial booking / close logic sees the full
                # open size (booked_qty starts at 0).
                booked_qty=0,
                remaining_qty=filled_qty,
                invested_amount=round(invested_amount, 2),
                stop_loss=stop_loss,
                target=target,
                current_price=filled_price,
                status="OPEN",
                session_id=self.session_id,
                extra=position_extra,
            )

        self._trades_executed += 1


        # --- Broadcast & Telegram trade fill ---
        trade_payload = {
            "type": "trade_fill",
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "quantity": filled_qty,
            "filled_price": filled_price,
            "entry_price": filled_price,
            "invested_amount": round(invested_amount, 2),
            "stop_loss": stop_loss,
            "target": target,
            "strategy": strategy,
            "broker_order_id": broker_order_id,
            "fees": total_fees,
        }
        await self._broadcast("trade", trade_payload)
        await self._route_alert("trade_fill", trade_payload)

        logger.info(
            "Trade executed: %s %s x%d @ %.2f (SL=%.2f, T=%.2f) [%s]",
            direction, symbol, filled_qty, filled_price, stop_loss, target, strategy,
        )

        return {
            "status": "filled",
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "quantity": filled_qty,
            "filled_price": filled_price,
            "invested_amount": round(invested_amount, 2),
            "stop_loss": stop_loss,
            "target": target,
            "strategy": strategy,
            "broker_order_id": broker_order_id,
            "fees": total_fees,
        }

    async def skip_opportunity(self, opportunity_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """User skips an opportunity."""
        async with self._opportunities_lock:
            opportunity = self.pending_opportunities.pop(opportunity_id, None)
        if opportunity is None:
            return {"status": "not_found", "error": f"Opportunity {opportunity_id} not in pending list"}

        symbol = opportunity.get("symbol", "")
        strategy = opportunity.get("strategy", "")
        skip_reason = reason or "User skipped"

        # Update signal status in DB
        try:
            signal_id = opportunity.get("signal_id")
            if signal_id:
                async with self._repo_context() as repo:
                    await repo.update_signal(signal_id, status="skipped")
        except Exception as exc:
            logger.debug("Could not update signal status: %s", exc)

        await self._broadcast("opportunity", {
            "type": "opportunity_skipped",
            "opportunity_id": opportunity_id,
            "symbol": symbol,
            "strategy": strategy,
            "reason": skip_reason,
        })

        logger.info("Opportunity %s (%s %s) skipped: %s", opportunity_id, symbol, strategy, skip_reason)
        return {"status": "skipped", "opportunity_id": opportunity_id, "reason": skip_reason}

    # ------------------------------------------------------------------
    # Position Management
    # ------------------------------------------------------------------

    async def _manage_all_positions(self) -> None:
        """Manage all open positions: update prices, check SL/target/partial bookings."""
        async with self._repo_context() as repo:
            positions = await repo.get_open_positions()

            for position in positions:
                try:
                    await self._manage_position(position, repo)
                except Exception as pos_exc:
                    logger.error("Error managing position %s: %s", position.id, pos_exc)
                    await self.error_engine.handle_error(
                        pos_exc,
                        context={"action": "manage_position", "position_id": position.id, "symbol": position.symbol},
                        session_id=self.session_id,
                    )

    async def _manage_position(self, position, repo=None) -> None:
        """Manage a single open position: check SL hit, target hit, partial bookings.

        Args:
            position: A Position ORM object from the repository.
            repo: Optional Repository instance to reuse.
        """
        # Get latest price
        current_price = 0.0
        if self.feed is not None and hasattr(self.feed, "get_latest_price"):
            try:
                current_price = await self.feed.get_latest_price(position.symbol)
            except Exception:
                pass
        if current_price <= 0 and self.broker is not None and hasattr(self.broker, "get_latest_price"):
            try:
                current_price = await self.broker.get_latest_price(position.symbol)
            except Exception:
                pass
        if current_price <= 0:
            current_price = position.current_price or position.entry_price

        # Update current price on position
        if repo is not None:
            await repo.update_position(position.id, current_price=current_price)
        else:
            async with self._repo_context() as r:
                await r.update_position(position.id, current_price=current_price)

        entry = position.entry_price
        sl = position.stop_loss
        target = position.target
        direction = position.direction
        quantity = position.quantity

        # Calculate current P&L
        # CORRECTION (live-run-2): positions carry BUY/SELL — normalize via
        # _is_long_direction (raw == "LONG" comparisons inverted every
        # position's P&L/SL/target logic; see the helper's docstring).
        _pos_is_long = _is_long_direction(direction)
        if _pos_is_long:
            pnl_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
            pnl_amount = (current_price - entry) * quantity
            sl_hit = current_price <= sl
            target_hit = current_price >= target
        else:  # SELL / SHORT
            pnl_pct = (entry - current_price) / entry * 100 if entry > 0 else 0
            pnl_amount = (entry - current_price) * quantity
            sl_hit = current_price >= sl
            target_hit = current_price <= target

        # --- Stop Loss Hit ---
        if sl_hit and sl > 0:
            await self._close_position(
                position=position,
                exit_price=current_price,
                close_reason="stop_loss",
                pnl_amount=pnl_amount,
                pnl_pct=pnl_pct,
            )
            return

        # --- Target Hit ---
        if target_hit and target > 0:
            await self._close_position(
                position=position,
                exit_price=current_price,
                close_reason="target",
                pnl_amount=pnl_amount,
                pnl_pct=pnl_pct,
            )
            return

        # --- Time Stop (Phase 1): force-exit stagnant positions -----------
        # A position older than its strategy's time-stop budget is dead
        # capital: the setup's momentum thesis has expired even though the
        # stop has not been hit. Frees margin and attention for fresh setups.
        _pos_extra = self._position_extra_dict(position)
        _strategy = str(getattr(position, "strategy", "") or _pos_extra.get("strategy", ""))
        _age_min = self._position_age_minutes(position)
        _time_stop_min = self._time_stop_for(_strategy)
        if _time_stop_min > 0 and _age_min is not None and _age_min >= _time_stop_min:
            logger.info(
                "Time stop triggered for %s (%s): held %.0f min >= %d min budget",
                position.symbol, _strategy or "?", _age_min, int(_time_stop_min),
            )
            await self._close_position(
                position=position,
                exit_price=current_price,
                close_reason="time_stop",
                pnl_amount=pnl_amount,
                pnl_pct=pnl_pct,
            )
            return

        # --- Fail-Fast Exit (Phase 1): early adverse-move ejection --------
        # Strategies configured in risk.fail_fast (MB, VC) get ejected at a
        # FRACTION of their normal stop distance when the adverse move hits
        # soon after entry: momentum/breakout setups that do not work
        # immediately are statistically broken, so pay the small loss now
        # instead of the full stop later.
        _ff_mult = self._fail_fast_atr_mults.get(_strategy.upper())
        if _ff_mult is not None and _age_min is not None:
            _entry_atr = float(_pos_extra.get("entry_atr") or 0.0)
            if _entry_atr > 0 and _age_min <= self._fail_fast_window_minutes:
                if _pos_is_long:
                    _adverse = entry - current_price
                else:
                    _adverse = current_price - entry
                if _adverse >= _ff_mult * _entry_atr:
                    logger.info(
                        "Fail-fast exit for %s (%s): adverse move %.2f >= %.2f × ATR %.2f within %.0f min",
                        position.symbol, _strategy, _adverse, _ff_mult, _entry_atr, _age_min,
                    )
                    await self._close_position(
                        position=position,
                        exit_price=current_price,
                        close_reason="fail_fast",
                        pnl_amount=pnl_amount,
                        pnl_pct=pnl_pct,
                    )
                    return

        # --- EOD Auto Square-off (Safe Exit Time / Market Close) ---
        if self.market_hours is not None and self.market_hours.is_safe_exit_time():
            logger.info("Auto square-off triggered for %s at safe exit time", position.symbol)
            await self._close_position(
                position=position,
                exit_price=current_price,
                close_reason="auto_squareoff",
                pnl_amount=pnl_amount,
                pnl_pct=pnl_pct,
            )
            return

        # --- Partial Booking Check ---
        if self.partial_booker is not None:
            try:
                booking_result = self.partial_booker.check_partial_booking(
                    current_price=current_price,
                    entry_price=entry,
                    stop_loss=sl,
                    target=target,
                    direction=direction,
                    position=position,
                )

                if hasattr(booking_result, "model_dump"):
                    booking_data = booking_result.model_dump()
                elif isinstance(booking_result, dict):
                    booking_data = booking_result
                else:
                    booking_data = {}

                # Execute partial booking if a new level is triggered
                if booking_data.get("triggered_level"):
                    await self._execute_partial_booking(
                        position=position,
                        booking_data=booking_data,
                        current_price=current_price,
                    )

                # Update trailing / breakeven SL if active or level 1+ triggered
                new_sl = booking_data.get("current_trailing_sl")
                if new_sl:
                    async def _update_sl_on_repo(r):
                        if _pos_is_long and new_sl > sl:
                            await r.update_position(position.id, stop_loss=round(new_sl, 2), extra=getattr(position, "extra", None))
                            await r.update_trade(position.trade_id, stop_loss=round(new_sl, 2))
                            logger.info(
                                "Trailing SL updated for %s: %.2f -> %.2f (Level %s)",
                                position.symbol, sl, new_sl, booking_data.get("current_level"),
                            )
                        elif not _pos_is_long:
                            entry_p = float(getattr(position, "entry_price", None) or getattr(position, "price", 0.0) or 0.0)
                            should_update = (sl > 0 and new_sl < sl) or (sl == 0 and entry_p > 0 and new_sl < entry_p)
                            if should_update:
                                await r.update_position(position.id, stop_loss=round(new_sl, 2), extra=getattr(position, "extra", None))
                                await r.update_trade(position.trade_id, stop_loss=round(new_sl, 2))
                                logger.info(
                                    "Trailing SL updated for %s: %.2f -> %.2f (Level %s)",
                                    position.symbol, sl, new_sl, booking_data.get("current_level"),
                                )

                    if repo is not None:
                        await _update_sl_on_repo(repo)
                    else:
                        async with self._repo_context() as active_repo:
                            await _update_sl_on_repo(active_repo)

            except Exception as pb_exc:
                logger.warning("Partial booking check error for %s: %s", position.symbol, pb_exc, exc_info=True)

    async def _execute_partial_booking(
        self,
        position,
        booking_data: dict,
        current_price: float,
    ) -> None:
        """Execute a partial book exit for a position."""
        level = booking_data.get("triggered_level", 0)
        book_qty = booking_data.get("book_qty", 0)
        remaining_qty = booking_data.get("remaining_qty", 0)

        if book_qty <= 0:
            return

        symbol = position.symbol
        direction = position.direction

        # Execute partial exit via broker
        try:
            if self.broker is not None and hasattr(self.broker, "place_order"):
                exit_tx_type = "SELL" if str(direction).upper() in ("LONG", "BUY") else "BUY"
                partial_order_res = await self.broker.place_order(
                    symbol=symbol,
                    exchange=self._derive_order_exchange(self._position_segment(position)),
                    transaction_type=exit_tx_type,
                    quantity=book_qty,
                    price=current_price,
                    order_type="MARKET",
                )
                # Use the ACTUAL fill price for the P&L math so partial-booked
                # P&L matches broker reality (live-LTP fills / slippage).
                if isinstance(partial_order_res, dict):
                    _pf = partial_order_res.get("filled_price") or partial_order_res.get("avg_fill_price")
                    try:
                        if _pf is not None and float(_pf) > 0:
                            current_price = float(_pf)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            logger.error("Partial booking order failed for %s: %s", symbol, exc)
            await self.error_engine.handle_error(
                exc,
                context={"action": "partial_booking", "symbol": symbol, "level": level, "qty": book_qty},
                session_id=self.session_id,
            )
            return

        # Calculate P&L for partial exit
        entry = position.entry_price
        # CORRECTION (live-run-2): normalize BUY/SELL (raw == "LONG" inverted
        # partial-exit P&L and swapped the fee legs for BUY positions).
        _pb_is_long = _is_long_direction(direction)
        if _pb_is_long:
            pnl_amount = (current_price - entry) * book_qty
            buy_p, sell_p = entry, current_price
        else:
            pnl_amount = (entry - current_price) * book_qty
            buy_p, sell_p = current_price, entry

        fees_config = self.config.get_fees_config() if hasattr(self.config, "get_fees_config") else {}
        brokerage = float(fees_config.get("brokerage_per_order", 20.0))
        from fees.nse_fee_calculator import NSEFeeCalculator
        fee_calc = NSEFeeCalculator(brokerage_per_order=brokerage)
        fee_res = fee_calc.calculate_equity_intraday(buy_price=buy_p, sell_price=sell_p, quantity=int(book_qty), brokerage_per_order=brokerage)
        partial_fees = float(fee_res.get("total", 0.0))
        net_partial_pnl = round(pnl_amount - partial_fees, 2)

        # Record realized P&L from this partial leg into the daily risk tracker
        # (keeps G4/G5 daily-loss gates accurate intra-day without inflating
        # the trade counter).
        if self.daily_risk is not None and hasattr(self.daily_risk, "record_pnl"):
            try:
                self.daily_risk.record_pnl(net_partial_pnl)
            except Exception as dr_err:
                logger.debug("Daily risk partial P&L recording note: %s", dr_err)

        # Update position quantity and persist extra
        extra_data = getattr(position, "extra", {}) or {}
        if isinstance(extra_data, dict):
            extra_data["partial_realized_pnl"] = extra_data.get("partial_realized_pnl", 0.0) + net_partial_pnl
            extra_data["partial_fees"] = extra_data.get("partial_fees", 0.0) + partial_fees

        async with self._repo_context() as repo:
            await repo.update_position(
                position.id,
                quantity=remaining_qty,
                current_price=current_price,
                extra=extra_data,
            )

        # v0.4.8 HF-9: keep the IN-MEMORY position's extra in sync with what
        # was just persisted, so a same-cycle full close through
        # _close_position sees the accumulated partial legs when it merges
        # the round-trip P&L (the caller's ORM object is otherwise stale).
        try:
            position.extra = extra_data if isinstance(extra_data, str) else json.dumps(extra_data)
        except Exception:
            pass

        # If fully exited, close the position
        if remaining_qty <= 0:
            await self._close_position(
                position=position,
                exit_price=current_price,
                close_reason="partial_complete",
                pnl_amount=pnl_amount,
            )

        # --- Broadcast & Telegram Partial Booking ---
        partial_payload = {
            "type": "partial_book",
            "position_id": position.id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry,
            "level": level,
            "stage_name": booking_data.get("stage_name") or f"T{level}",
            "booked_qty": book_qty,
            "remaining_qty": remaining_qty,
            "booked_price": current_price,
            "pnl": round(pnl_amount, 2),
            "net_pnl": net_partial_pnl,
        }
        await self._broadcast("trade", partial_payload)
        await self._route_alert("partial_booking", partial_payload)

        logger.info(
            "Partial book L%d (%s) for %s: %d @ %.2f (P&L: %.2f, remaining: %d)",
            level, booking_data.get("stage_name", ""), symbol, book_qty, current_price, pnl_amount, remaining_qty,
        )


    async def _close_position(
        self,
        position,
        exit_price: float,
        close_reason: str,
        pnl_amount: float = 0,
        pnl_pct: float = 0,
    ) -> None:
        """Close a position and update the corresponding trade.

        v0.4.4: ``pnl_amount`` / ``pnl_pct`` are ACCEPTED for backward
        compatibility but are NO LONGER TRUSTED — both are recomputed here
        from the position's own direction and the EFFECTIVE exit (fill)
        price. This makes the close path self-sufficient: a caller with
        buggy direction logic (the auto-squareoff scheduler shipped with an
        inverted one for BUY positions) or a caller that passes no P&L at
        all (the manual-close API route) can no longer corrupt the books.
        """
        # Execute exit order via broker
        exit_success = True
        exit_err_msg = None
        # Prefer the broker's ACTUAL fill price over the intended exit price so
        # recorded P&L matches broker fills (slippage / live LTP divergence).
        effective_exit_price = float(exit_price)
        try:
            if self.broker is not None and hasattr(self.broker, "place_order"):
                exit_tx_type = "SELL" if str(position.direction).upper() in ("LONG", "BUY") else "BUY"
                order_res = await self.broker.place_order(
                    symbol=position.symbol,
                    exchange=self._derive_order_exchange(self._position_segment(position)),
                    transaction_type=exit_tx_type,
                    quantity=position.quantity,
                    price=exit_price,
                    order_type="MARKET",
                )
                if isinstance(order_res, dict):
                    status = str(order_res.get("status", "")).upper()
                    if status in ("REJECTED", "CANCELLED", "FAILED"):
                        exit_success = False
                        exit_err_msg = order_res.get("message") or order_res.get("error") or f"Broker order returned status: {status}"
                    else:
                        filled = order_res.get("filled_price") or order_res.get("avg_fill_price")
                        try:
                            if filled is not None and float(filled) > 0:
                                effective_exit_price = float(filled)
                        except (TypeError, ValueError):
                            pass
        except Exception as exc:
            exit_success = False
            exit_err_msg = str(exc)
            logger.error("Exit order failed for %s: %s", position.symbol, exc, exc_info=True)
            await self.error_engine.handle_error(
                exc,
                context={"action": "close_position", "symbol": position.symbol, "reason": close_reason},
                session_id=self.session_id,
            )

        if not exit_success:
            self._errors_count += 1
            logger.critical(
                "CRITICAL: Failed to execute exit order for position %s (%s): %s. Position kept OPEN with status EXIT_FAILED.",
                position.id, position.symbol, exit_err_msg
            )
            async with self._repo_context() as repo:
                await repo.update_position(
                    position.id,
                    status="EXIT_FAILED",
                )
                await repo.update_trade(
                    position.trade_id,
                    status="EXIT_FAILED",
                )
            await self._broadcast("trade", {
                "type": "position_exit_failed",
                "position_id": position.id,
                "trade_id": position.trade_id,
                "symbol": position.symbol,
                "error": exit_err_msg,
                "close_reason": close_reason,
            })
            await self._route_alert("error", {
                "message": f"CRITICAL: Position exit failed for {position.symbol}. Position status set to EXIT_FAILED: {exit_err_msg}",
                "rule": "EXIT_ORDER_FAILED",
            })
            return

        # Calculate fees for exit using NSEFeeCalculator
        fees_config = self.config.get_fees_config() if hasattr(self.config, "get_fees_config") else {}
        brokerage = float(fees_config.get("brokerage_per_order", 20.0))
        from fees.nse_fee_calculator import NSEFeeCalculator
        fee_calc = NSEFeeCalculator(brokerage_per_order=brokerage)

        _close_is_long = _is_long_direction(position.direction)
        buy_price = position.entry_price if _close_is_long else effective_exit_price
        sell_price = effective_exit_price if _close_is_long else position.entry_price
        fee_breakdown = fee_calc.calculate_equity_intraday(
            buy_price=buy_price,
            sell_price=sell_price,
            quantity=int(position.quantity),
            brokerage_per_order=brokerage,
        )
        exit_fees = float(fee_breakdown.get("total", 0.0))

        async with self._repo_context() as repo:
            # CORRECTION (live-run-2): calculate_equity_intraday() above
            # computes the FULL round trip (BOTH legs, incl. ₹40 total
            # brokerage) from the actual fill prices. The entry-time fee
            # ESTIMATE stored on the open trade must NOT be added on top —
            # the previous `entry_fees + exit_fees` double-counted costs
            # (live: ASIANPAINT recorded ₹99.42 vs the true ₹61.33 round
            # trip, inflating net loss by ₹38).
            total_fees = exit_fees

            # v0.4.8 HF-9 (accounting truth): merge the realized P&L and fees
            # of any earlier partial-booking legs (accumulated on the
            # position's extra JSON by _execute_partial_booking) into THIS
            # trade record so the row represents the FULL round trip.
            # Previously a trade with partial legs closed showing only the
            # final leg (live 2026-09-01: HCLTECH recorded +299.19 while the
            # L2 leg's +39.92 net leaked out of every EOD aggregation), so
            # EOD net/fees and per-strategy stats were all understated.
            _close_extra = self._position_extra_dict(position)
            _partial_gross = float(_close_extra.get("partial_realized_pnl", 0.0) or 0.0)
            _partial_fees = float(_close_extra.get("partial_fees", 0.0) or 0.0)
            if _partial_fees:
                total_fees = round(total_fees + _partial_fees, 2)

            # v0.4.8 HF-7: classify the exit ONCE, here, from the effective
            # fill data. The previous substring dispatch ("stop" in reason)
            # mislabeled every time_stop / fail_fast / profit-locking
            # trailing exit as "STOP LOSS HIT" (5 of 7 exits on 2026-09-01).
            from utils.exit_taxonomy import (
                classify_exit as _classify_exit,
                exit_alert_kind as _exit_alert_kind,
                EXIT_LABELS as _EXIT_LABELS,
            )
            exit_class = _classify_exit(
                close_reason=close_reason,
                direction=getattr(position, "direction", ""),
                entry_price=float(getattr(position, "entry_price", 0) or 0),
                exit_price=float(effective_exit_price),
                stop_loss=getattr(position, "stop_loss", None),
            )
            # v0.4.4 (audit round 2, defense in depth): ALWAYS recompute gross
            # P&L (and pct) from the position's own direction + the EFFECTIVE
            # (fill) price instead of trusting caller-supplied values.
            # Callers historically computed pnl with their own — sometimes
            # buggy — direction logic:
            #   * scheduler.on_auto_squareoff compared raw ``pos.direction ==
            #     "LONG"`` → inverted for BUY positions (a +₹50 gain was
            #     passed as −₹50)
            #   * the manual-close API route passes NO pnl at all (default 0)
            # The previous code only overrode the caller's value when the
            # fill differed from the requested exit by > ₹0.01, so the
            # scheduler's inverted value survived whenever the fill landed
            # within a paisa, and pnl_pct was NEVER recomputed (inverted %
            # flowed into the trade-closed broadcast and performance tracker
            # for every BUY position). Recomputing here makes _close_position
            # self-sufficient and correct regardless of caller.
            _close_entry = float(getattr(position, "entry_price", 0) or 0)
            _close_qty = float(getattr(position, "quantity", 0) or 0)
            if _close_is_long:
                effective_pnl = (effective_exit_price - _close_entry) * _close_qty
            else:
                effective_pnl = (_close_entry - effective_exit_price) * _close_qty
            pnl_amount = effective_pnl
            _close_cost_basis = _close_entry * _close_qty
            if _close_cost_basis > 0:
                pnl_pct = (effective_pnl / _close_cost_basis) * 100.0
            else:
                pnl_pct = 0.0
            # v0.4.8 HF-9: net covers the FULL round trip (final leg +
            # partial legs) minus ALL fees (final leg + partial legs).
            _round_trip_gross = round(pnl_amount + _partial_gross, 2)
            net_pnl = round(_round_trip_gross - total_fees, 2)

            # Update trade
            # v0.4.8 HF-7: exit_reason finally written (column existed since
            # the initial schema but was never populated).
            await repo.update_trade(
                position.trade_id,
                exit_price=effective_exit_price,
                exit_time=datetime.now(IST).isoformat(),
                status="CLOSED",
                exit_reason=exit_class,
                pnl=_round_trip_gross,
                fees=total_fees,
                net_pnl=net_pnl,
            )

            # Update position
            await repo.update_position(
                position.id,
                current_price=effective_exit_price,
                status="CLOSED",
            )

        # Update daily risk tracker if present
        # NOTE: DailyRiskManager's real method is record_trade_result(); the
        # previous hasattr(..., "record_trade") guard referenced a method that
        # does not exist, silently disabling ALL daily-loss/trade-count gates.
        if self.daily_risk is not None and hasattr(self.daily_risk, "record_trade_result"):
            try:
                # v0.4.8 HF-9: partial-booking legs were ALREADY fed to the
                # daily-risk tracker as they happened (record_pnl inside
                # _execute_partial_booking). net_pnl now includes those
                # legs, so record ONLY the final leg here — passing net_pnl
                # would double-count every partial booking.
                self.daily_risk.record_trade_result(pnl=round(pnl_amount - exit_fees, 2))
                daily_status = self.daily_risk.check_daily_limits()
                if daily_status and not getattr(daily_status, "can_trade", True):
                    await self._route_alert("risk_event", {
                        "message": f"Daily risk threshold exceeded ({getattr(daily_status, 'reason', 'Daily limit reached')}).",
                        "rule": "DAILY_RISK_LIMIT",
                    })
            except Exception as dr_err:
                logger.debug("Daily risk recording note: %s", dr_err)

        # Feed the REAL closed-trade result into the performance tracker so
        # per-strategy statistics build from executed trades only.
        _perf_tracker = getattr(self, "performance_tracker", None)
        if _perf_tracker is not None and hasattr(_perf_tracker, "record_trade"):
            try:
                holding_secs = None
                try:
                    _pos_entry = getattr(position, "entry_time", None)
                    if _pos_entry:
                        _entry_dt = datetime.fromisoformat(str(_pos_entry))
                        if _entry_dt.tzinfo is None:
                            _entry_dt = _entry_dt.replace(tzinfo=IST)
                        holding_secs = max(0.0, (datetime.now(IST) - _entry_dt).total_seconds())
                except Exception:
                    holding_secs = None
                _perf_tracker.record_trade(
                    strategy_name=str(getattr(position, "strategy", "") or ""),
                    regime=str(getattr(self, "current_regime", "") or "Unknown"),
                    pnl=float(net_pnl or 0.0),
                    pnl_pct=float(pnl_pct or 0.0),
                    holding_time_seconds=float(holding_secs or 0.0),
                    trade_id=str(getattr(position, "trade_id", "") or None),
                    symbol=str(getattr(position, "symbol", "") or None),
                )
            except Exception as pt_err:
                logger.debug("Performance tracker recording note: %s", pt_err)

        close_payload = {
            "type": "position_closed",
            "position_id": position.id,
            "trade_id": position.trade_id,
            "symbol": position.symbol,
            "direction": position.direction,
            "strategy": getattr(position, "strategy", ""),
            "entry_price": position.entry_price,
            # v0.4.8 HF-9: report the EFFECTIVE (fill) exit price — the same
            # number persisted to the trade row — so Telegram messages and
            # dashboard toasts reconcile against the DB to the paisa. The
            # requested price is kept alongside for auditability.
            "exit_price": effective_exit_price,
            "requested_exit_price": exit_price,
            "target": getattr(position, "target", 0.0),
            "stop_loss": getattr(position, "stop_loss", 0.0),
            "quantity": position.quantity,
            "pnl": _round_trip_gross,
            "net_pnl": net_pnl,
            "pnl_pct": pnl_pct,
            "fees": total_fees,
            "close_reason": close_reason,
            "exit_reason": exit_class,
            "exit_reason_label": _EXIT_LABELS.get(exit_class, str(close_reason)),
        }
        await self._broadcast("trade", close_payload)

        # Dispatch the Telegram alert through the CLASSIFIED exit kind
        # (v0.4.8 HF-7): substring matching on close_reason labeled every
        # time_stop / trailing exit as a stop loss.
        _alert_kind = _exit_alert_kind(exit_class)
        if _alert_kind == "stop_loss_hit":
            await self._route_alert("stop_loss_hit", close_payload)
        elif _alert_kind == "target_hit":
            await self._route_alert("target_hit", close_payload)
        else:
            await self._route_alert("position_closed", close_payload)

        logger.info(
            "Position closed: %s %s x%d | Entry: %.2f -> Exit: %.2f | P&L: %.2f (Net: %.2f) | Reason: %s",
            position.direction, position.symbol, position.quantity,
            position.entry_price, exit_price, pnl_amount, net_pnl, close_reason,
        )

    # ------------------------------------------------------------------
    # Market Context
    # ------------------------------------------------------------------

    async def _update_market_context(self) -> None:
        """Update VIX, nifty price, nifty change, banknifty, and regime from feed/broker."""
        prev_nifty = self.nifty_price

        # Get Nifty price — prefer feed, fallback to broker with sanity range 15000–28000
        nifty_fetched = None
        for nifty_sym in ("NIFTY", "NIFTY 50", "NSE:NIFTY50-INDEX"):
            if self.feed is not None and hasattr(self.feed, "get_latest_price"):
                try:
                    p = await self.feed.get_latest_price(nifty_sym)
                    if p and p > 0:
                        nifty_fetched = float(p)
                        break
                except Exception:
                    pass
            if self.broker is not None and hasattr(self.broker, "get_latest_price"):
                try:
                    p = await self.broker.get_latest_price(nifty_sym)
                    if p and p > 0:
                        nifty_fetched = float(p)
                        break
                except Exception:
                    pass

        # Sanity ranges are config-driven so they can be widened without a
        # code change when index levels drift (BANKNIFTY crossed the old
        # hardcoded 55000 ceiling and its REAL price was being rejected).
        _sanity_cfg = self.config.get_risk_config() if hasattr(self.config, "get_risk_config") else {}
        nifty_min = float(_sanity_cfg.get("nifty_sanity_min", 10000.0))
        nifty_max = float(_sanity_cfg.get("nifty_sanity_max", 40000.0))

        if nifty_fetched is not None:
            if nifty_min <= nifty_fetched <= nifty_max:
                self.nifty_price = nifty_fetched
            else:
                logger.warning(
                    "NIFTY price %.2f outside sanity range [%.0f, %.0f], retaining previous valid price %.2f",
                    nifty_fetched, nifty_min, nifty_max, self.nifty_price,
                )

        # Compute nifty_change percentage
        if self.nifty_price > 0:
            # Try to get previous close for accurate change
            if self._prev_nifty_close <= 0:
                # First run: try to fetch previous close from broker
                if self.broker is not None and hasattr(self.broker, "get_previous_close"):
                    try:
                        pc = await self.broker.get_previous_close("NIFTY 50")
                        if pc and 15000.0 <= pc <= 28000.0:
                            self._prev_nifty_close = pc
                    except Exception:
                        pass
                # Second fallback: try to fetch from feed daily candles
                if self._prev_nifty_close <= 0 and self.feed is not None and hasattr(self.feed, "get_candles"):
                    try:
                        candles = await self.feed.get_candles("^NSEI", "1d", 2)
                        if candles is not None and len(candles) >= 2:
                            c_close = float(candles.iloc[-2]["close"])
                            if 15000.0 <= c_close <= 28000.0:
                                self._prev_nifty_close = c_close
                    except Exception:
                        pass
                # Third fallback: use first observed price as baseline
                if self._prev_nifty_close <= 0 and prev_nifty > 0:
                    self._prev_nifty_close = prev_nifty
                elif self._prev_nifty_close <= 0:
                    self._prev_nifty_close = self.nifty_price

            if self._prev_nifty_close > 0:
                self.nifty_change = round(
                    ((self.nifty_price - self._prev_nifty_close) / self._prev_nifty_close) * 100, 2
                )

        # Get BankNifty price — prefer feed, fallback to broker with sanity range 35000–55000
        banknifty_fetched = None
        for bn_sym in ("BANKNIFTY", "NIFTY BANK", "NSE:NIFTYBANK-INDEX"):
            if self.feed is not None and hasattr(self.feed, "get_latest_price"):
                try:
                    p = await self.feed.get_latest_price(bn_sym)
                    if p and p > 0:
                        banknifty_fetched = float(p)
                        break
                except Exception:
                    pass
            if self.broker is not None and hasattr(self.broker, "get_latest_price"):
                try:
                    p = await self.broker.get_latest_price(bn_sym)
                    if p and p > 0:
                        banknifty_fetched = float(p)
                        break
                except Exception:
                    pass

        banknifty_min = float(_sanity_cfg.get("banknifty_sanity_min", 25000.0))
        banknifty_max = float(_sanity_cfg.get("banknifty_sanity_max", 100000.0))

        if banknifty_fetched is not None:
            if banknifty_min <= banknifty_fetched <= banknifty_max:
                self.banknifty_price = banknifty_fetched
            else:
                logger.warning(
                    "BANKNIFTY price %.2f outside sanity range [%.0f, %.0f], retaining previous valid price %.2f",
                    banknifty_fetched, banknifty_min, banknifty_max, self.banknifty_price,
                )

        # Get VIX — prefer feed, fallback to broker
        vix_fetched = False
        for vix_sym in ("INDIAVIX", "INDIA VIX", "NSE:INDIAVIX-INDEX"):
            if self.feed is not None and hasattr(self.feed, "get_latest_price"):
                try:
                    v = await self.feed.get_latest_price(vix_sym)
                    if v and v > 0:
                        self.vix = float(v)
                        self.vix_updated_at = datetime.now(IST)
                        vix_fetched = True
                        break
                except Exception:
                    pass
            if self.broker is not None and hasattr(self.broker, "get_latest_price"):
                try:
                    v = await self.broker.get_latest_price(vix_sym)
                    if v and v > 0:
                        self.vix = float(v)
                        self.vix_updated_at = datetime.now(IST)
                        vix_fetched = True
                        break
                except Exception:
                    pass

        # 3-Tier VIX Staleness Safety Evaluation
        now = datetime.now(IST)
        if vix_fetched:
            # Fresh VIX fetched this cycle
            was_stale = self.vix_critical_stale or self.vix_stale_warning_logged
            self.vix_critical_stale = False
            self.vix_stale_warning_logged = False
            if was_stale:
                logger.info("VIX data feed recovered: VIX=%.2f. Resuming normal operations.", self.vix)
                await self._route_alert(
                    "risk_alert",
                    {
                        "type": "vix_recovered",
                        "severity": "INFO",
                        "vix": self.vix,
                        "action": "RESUMED_NORMAL_OPERATIONS",
                    },
                )
        elif self.vix_updated_at is None:
            # VIX has never been successfully fetched yet (engine startup phase)
            start_ref = self._start_time or now
            startup_elapsed = (now - start_ref).total_seconds()
            if startup_elapsed < self.vix_staleness_critical_seconds:
                # Startup grace period: do not halt or apply conservative floor yet
                logger.debug(
                    "Initial VIX fetch pending during startup grace period (elapsed=%.1fs < %ds). Using default VIX=%.1f.",
                    startup_elapsed,
                    self.vix_staleness_critical_seconds,
                    self.vix,
                )
                self.vix_critical_stale = False
                self.vix_stale_warning_logged = False
            else:
                # Startup grace window expired with zero successful VIX fetches
                self.vix_critical_stale = True
                self.vix = max(self.vix if self.vix > 0 else 15.0, self.vix_stale_floor)
                logger.critical(
                    "VIX has NEVER been fetched and startup grace period exceeded (elapsed=%.1fs > %ds). Applied conservative floor VIX=%.1f. Halting new signal generation.",
                    startup_elapsed,
                    self.vix_staleness_critical_seconds,
                    self.vix,
                )
                await self._route_alert(
                    "risk_alert",
                    {
                        "type": "vix_critically_stale",
                        "severity": "CRITICAL",
                        "age_seconds": round(startup_elapsed, 1),
                        "applied_vix": self.vix,
                        "action": "HALT_NEW_SIGNALS",
                    },
                )
        else:
            # VIX was previously fetched, but this cycle failed to refresh it
            vix_age = (now - self.vix_updated_at).total_seconds()
            if vix_age > self.vix_staleness_critical_seconds:
                self.vix_critical_stale = True
                applied_vix = max(self.vix if self.vix > 0 else 15.0, self.vix_stale_floor)
                self.vix = applied_vix
                logger.critical(
                    "VIX data is CRITICALLY STALE (age=%.1fs > %ds threshold). Applied conservative floor VIX=%.1f. Halting new signal generation.",
                    vix_age,
                    self.vix_staleness_critical_seconds,
                    self.vix,
                )
                await self._route_alert(
                    "risk_alert",
                    {
                        "type": "vix_critically_stale",
                        "severity": "CRITICAL",
                        "age_seconds": round(vix_age, 1),
                        "applied_vix": self.vix,
                        "action": "HALT_NEW_SIGNALS",
                    },
                )
            elif vix_age > self.vix_staleness_warning_seconds:
                self.vix_critical_stale = False
                applied_vix = max(self.vix if self.vix > 0 else 15.0, self.vix_stale_floor)
                self.vix = applied_vix
                if not self.vix_stale_warning_logged:
                    logger.warning(
                        "VIX data is STALE (age=%.1fs > %ds threshold). Applied conservative floor VIX=%.1f.",
                        vix_age,
                        self.vix_staleness_warning_seconds,
                        self.vix,
                    )
                    await self._route_alert(
                        "risk_alert",
                        {
                            "type": "vix_stale_warning",
                            "severity": "WARNING",
                            "age_seconds": round(vix_age, 1),
                            "applied_vix": self.vix,
                            "action": "APPLIED_CONSERVATIVE_FLOOR",
                        },
                    )
                    self.vix_stale_warning_logged = True
            else:
                # Fresh within warning threshold
                self.vix_critical_stale = False
                self.vix_stale_warning_logged = False

        if self.vix <= 0:
            self.vix = 15.0  # fallback default

        # Feed Watchdog Health Check Evaluation
        if self.feed is not None and hasattr(self.feed, "health_check"):
            try:
                feed_health = await self.feed.health_check()
                status = feed_health.get("status", "HEALTHY")
                consec_fails = feed_health.get("failure_count", 0)
                is_healthy = feed_health.get("healthy", False)

                if status == "FROZEN":
                    if not self._feed_alerted_down:
                        self._feed_alerted_down = True
                        logger.critical(
                            "Market data feed is FROZEN (data stalled for >= %d consecutive checks during market hours).",
                            feed_health.get("consecutive_frozen_checks", 5),
                        )
                        await self._route_alert(
                            "feed_alert",
                            {
                                "type": "feed_frozen",
                                "severity": "CRITICAL",
                                "consecutive_frozen": feed_health.get("consecutive_frozen_checks"),
                                "status": "FROZEN",
                                "action": "FEED_FROZEN_STALLED",
                            },
                        )
                elif not is_healthy and consec_fails >= 3:
                    if not self._feed_alerted_down:
                        self._feed_alerted_down = True
                        logger.critical(
                            "Market data feed is UNRESPONSIVE (%d consecutive failures).",
                            consec_fails,
                        )
                        await self._route_alert(
                            "feed_alert",
                            {
                                "type": "feed_unresponsive",
                                "severity": "CRITICAL",
                                "failures": consec_fails,
                                "status": status,
                                "action": "FEED_DEGRADED",
                            },
                        )
                elif is_healthy:
                    if self._feed_alerted_down:
                        self._feed_alerted_down = False
                        logger.info("Market data feed connection RESTORED.")
                        await self._route_alert(
                            "feed_alert",
                            {
                                "type": "feed_recovered",
                                "severity": "INFO",
                                "status": "HEALTHY",
                                "action": "FEED_RESTORED",
                            },
                        )
            except Exception as fh_exc:
                logger.warning("Error running feed health check: %s", fh_exc)

        # Update regime (simplified: based on VIX and Nifty change)
        self._update_regime_simple()

    def _update_regime_simple(self) -> None:
        """Update market regime using RegimeDetector or fallback rules.

        Classifies market into: Bull, Bear, Sideways, Volatile.
        """
        new_regime = None
        if self.regime_detector is not None and hasattr(self.regime_detector, "classify"):
            try:
                res = self.regime_detector.classify(
                    nifty_price=self.nifty_price,
                    nifty_day_change_pct=self.nifty_change,
                    vix=self.vix,
                )
                if isinstance(res, dict) and "regime" in res:
                    new_regime = res["regime"]
                    # Keep the detector's REAL confidence; the status payload
                    # previously fabricated a hardcoded 78 here.
                    try:
                        self.regime_confidence = float(res.get("confidence", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        self.regime_confidence = 0.0
            except Exception as rd_exc:
                logger.warning("RegimeDetector.classify error: %s", rd_exc)

        if not new_regime and self.adaptive_manager is not None and hasattr(self.adaptive_manager, "update_regime"):
            try:
                new_regime = self.adaptive_manager.update_regime({
                    "nifty_price": self.nifty_price,
                    "nifty_day_change_pct": self.nifty_change,
                    "vix": self.vix,
                })
            except Exception as am_exc:
                logger.warning("AdaptiveManager.update_regime error: %s", am_exc)

        if not new_regime:
            regime_config = self.config.get_regime_config() if hasattr(self.config, "get_regime_config") else {}
            high_vix = regime_config.get("high_vix_threshold", 22)

            if self.vix >= high_vix:
                new_regime = "Volatile"
            elif abs(self.nifty_change) <= 0.3 and self.vix < 18:
                new_regime = "Sideways"
            elif self.nifty_change < -0.3 or self.vix >= 18:
                new_regime = "Bear"
            else:
                new_regime = "Bull"

        if new_regime != self.current_regime or not self.active_strategies:
            old_regime = self.current_regime
            self.current_regime = new_regime

            # Update active strategies for new regime
            if hasattr(self.config, "get_strategy_activation"):
                activation = self.config.get_strategy_activation(new_regime)
                if isinstance(activation, dict) and "active" in activation:
                    self.active_strategies = list(activation.get("active", []))
            elif self.adaptive_manager and hasattr(self.adaptive_manager, "get_active_strategies"):
                active = self.adaptive_manager.get_active_strategies()
                if active:
                    self.active_strategies = [
                        getattr(s, "name", str(s)) for s in active
                    ]

            if old_regime != new_regime:
                logger.info(
                    "Regime changed: %s -> %s (VIX=%.1f, Nifty=%.2f%%, strategies=%s)",
                    old_regime, new_regime, self.vix, self.nifty_change, self.active_strategies,
                )

    async def _update_position_prices(self) -> None:
        """Update current_price for all open positions."""
        async with self._repo_context() as repo:
            positions = await repo.get_open_positions()

            for pos in positions:
                price = 0.0
                if self.feed is not None and hasattr(self.feed, "get_latest_price"):
                    try:
                        price = await self.feed.get_latest_price(pos.symbol)
                    except Exception:
                        pass
                if price <= 0 and self.broker is not None and hasattr(self.broker, "get_latest_price"):
                    try:
                        price = await self.broker.get_latest_price(pos.symbol)
                    except Exception:
                        pass
                if price > 0:
                    await repo.update_position(pos.id, current_price=price)

    # ------------------------------------------------------------------
    # Status & Dashboard
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """Get full engine status."""
        market_status = self.market_hours.get_market_status()
        uptime_seconds = 0
        if self._start_time:
            uptime_seconds = (datetime.now(IST) - self._start_time).total_seconds()

        # Get daily P&L
        pnl_data = {"net_pnl": 0, "total_trades": 0, "wins": 0, "losses": 0}
        try:
            async with self._repo_context() as repo:
                pnl_data = await repo.get_todays_pnl()
        except Exception:
            pass

        # Get risk status
        risk_summary = {}
        try:
            risk_status = await self.daily_risk.get_daily_risk_status()
            if hasattr(risk_status, "model_dump"):
                risk_summary = risk_status.model_dump()
            elif isinstance(risk_status, dict):
                risk_summary = risk_status
        except Exception:
            pass

        return {
            "state": self.state.value,
            "mode": self.mode,
            "broker": self.broker_name or "paper",
            "session_id": self.session_id,
            "regime": self.current_regime,
            "vix": self.vix,
            "vix_updated_at": self.vix_updated_at.isoformat() if self.vix_updated_at else None,
            "vix_critical_stale": getattr(self, "vix_critical_stale", False),
            "vix_staleness_seconds": round((datetime.now(IST) - self.vix_updated_at).total_seconds(), 1) if self.vix_updated_at else None,
            "nifty_price": self.nifty_price,
            "nifty_change": self.nifty_change,
            "regime_confidence": getattr(self, "regime_confidence", 0.0),
            "market": market_status,
            "active_strategies": self.active_strategies,
            "shadow_strategies": sorted(self.shadow_strategies),
            "pending_opportunities": len(self.pending_opportunities),
            "scans_completed": self._scan_count,
            "symbols_scanned": self._symbols_scanned_count,
            "signals_generated": self._signals_generated,
            "signals_passed": self._signals_passed_count,
            "signals_rejected": self._signals_rejected_count,
            "rejections_by_gate": dict(self._rejections_by_gate),
            "rejections_by_strategy": dict(self._rejections_by_strategy),
            "trades_executed": self._trades_executed,
            "errors_count": self._errors_count,
            "uptime_seconds": round(uptime_seconds, 1),
            "daily_pnl": pnl_data,
            "risk": risk_summary,
            "initial_capital": self.initial_capital,
            "feed_health": self.feed.get_status() if self.feed and hasattr(self.feed, "get_status") else (
                self.feed_manager.get_status() if self.feed_manager and hasattr(self.feed_manager, "get_status") else None
            ),
            # P1: data-source transparency — which feed is actually serving
            # candles right now (e.g. "Fyers 1m Realtime" vs "Yahoo") and the
            # effective scan cadence (60s realtime vs 180s config default).
            "data_source": self._active_data_source_name(),
            "data_source_realtime": self._is_realtime_feed_active(),
            "effective_scan_interval_seconds": self._effective_scan_interval(),
        }

    def _active_data_source_name(self) -> Optional[str]:
        """Name of the feed currently serving candles (None when unknown)."""
        try:
            feed = self.feed
            if feed is not None and hasattr(feed, "get_active_feed"):
                active = feed.get_active_feed()
                return active.get_name() if active and hasattr(active, "get_name") else None
            if feed is not None and hasattr(feed, "get_name"):
                return feed.get_name()
        except Exception:
            pass
        return None

    def _is_realtime_feed_active(self) -> bool:
        """True when the active candle source is a realtime feed (Fyers 1m)."""
        try:
            feed = self.feed
            active = feed.get_active_feed() if feed is not None and hasattr(feed, "get_active_feed") else feed
            return bool(active is not None and getattr(active, "is_realtime", False))
        except Exception:
            return False

    def _effective_scan_interval(self) -> float:
        """The scan cadence the main loop is actually sleeping (data-aware)."""
        try:
            engine_cfg = self.config.get_engine_config() if hasattr(self.config, "get_engine_config") else {}
            base = float(engine_cfg.get("scan_interval_seconds", 180))
            if self._is_realtime_feed_active():
                fast = float(engine_cfg.get("scan_interval_realtime_seconds", 60))
                return min(base, fast)
            return base
        except Exception:
            return 180.0

    async def get_dashboard_data(self) -> dict:
        """Get aggregated dashboard data."""
        positions_data = []
        total_invested = 0
        total_unrealized_pnl = 0
        trades_data = []
        pnl_data = {"net_pnl": 0, "total_trades": 0, "wins": 0, "losses": 0}

        async with self._repo_context() as repo:
            # Today's P&L
            pnl_data = await repo.get_todays_pnl()

            # Open positions
            open_positions = await repo.get_open_positions()

            for pos in open_positions:
                entry = pos.entry_price or 0
                current = pos.current_price or pos.entry_price or 0
                qty = pos.quantity or 0
                invested = entry * qty
                unrealized = 0
                if _is_long_direction(pos.direction):
                    unrealized = (current - entry) * qty
                else:
                    unrealized = (entry - current) * qty

                # Live Trade Plan fields: pre-marked booking ladder + the
                # entry-anchored dynamic duration estimate, parsed from the
                # position's extra JSON so the frontend can render the plan.
                _pos_extra = self._position_extra_dict(pos)

                positions_data.append({
                    "position_id": pos.id,
                    "trade_id": pos.trade_id,
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "strategy": pos.strategy,
                    "entry_price": entry,
                    "current_price": current,
                    "quantity": qty,
                    "invested_amount": round(invested, 2),
                    "unrealized_pnl": round(unrealized, 2),
                    "unrealized_pnl_pct": round(unrealized / invested * 100, 2) if invested > 0 else 0,
                    "stop_loss": pos.stop_loss,
                    "target": pos.target,
                    "entry_time": getattr(pos, "entry_time", None),
                    "booking_levels": _pos_extra.get("booking_levels", []),
                    "expected_duration": _pos_extra.get("expected_duration"),
                })
                total_invested += invested
                total_unrealized_pnl += unrealized

            # Today's trades
            todays_trades = await repo.get_todays_trades()
            for t in todays_trades:
                trades_data.append({
                    "trade_id": t.id,
                    "symbol": t.symbol,
                "direction": t.direction,
                "strategy": t.strategy,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "status": t.status,
                "pnl": t.pnl,
                "net_pnl": t.net_pnl,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
            })

        # Risk state
        risk_state = {}
        try:
            risk_status = await self.daily_risk.get_daily_risk_status()
            if hasattr(risk_status, "model_dump"):
                risk_state = risk_status.model_dump()
            elif isinstance(risk_status, dict):
                risk_state = risk_status
        except Exception:
            pass

        # Market status
        market_status = self.market_hours.get_market_status()

        # Pending opportunities
        async with self._opportunities_lock:
            pending = list(self.pending_opportunities.values())

        # Engine status
        uptime_seconds = 0
        if self._start_time:
            uptime_seconds = (datetime.now(IST) - self._start_time).total_seconds()

        # Capital metrics
        total_capital = resolve_total_capital(engine=self)
        capital_available = max(0.0, total_capital - total_invested)
        capital_usage_pct = round((total_invested / total_capital * 100.0), 2) if total_capital > 0 else 0.0

        return {
            "engine": {
                "state": self.state.value,
                "mode": self.mode,
                "broker": self.broker_name or "paper",
                "session_id": self.session_id,
                "uptime_seconds": round(uptime_seconds, 1),
                "scans_completed": self._scan_count,
                "symbols_scanned": self._symbols_scanned_count,
                "signals_generated": self._signals_generated,
                "signals_passed": self._signals_passed_count,
                "signals_rejected": self._signals_rejected_count,
                "rejections_by_gate": dict(self._rejections_by_gate),
                "rejections_by_strategy": dict(self._rejections_by_strategy),
                "trades_executed": self._trades_executed,
                "errors_count": self._errors_count,
            },
            "market": market_status,
            "regime": self.current_regime if hasattr(self, "current_regime") and self.current_regime else "Sideways",
            "regime_confidence": getattr(self, "regime_confidence", 0.0),
            "vix": getattr(self, "vix", 0.0),
            "vix_updated_at": self.vix_updated_at.isoformat() if self.vix_updated_at else None,
            "vix_critical_stale": getattr(self, "vix_critical_stale", False),
            "vix_staleness_seconds": round((datetime.now(IST) - self.vix_updated_at).total_seconds(), 1) if self.vix_updated_at else None,
            "nifty_price": getattr(self, "nifty_price", 0.0),
            "nifty_change": getattr(self, "nifty_change", 0.0),
            "active_strategies": getattr(self, "active_strategies", []),
            "capital": {
                "total": total_capital,
                "invested": round(total_invested, 2),
                "available": round(capital_available, 2),
                "usage_pct": capital_usage_pct,
                "unrealized_pnl": round(total_unrealized_pnl, 2),
            },
            "daily_pnl": pnl_data,
            "risk": risk_state,
            "open_positions": positions_data,
            "open_position_count": len(open_positions),
            "todays_trades": trades_data,
            "pending_opportunities": pending,
            "pending_opportunity_count": len(pending),
            "feed_health": self.feed.get_status() if self.feed and hasattr(self.feed, "get_status") else (
                self.feed_manager.get_status() if self.feed_manager and hasattr(self.feed_manager, "get_status") else None
            ),
            "timestamp": datetime.now(IST).isoformat(),
        }

    # ------------------------------------------------------------------
    # Paper-broker capital alignment
    # ------------------------------------------------------------------

    def _sync_paper_broker_capital(self) -> None:
        """Align the PaperBroker ledger with the engine's current capital.

        Called after any post-creation change to ``self.initial_capital``
        (same-day session recovery, live-margin fetch). Only safe when the
        broker has no open positions — an active ledger must never be
        clobbered mid-session.
        """
        try:
            from brokers.paper_broker import PaperBroker

            broker = getattr(self, "broker", None)
            if isinstance(broker, PaperBroker):
                positions = getattr(broker, "positions", None) or {}
                if not positions:
                    cap = float(self.initial_capital or 0.0)
                    if cap > 0:
                        broker.initial_capital = cap
                        broker.capital = cap
        except Exception as exc:
            logger.debug("Paper-broker capital sync skipped: %s", exc)

    # ------------------------------------------------------------------
    # WebSocket Broadcast
    # ------------------------------------------------------------------

    async def _broadcast(self, channel: str, data: dict) -> None:
        """Send data via WebSocket manager if available.

        Args:
            channel: Channel name (e.g. 'engine', 'trade', 'opportunity', 'risk').
            data: Payload dict to send.
        """
        if self.ws_manager is None:
            return

        try:
            if hasattr(self.ws_manager, "broadcast"):
                await self.ws_manager.broadcast(channel, data)
            elif callable(self.ws_manager):
                result = self.ws_manager(channel, data)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as ws_exc:
            logger.warning("WebSocket broadcast failed on channel '%s': %s", channel, ws_exc, exc_info=True)
