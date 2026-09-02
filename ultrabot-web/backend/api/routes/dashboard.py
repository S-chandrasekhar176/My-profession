import logging
from datetime import date, datetime
from typing import Any, Dict
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_current_user, get_engine, get_repository
from db.repository import Repository
from core.engine import UltraBotEngine
from config.settings import settings

logger = logging.getLogger(__name__)

# Very basic cache for Yahoo Finance market data
_market_data_cache = {
    "nifty": 0.0,
    "vix": 0.0,
    "nifty_change": 0.0,
    "last_updated": 0,
}

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
    repo: Repository = Depends(get_repository),
) -> Dict:
    """Return aggregated dashboard data.

    If the engine is running, delegates to engine.get_dashboard_data().
    Otherwise, assembles basic data from the repository.
    """
    try:
        # Try to use engine dashboard if running
        if engine is not None and engine.state.value in ("running", "paused"):
            data = await engine.get_dashboard_data()
            return data
    except Exception as exc:
        logger.warning("Engine dashboard failed, falling back to repo: %s", exc)

    # Fallback: build dashboard from repository
    try:
        # Today's P&L
        pnl_data = await repo.get_todays_pnl()

        # Open positions
        open_positions = await repo.get_open_positions()
        positions_data = []
        total_invested = 0.0
        total_unrealized_pnl = 0.0

        for pos in open_positions:
            entry = pos.entry_price or 0
            current = pos.current_price or pos.entry_price or 0
            qty = pos.quantity or 0
            invested = entry * qty
            unrealized = 0.0
            # v0.4.4 (audit round 2): positions carry BUY/SELL — the raw
            # ``pos.direction == "LONG"`` comparison inverted unrealized P&L
            # for every BUY position in this engine-down fallback path.
            from utils.direction import is_long_direction
            if is_long_direction(pos.direction):
                unrealized = (current - entry) * qty
            else:
                unrealized = (entry - current) * qty

            # Live Trade Plan fields (mirror engine.get_dashboard_data)
            pos_extra: Dict[str, Any] = {}
            raw_extra = getattr(pos, "extra", None)
            if isinstance(raw_extra, dict):
                pos_extra = raw_extra
            elif isinstance(raw_extra, str) and raw_extra:
                try:
                    import json as _json

                    parsed = _json.loads(raw_extra)
                    pos_extra = parsed if isinstance(parsed, dict) else {}
                except (ValueError, TypeError):
                    pos_extra = {}

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
                "booking_levels": pos_extra.get("booking_levels", []),
                "expected_duration": pos_extra.get("expected_duration"),
            })
            total_invested += invested
            total_unrealized_pnl += unrealized

        # Capital
        capital_config = settings.get_capital_config()
        total_capital = capital_config.get("virtual_capital", 100000)
        capital_available = total_capital - total_invested
        capital_usage_pct = round(total_invested / total_capital * 100, 2) if total_capital > 0 else 0

        # Today's trades
        todays_trades = await repo.get_todays_trades()
        trades_data = []
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

        # Watchlist count
        watchlist_count = await repo.get_watchlist_count()

        # Risk summary
        risk_summary = {}
        try:
            if engine is not None and hasattr(engine, "daily_risk"):
                risk_status = await engine.daily_risk.get_daily_risk_status()
                if hasattr(risk_status, "model_dump"):
                    risk_summary = risk_status.model_dump()
                elif isinstance(risk_status, dict):
                    risk_summary = risk_status
        except Exception:
            risk_summary = {"can_take_new_trades": True}

        # Engine state
        engine_state = "stopped"
        engine_mode = None
        session_id = None
        broker_name = "paper"
        if engine is not None:
            engine_state = engine.state.value
            engine_mode = engine.mode
            session_id = engine.session_id
            broker_name = engine.broker_name or "paper"

        return {
            "engine": {
                "state": engine_state,
                "mode": engine_mode,
                "broker": broker_name,
                "session_id": session_id,
                "uptime_seconds": 0,
                "scans_completed": 0,
                "signals_generated": 0,
                "trades_executed": 0,
                "errors_count": 0,
            },
            "market": engine.market_hours.get_market_status() if engine and hasattr(engine, "market_hours") else {"is_open": False, "session": "unknown", "time_to_close_seconds": 0},
            "regime": (engine.current_regime.lower() if engine and engine.current_regime else "sideways"),
            "regime_confidence": getattr(engine, "regime_confidence", 0.0) if engine else 0.0,
            "regimeConfidence": getattr(engine, "regime_confidence", 0.0) if engine else 0.0,
            "vix": (engine.vix if engine and engine.vix and engine.vix > 0 else 0),
            "nifty_price": (engine.nifty_price if engine and engine.nifty_price and engine.nifty_price > 0 else 0),
            "nifty_change": getattr(engine, "nifty_change", 0) if engine else 0,
            "active_strategies": engine.active_strategies if engine and engine.active_strategies else [],
            "activeStrategies": engine.active_strategies if engine and engine.active_strategies else [],
            "capital": {
                "total": total_capital,
                "invested": round(total_invested, 2),
                "available": round(capital_available, 2),
                "usage_pct": capital_usage_pct,
                "unrealized_pnl": round(total_unrealized_pnl, 2),
            },
            "daily_pnl": pnl_data,
            "risk": risk_summary,
            "open_positions": positions_data,
            "open_position_count": len(open_positions),
            "todays_trades": trades_data,
            "pending_opportunities": [],
            "pending_opportunity_count": 0,
            "watchlist_count": watchlist_count,
            "timestamp": datetime.now(IST).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Dashboard endpoint error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load dashboard: {str(exc)}",
        )


@router.get("/market-data")
async def get_market_data(
    engine: UltraBotEngine = Depends(get_engine)
) -> Dict:
    import time
    
    active_broker = getattr(engine, "broker_name", "") or "paper"
    is_engine_active = engine and engine.state.value in ("running", "paused", "scanning")
    source = f"{active_broker.capitalize()}" if is_engine_active else "Yahoo Finance"
    
    nifty = getattr(engine, "nifty_price", 0)
    vix = getattr(engine, "vix", 0)
    nifty_change = getattr(engine, "nifty_change", 0)

    # 1. Use engine's live broker data if active and available
    if nifty and nifty > 0 and is_engine_active:
        return {
            "nifty": nifty,
            "nifty_change": nifty_change,
            "vix": vix,
            "source": f"{active_broker.capitalize()}" if active_broker != "paper" else "Paper Engine"
        }

    # 2. If engine has a live broker attached (even if stopped), try fetching live quote directly from broker
    if engine and hasattr(engine, "broker") and engine.broker is not None and hasattr(engine.broker, "get_latest_price"):
        try:
            for sym in ("NIFTY 50", "NIFTY", "NSE:NIFTY50-INDEX"):
                b_price = await engine.broker.get_latest_price(sym)
                if b_price and b_price > 0:
                    nifty = b_price
                    source = f"{active_broker.capitalize()} (Direct)"
                    break
            for vix_sym in ("INDIAVIX", "INDIA VIX", "NSE:INDIAVIX-INDEX"):
                b_vix = await engine.broker.get_latest_price(vix_sym)
                if b_vix and b_vix > 0:
                    vix = b_vix
                    break
            if nifty > 0:
                _market_data_cache["nifty"] = nifty
                _market_data_cache["vix"] = vix
                _market_data_cache["nifty_change"] = nifty_change
                _market_data_cache["last_updated"] = time.time()
                return {
                    "nifty": nifty,
                    "nifty_change": nifty_change,
                    "vix": vix,
                    "source": source,
                }
        except Exception as b_exc:
            logger.debug("Could not fetch direct broker quote: %s", b_exc)
        
    # 3. Otherwise fetch from Yahoo Finance with a short 10 second cache
    now = time.time()
    if now - _market_data_cache["last_updated"] < 10 and _market_data_cache["nifty"] > 0:
        return {
            "nifty": _market_data_cache["nifty"],
            "nifty_change": _market_data_cache["nifty_change"],
            "vix": _market_data_cache["vix"],
            "source": _market_data_cache.get("source", "Yahoo Finance")
        }
        
    try:
        import yfinance as yf
        nifty_tk = yf.Ticker("^NSEI")
        vix_tk = yf.Ticker("^INDIAVIX")
        nifty_data = nifty_tk.history(period="2d")
        vix_data = vix_tk.history(period="1d")
        
        if len(nifty_data) >= 2:
            nifty = float(nifty_data['Close'].iloc[-1])
            prev_close = float(nifty_data['Close'].iloc[-2])
            nifty_change = round(((nifty - prev_close) / prev_close) * 100, 2)
        elif len(nifty_data) == 1:
            nifty = float(nifty_data['Close'].iloc[0])
            prev_close = float(nifty_data['Open'].iloc[0])
            nifty_change = round(((nifty - prev_close) / prev_close) * 100, 2)
            
        if not vix_data.empty:
            vix = round(float(vix_data['Close'].iloc[-1]), 2)
            
        _market_data_cache["nifty"] = nifty
        _market_data_cache["nifty_change"] = nifty_change
        _market_data_cache["vix"] = vix
        _market_data_cache["last_updated"] = now
        _market_data_cache["source"] = "Yahoo Finance"
        source = "Yahoo Finance"
    except Exception as e:
        logger.error("Failed to fetch Yahoo market data: %s", e)
        # Serve the last REAL cached values if present; otherwise return
        # honest zeros — never fabricated placeholder prices.
        nifty = _market_data_cache["nifty"]
        vix = _market_data_cache["vix"]
        nifty_change = _market_data_cache["nifty_change"]
        source = "Unavailable (feed error)"
        
    return {
        "nifty": nifty,
        "nifty_change": nifty_change,
        "vix": vix,
        "source": source
    }
