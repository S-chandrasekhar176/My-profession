import logging
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_user, get_engine, get_repository
from db.repository import Repository
from core.engine import UltraBotEngine
from models.trade import (
    TradeResponse,
    TradeDetailResponse,
    PositionResponse,
    PositionModifySL,
    PositionModifyTarget,
    PositionClose,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["trades", "positions"])


# ────────────────────────────────────────
# Trade History
# ────────────────────────────────────────


@router.get("/trades", response_model=List[TradeResponse])
async def get_trades(
    status_filter: Optional[str] = Query(None, alias="status"),
    trade_date: Optional[str] = Query(None, alias="date"),
    strategy: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> List[TradeResponse]:
    """Get trade history with optional filters."""
    try:
        if status_filter:
            trades = await repo.get_trades_by_status(status_filter)
        elif trade_date:
            trades = await repo.get_trades_by_date(trade_date, limit=limit)
        elif strategy:
            trades = await repo.get_trades_by_strategy(strategy, limit=limit)
        else:
            trades = await repo.get_trades(limit=limit, offset=offset)

        return [TradeResponse.model_validate(t) for t in trades]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get trades: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch trades: {str(exc)}",
        )


@router.get("/trades/{trade_id}", response_model=TradeDetailResponse)
async def get_trade_detail(
    trade_id: str,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> TradeDetailResponse:
    """Get detailed info for a single trade."""
    try:
        trade = await repo.get_trade(trade_id)
        if trade is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Trade '{trade_id}' not found",
            )

        # Build computed fields for detail response
        entry = trade.entry_price or 0
        sl = trade.actual_sl or trade.stop_loss
        target = trade.actual_target or trade.target

        sl_distance = None
        sl_distance_pct = None
        if sl and entry > 0:
            sl_distance = round(abs(entry - sl), 2)
            sl_distance_pct = round(sl_distance / entry * 100, 2)

        target_distance = None
        target_distance_pct = None
        if target and entry > 0:
            target_distance = round(abs(target - entry), 2)
            target_distance_pct = round(target_distance / entry * 100, 2)

        risk_reward = None
        if sl_distance and target_distance and sl_distance > 0:
            risk_reward = round(target_distance / sl_distance, 2)

        invested = round(entry * (trade.quantity or 0), 2)

        # Holding duration
        holding_duration = None
        if trade.entry_time and trade.exit_time:
            from datetime import datetime
            try:
                entry_dt = datetime.fromisoformat(trade.entry_time)
                exit_dt = datetime.fromisoformat(trade.exit_time)
                total_seconds = int((exit_dt - entry_dt).total_seconds())
                hours, remainder = divmod(total_seconds, 3600)
                minutes, secs = divmod(remainder, 60)
                parts = []
                if hours > 0:
                    parts.append(f"{hours}h")
                if minutes > 0:
                    parts.append(f"{minutes}m")
                if secs > 0 or not parts:
                    parts.append(f"{secs}s")
                holding_duration = " ".join(parts)
            except (ValueError, TypeError):
                holding_duration = None

        detail = TradeDetailResponse(
            id=trade.id,
            session_id=trade.session_id,
            signal_id=trade.signal_id,
            position_id=trade.position_id,
            symbol=trade.symbol,
            direction=trade.direction,
            strategy=trade.strategy,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            stop_loss=trade.stop_loss,
            target=trade.target,
            actual_sl=trade.actual_sl,
            actual_target=trade.actual_target,
            status=trade.status,
            exit_reason=trade.exit_reason,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            brokerage=trade.brokerage,
            fees=trade.fees,
            net_pnl=trade.net_pnl,
            holding_duration_seconds=trade.holding_duration_seconds,
            notes=trade.notes,
            tags=trade.tags if trade.tags else [],
            extra=trade.extra if trade.extra else {},
            created_at=trade.created_at,
            updated_at=trade.updated_at,
            holding_duration=holding_duration,
            sl_distance=sl_distance,
            sl_distance_pct=sl_distance_pct,
            target_distance=target_distance,
            target_distance_pct=target_distance_pct,
            risk_reward=risk_reward,
            invested_amount=invested,
        )
        return detail
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get trade detail: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch trade detail: {str(exc)}",
        )


# ────────────────────────────────────────
# Open Positions
# ────────────────────────────────────────


@router.get("/positions", response_model=List[PositionResponse])
async def get_positions(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> List[PositionResponse]:
    """Get all currently open positions."""
    try:
        positions = await repo.get_open_positions()
        return [PositionResponse.model_validate(p) for p in positions]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get positions: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch positions: {str(exc)}",
        )


@router.post("/positions/{position_id}/close")
async def close_position(
    position_id: str,
    body: Optional[PositionClose] = None,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Manually close a position at the given exit price or current price."""
    try:
        # Look up the position
        position = await repo.get_position(position_id)
        if position is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Position '{position_id}' not found",
            )
        if position.status != "OPEN":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Position '{position_id}' is not open (status: {position.status})",
            )

        exit_price = (body.exit_price if body and body.exit_price and body.exit_price > 0 else None)
        if not exit_price:
            exit_price = float(position.current_price or position.entry_price or 100.0)

        exit_reason = (body.exit_reason if body and body.exit_reason else "MANUAL")

        # If engine is active, delegate to engine
        # v0.4.4 (audit round 2): the parameter is ``close_reason`` — the old
        # ``exit_reason=`` keyword raised TypeError on every call, 500-ing
        # the manual-close endpoint whenever the engine was running.
        # NOTE: no pnl is passed — _close_position (v0.4.4) recomputes both
        # pnl_amount and pnl_pct from the position's direction and the
        # effective fill, so this path no longer records pnl=0.
        if engine and hasattr(engine, "_close_position") and engine.state.value in ("running", "paused"):
            await engine._close_position(
                position=position,
                exit_price=exit_price,
                close_reason=exit_reason,
            )
        else:
            # Standalone fallback: update position and trade in repository
            from zoneinfo import ZoneInfo
            from datetime import datetime
            ist_now = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()

            await repo.update_position(
                position_id,
                status="CLOSED",
                current_price=exit_price,
            )

            if position.trade_id:
                trade = await repo.get_trade(position.trade_id)
                if trade and trade.status == "OPEN":
                    entry = float(trade.entry_price or position.entry_price or 0.0)
                    qty = int(trade.quantity or position.quantity or 1)
                    direction = str(trade.direction or position.direction or "BUY").upper()
                    pnl = (exit_price - entry) * qty if direction in ("BUY", "LONG") else (entry - exit_price) * qty
                    fees = float(trade.fees or trade.brokerage or 40.0)
                    net_pnl = pnl - fees

                    await repo.update_trade(
                        position.trade_id,
                        status="CLOSED",
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        exit_time=ist_now,
                        pnl=round(pnl, 2),
                        net_pnl=round(net_pnl, 2),
                    )

        return {"message": "Position closed successfully", "position_id": position_id, "exit_price": exit_price}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to close position: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to close position: {str(exc)}",
        )


@router.post("/positions/{position_id}/modify-sl")
async def modify_stop_loss(
    position_id: str,
    body: PositionModifySL,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Modify the stop loss of an open position."""
    try:
        position = await repo.get_position(position_id)
        if position is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Position '{position_id}' not found",
            )
        if position.status != "OPEN":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Position '{position_id}' is not open (status: {position.status})",
            )

        updated = await repo.update_position(position_id, stop_loss=body.new_sl)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update stop loss",
            )

        return {
            "message": "Stop loss updated",
            "position_id": position_id,
            "new_sl": body.new_sl,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to modify SL: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to modify stop loss: {str(exc)}",
        )


@router.post("/positions/{position_id}/modify-target")
async def modify_target(
    position_id: str,
    body: PositionModifyTarget,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Modify the target of an open position."""
    try:
        position = await repo.get_position(position_id)
        if position is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Position '{position_id}' not found",
            )
        if position.status != "OPEN":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Position '{position_id}' is not open (status: {position.status})",
            )

        updated = await repo.update_position(position_id, target=body.new_target)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update target",
            )

        return {
            "message": "Target updated",
            "position_id": position_id,
            "new_target": body.new_target,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to modify target: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to modify target: {str(exc)}",
        )
