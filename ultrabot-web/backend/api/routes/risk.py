import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.dependencies import get_current_user, get_engine, get_repository
from db.repository import Repository
from core.engine import UltraBotEngine
from config.settings import settings

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

router = APIRouter(prefix="/api/risk", tags=["risk"])

# The 18 risk gate names as defined in the system (G1–G18).
GATE_NAMES = [
    "G1_MaxPositions",
    "G2_SectorConcentration",
    "G3_MaxPositionSize",
    "G4_MaxDailyTrades",
    "G5_MaxDailyLoss",
    "G6_CorrelationCheck",
    "G7_VIXFilter",
    "G8_TimeOfDay",
    "G9_PriceMismatch",
    "G10_MinConfidence",
    "G11_MaxDrawdown",
    "G12_MarginCheck",
    "G13_DuplicateSignal",
    "G14_StrategyBacktest",
    "G15_VolumeLiquidity",
    "G16_MultiTimeframe",
    "G17_CostPreCheck",
    "G18_StrategyGuard",
]

GATE_ALIASES: Dict[str, List[str]] = {
    "G1_MaxPositions": ["G1_MaxPositions", "max_open_positions_gate", "g1_max_positions"],
    "G2_SectorConcentration": ["G2_SectorConcentration", "sector_concentration_gate", "g2_sector_concentration"],
    "G3_MaxPositionSize": ["G3_MaxPositionSize", "position_size_gate", "g3_max_position_size"],
    "G4_MaxDailyTrades": ["G4_MaxDailyTrades", "daily_trade_limit_gate", "g4_max_daily_trades"],
    "G5_MaxDailyLoss": ["G5_MaxDailyLoss", "daily_loss_limit_gate", "g5_max_daily_loss"],
    "G6_CorrelationCheck": ["G6_CorrelationCheck", "correlation_check_gate", "consecutive_loss_gate", "g6_correlation_check"],
    "G7_VIXFilter": ["G7_VIXFilter", "vix_gate", "g7_vix_filter"],
    "G8_TimeOfDay": ["G8_TimeOfDay", "trade_window_gate", "g8_time_of_day"],
    "G9_PriceMismatch": ["G9_PriceMismatch", "price_mismatch_gate", "g9_price_mismatch"],
    "G10_MinConfidence": ["G10_MinConfidence", "signal_confidence_gate", "g10_min_confidence"],
    "G11_MaxDrawdown": ["G11_MaxDrawdown", "drawdown_gate", "g11_max_drawdown"],
    "G12_MarginCheck": ["G12_MarginCheck", "capital_usage_gate", "margin_check_gate", "g12_margin_check"],
    "G13_DuplicateSignal": ["G13_DuplicateSignal", "duplicate_signal_gate", "g13_duplicate_signal"],
    "G14_StrategyBacktest": ["G14_StrategyBacktest", "strategy_backtest_gate", "strategy_cooldown_gate", "g14_strategy_backtest"],
    "G15_VolumeLiquidity": ["G15_VolumeLiquidity", "volume_liquidity_gate", "g15_volume_liquidity"],
    "G16_MultiTimeframe": ["G16_MultiTimeframe", "multi_timeframe_gate", "g16_multi_timeframe"],
    "G17_CostPreCheck": ["G17_CostPreCheck", "cost_precheck_gate", "g17_cost_precheck"],
    "G18_StrategyGuard": ["G18_StrategyGuard", "strategy_guard_gate", "g18_strategy_guard"],
}


@router.get("/status")
async def get_risk_status(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Get the current daily risk status."""
    try:
        # Try engine's daily risk manager first
        if engine and hasattr(engine, "daily_risk"):
            try:
                risk_status = await engine.daily_risk.get_daily_risk_status()
                if hasattr(risk_status, "model_dump"):
                    return risk_status.model_dump()
                if isinstance(risk_status, dict):
                    return risk_status
            except Exception:
                pass

        # Fallback: build from repo data
        pnl = await repo.get_todays_pnl()
        open_positions = await repo.get_open_positions()
        consecutive = await repo.get_consecutive_losses()
        capital_in_use = await repo.get_capital_in_use()
        capital_config = settings.get_capital_config()
        total_capital = capital_config.get("virtual_capital", 100000)
        risk_config = settings.get_risk_config()

        max_consec = risk_config.get("max_consecutive_losses", 3)
        max_daily_trades = risk_config.get("max_daily_trades", 10)
        max_loss_pct = risk_config.get("max_daily_loss_pct", 3.0)
        max_dd_pct = risk_config.get("max_drawdown_pct", 10.0)
        max_open_pos = risk_config.get("max_open_positions", 5)

        net_pnl = float(pnl.get("net_pnl", 0.0))
        daily_loss_pct = round(abs(net_pnl) / total_capital * 100.0, 2) if (net_pnl < 0 and total_capital > 0) else 0.0
        drawdown = await repo.get_max_drawdown_pct()

        max_consec_hit = consecutive >= max_consec
        trade_limit_hit = pnl.get("total_trades", 0) >= max_daily_trades
        loss_limit_hit = daily_loss_pct >= max_loss_pct
        dd_hit = drawdown >= max_dd_pct
        max_pos_hit = len(open_positions) >= max_open_pos

        can_take_trades = not (max_consec_hit or trade_limit_hit or loss_limit_hit or dd_hit or max_pos_hit)
        block_reason = None
        if not can_take_trades:
            if loss_limit_hit:
                block_reason = "Daily loss limit reached"
            elif max_consec_hit:
                block_reason = "Max consecutive losses reached"
            elif trade_limit_hit:
                block_reason = "Max daily trades limit reached"
            elif dd_hit:
                block_reason = "Max drawdown limit reached"
            elif max_pos_hit:
                block_reason = "Max open positions reached"

        return {
            "date": datetime.now(IST).strftime("%Y-%m-%d"),
            "total_trades": pnl.get("total_trades", 0),
            "wins": pnl.get("wins", 0),
            "losses": pnl.get("losses", 0),
            "breakeven": pnl.get("breakeven", 0),
            "net_pnl": net_pnl,
            "net_pnl_pct": round(net_pnl / total_capital * 100.0, 2) if total_capital > 0 else 0.0,
            "daily_loss_pct": daily_loss_pct,
            "consecutive_losses": consecutive,
            "max_consecutive_losses_hit": max_consec_hit,
            "daily_trade_limit_hit": trade_limit_hit,
            "daily_loss_limit_hit": loss_limit_hit,
            "max_drawdown_pct": drawdown,
            "drawdown_limit_hit": dd_hit,
            "capital_in_use": round(capital_in_use, 2),
            "capital_usage_pct": round(capital_in_use / total_capital * 100, 2) if total_capital > 0 else 0,
            "open_positions": len(open_positions),
            "max_positions_hit": max_pos_hit,
            "in_cooloff": False,
            "cooloff_until": None,
            "vix": engine.vix if engine else None,
            "vix_above_threshold": False,
            "regime": engine.current_regime if engine else None,
            "can_take_new_trades": can_take_trades,
            "block_reason": block_reason,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get risk status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get risk status: {str(exc)}",
        )


@router.get("/gates")
async def get_risk_gates(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Get all 13 risk gate configs and their last results."""
    try:
        risk_config = settings.get_risk_config()
        gates_data = {}

        # Get gate configs from settings (supports both nested "gates" dict and flat risk_config)
        gates_config = risk_config.get("gates", {}) if isinstance(risk_config.get("gates"), dict) else {}

        # Get last risk gate results from engine's pending opportunities or recent signals
        last_results: Dict[str, Any] = {}
        if engine and hasattr(engine, "pending_opportunities"):
            # Look at the most recent opportunity for gate results
            for opp_data in engine.pending_opportunities.values():
                risk_gates = opp_data.get("risk_gates", opp_data.get("risk_gate_results", {}))
                if risk_gates:
                    last_results = risk_gates
                    break

        for gate_name in GATE_NAMES:
            aliases = GATE_ALIASES.get(gate_name, [gate_name])
            gate_cfg = {}
            for a in aliases:
                if a in gates_config:
                    gate_cfg = gates_config[a]
                    break
                elif a in risk_config:
                    gate_cfg = {a: risk_config[a]}
                    break

            gate_result = {}
            for a in aliases:
                if a in last_results:
                    gate_result = last_results[a]
                    break

            gates_data[gate_name] = {
                "name": gate_name,
                "config": gate_cfg,
                "last_result": gate_result,
                "last_passed": gate_result.get("passed", None) if gate_result else None,
            }

        # Add general limits from config
        limits = {
            "max_daily_trades": risk_config.get("max_daily_trades", 10),
            "max_daily_loss_pct": risk_config.get("max_daily_loss_pct", 3.0),
            "max_open_positions": risk_config.get("max_open_positions", 3),
            "max_position_size_pct": risk_config.get("max_position_size_pct", 25.0),
            "max_consecutive_losses": risk_config.get("max_consecutive_losses", 5),
            "max_drawdown_pct": risk_config.get("max_drawdown_pct", 5.0),
            "max_sector_concentration_pct": risk_config.get("max_sector_concentration_pct", 40.0),
            "vix_threshold": risk_config.get("vix_threshold", risk_config.get("vix_high_threshold", 20.0)),
            "vix_high_threshold": risk_config.get("vix_high_threshold", risk_config.get("vix_threshold", 20.0)),
            "vix_extreme_threshold": risk_config.get("vix_extreme_threshold", 35.0),
            "max_capital_usage_pct": risk_config.get("max_capital_usage_pct", 80.0),
            "cooloff_minutes": risk_config.get("consec_loss_cooloff_minutes", risk_config.get("cooloff_minutes", 30)),
            "consec_loss_cooloff_minutes": risk_config.get("consec_loss_cooloff_minutes", risk_config.get("cooloff_minutes", 30)),
            "min_signal_confidence": risk_config.get("min_signal_confidence", 0.6),
        }

        return {
            "gates": gates_data,
            "limits": limits,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get risk gates: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get risk gates: {str(exc)}",
        )


class RiskLimitsUpdate(BaseModel):
    """Risk limit updates. Every field has a hard min/max so a fat-fingered
    or malicious value can never reach the live risk config — FastAPI
    rejects out-of-bounds values with 422 automatically. These bounds are
    deliberately conservative (see prior risk-review discussion): the goal
    is capital preservation, not maximum theoretical flexibility.
    """
    max_daily_trades: Optional[int] = Field(default=None, ge=1, le=50)
    max_daily_loss_pct: Optional[float] = Field(default=None, ge=0.5, le=5.0)
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=6)
    max_position_size_pct: Optional[float] = Field(default=None, ge=1.0, le=15.0)
    max_consecutive_losses: Optional[int] = Field(default=None, ge=1, le=10)
    max_drawdown_pct: Optional[float] = Field(default=None, ge=1.0, le=15.0)
    max_sector_concentration_pct: Optional[float] = Field(default=None, ge=5.0, le=50.0)
    vix_high_threshold: Optional[float] = Field(default=None, ge=10.0, le=40.0)
    max_capital_usage_pct: Optional[float] = Field(default=None, ge=10.0, le=95.0)
    cooloff_minutes: Optional[int] = Field(default=None, ge=5, le=120)
    min_signal_confidence: Optional[float] = Field(default=None, ge=0.3, le=0.95)
    kelly_max_fraction: Optional[float] = Field(default=None, ge=0.03, le=0.15)
    hard_risk_pct: Optional[float] = Field(default=None, ge=0.25, le=2.0)




@router.put("/limits")
async def update_risk_limits(
    body: RiskLimitsUpdate,
    username: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Update risk limits in the live settings."""
    try:
        update_data = body.model_dump(exclude_none=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )

        # Update the in-memory raw config across appropriate sections.
        # SECTION OWNERSHIP (v0.4.2 fix — was a cross-write bug):
        #   * This route owns ONLY the `risk` and `position_sizing` sections.
        #   * The `capital` section is owned exclusively by PUT /api/settings
        #     (the Capital tab). Writing capital keys from here silently
        #     changed the user's capital allocation when they saved risk
        #     limits (config-hygiene incident) — strictly forbidden now.
        risk_config = settings._raw_config.setdefault("risk", {})
        pos_config = settings._raw_config.setdefault("position_sizing", {})

        for key, value in update_data.items():
            if key in {"kelly_max_fraction", "hard_risk_pct"}:
                pos_config[key] = value
                risk_config[key] = value
            elif key == "max_position_size_pct":
                # Risk-section keys only (max_position_size_pct is the API
                # field; max_per_position_pct is the G3 gate's legacy alias
                # for the same knob). capital.max_per_position_pct is a
                # DIFFERENT, user-owned setting and must never be touched
                # from the risk route.
                risk_config["max_position_size_pct"] = value
                risk_config["max_per_position_pct"] = value
            elif key == "vix_high_threshold":
                risk_config["vix_high_threshold"] = value
                risk_config["vix_threshold"] = value
            elif key == "max_capital_usage_pct":
                # Risk-side view only; capital.max_capital_usage_pct belongs
                # to the Capital tab (PUT /api/settings).
                risk_config["max_capital_usage_pct"] = value
            else:
                risk_config[key] = value

        # Persist to disk
        saved = settings.save()
        if not saved:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist updated limits to configuration file",
            )

        return {
            "message": "Risk limits updated successfully",
            "updated": update_data,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update risk limits: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update risk limits: {str(exc)}",
        )


@router.get("/events")
async def get_risk_events(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
    severity: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> List[Dict[str, Any]]:
    """Get risk events log."""
    try:
        if severity:
            events = await repo.get_risk_events_by_severity(severity)
        else:
            events = await repo.get_risk_events(limit=limit, offset=offset)

        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "severity": e.severity,
                "message": e.message,
                "gate_name": e.gate_name,
                "trade_id": e.trade_id,
                "session_id": e.session_id,
                "extra": e.extra if e.extra else {},
                "created_at": e.created_at,
            }
            for e in events
        ]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get risk events: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get risk events: {str(exc)}",
        )
