"""F&O Options Chain & Greeks API Routes (Phase 2: F&O Data Foundation)."""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_user, get_repository
from db.repository import Repository
from options.greeks import GreeksCalculator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/snapshots")
async def get_option_snapshots(
    symbol: str = Query("NIFTY", description="Underlying symbol (e.g. NIFTY, BANKNIFTY)"),
    limit: int = Query(20, ge=1, le=100, description="Max snapshots to retrieve"),
    repo: Repository = Depends(get_repository),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve recorded historical option chain snapshots."""
    try:
        snapshots = await repo.get_latest_option_snapshots(underlying_symbol=symbol, limit=limit)
        items = []
        for s in snapshots:
            try:
                parsed_chain = json.loads(s.chain_json) if getattr(s, "chain_json", None) else []
            except Exception:
                parsed_chain = []

            items.append({
                "id": s.id,
                "timestamp": s.timestamp,
                "underlying_symbol": s.underlying_symbol,
                "spot_price": s.spot_price,
                "expiry": s.expiry,
                "expiry_epoch": s.expiry_epoch,
                "atm_strike": s.atm_strike,
                "max_pain": s.max_pain,
                "pcr": s.pcr,
                "total_ce_oi": s.total_ce_oi,
                "total_pe_oi": s.total_pe_oi,
                "tier": s.tier,
                "chain_length": len(parsed_chain),
                "created_at": s.created_at,
            })
        return {
            "symbol": symbol.upper(),
            "count": len(items),
            "snapshots": items,
        }
    except Exception as exc:
        logger.error("Failed to fetch option snapshots for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching snapshots: {exc}",
        )


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot_detail(
    snapshot_id: str,
    repo: Repository = Depends(get_repository),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve full strike breakdown of a specific option snapshot."""
    try:
        from db.migrations import OptionSnapshot
        from sqlalchemy import select

        stmt = select(OptionSnapshot).where(OptionSnapshot.id == snapshot_id)
        result = await repo.session.execute(stmt)
        snapshot = result.scalar_one_or_none()
        if not snapshot:
            raise HTTPException(status_code=404, detail="Option snapshot not found")

        try:
            chain_data = json.loads(snapshot.chain_json) if snapshot.chain_json else []
        except Exception:
            chain_data = []

        return {
            "id": snapshot.id,
            "timestamp": snapshot.timestamp,
            "underlying_symbol": snapshot.underlying_symbol,
            "spot_price": snapshot.spot_price,
            "expiry": snapshot.expiry,
            "expiry_epoch": snapshot.expiry_epoch,
            "atm_strike": snapshot.atm_strike,
            "max_pain": snapshot.max_pain,
            "pcr": snapshot.pcr,
            "total_ce_oi": snapshot.total_ce_oi,
            "total_pe_oi": snapshot.total_pe_oi,
            "tier": snapshot.tier,
            "chain_data": chain_data,
            "created_at": snapshot.created_at,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/verify-greeks")
async def verify_greeks_endpoint(
    payload: Dict[str, Any],
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Verify broker-provided Greeks against Black-Scholes theoreticals."""
    spot = float(payload.get("spot_price", 0.0) or 0.0)
    strike = float(payload.get("strike", 0.0) or 0.0)
    tte_years = float(payload.get("tte_years", 0.02) or 0.02)
    iv = float(payload.get("iv", 0.15) or 0.15)
    option_type = str(payload.get("option_type", "CE")).upper()
    broker_greeks = payload.get("broker_greeks", {})

    if spot <= 0 or strike <= 0:
        raise HTTPException(status_code=400, detail="Invalid spot or strike price")

    calc = GreeksCalculator()
    theo_greeks = calc.all_greeks(
        S=spot,
        K=strike,
        T=tte_years,
        sigma=iv,
        option_type=option_type,
    )
    verification = calc.verify_greeks(
        broker_greeks=broker_greeks,
        theoretical_greeks=theo_greeks,
        tolerance=float(payload.get("tolerance", 0.20)),
    )
    return {
        "theoretical_greeks": theo_greeks,
        "broker_greeks": broker_greeks,
        "verification": verification,
    }


@router.post("/theta-budget")
async def check_theta_budget_endpoint(
    payload: Dict[str, Any],
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Theta-Budget Gate: Verify expected move covers time decay and execution costs."""
    expected_move = float(payload.get("expected_move_points", 0.0) or 0.0)
    delta = float(payload.get("delta", 0.5) or 0.5)
    daily_theta = float(payload.get("daily_theta", 10.0) or 10.0)
    costs = float(payload.get("round_trip_cost_per_share", 2.0) or 2.0)
    holding_fraction = float(payload.get("holding_fraction_of_day", 0.5) or 0.5)

    calc = GreeksCalculator()
    result = calc.check_theta_budget(
        expected_move_points=expected_move,
        delta=delta,
        daily_theta=daily_theta,
        round_trip_cost_per_share=costs,
        holding_fraction_of_day=holding_fraction,
    )
    return result
