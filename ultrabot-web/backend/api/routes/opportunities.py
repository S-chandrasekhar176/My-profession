import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.dependencies import get_current_user, get_engine
from core.engine import UltraBotEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


class OpportunityConfirmRequest(BaseModel):
    segment: str = "EQ"


class OpportunitySkipRequest(BaseModel):
    reason: Optional[str] = None


class OpportunityRemindRequest(BaseModel):
    remind_after_minutes: int = 5


@router.get("")
async def get_pending_opportunities(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> List[Dict[str, Any]]:
    """Get all pending (unconfirmed) opportunities from the engine."""
    try:
        if engine is None or engine.state.value != "running":
            return []

        opportunities = list(engine.pending_opportunities.values())
        return opportunities
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get opportunities: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get opportunities: {str(exc)}",
        )


@router.get("/invalidated")
async def get_invalidated_opportunities(
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> List[Dict[str, Any]]:
    """Get recently expired/invalidated opportunities from the engine."""
    try:
        if engine is None:
            return []
        if hasattr(engine, "invalidated_opportunities"):
            return list(engine.invalidated_opportunities.values())
        return []
    except Exception as exc:
        logger.error("Failed to get invalidated opportunities: %s", exc, exc_info=True)
        return []


@router.post("/{opportunity_id}/confirm")
async def confirm_opportunity(
    opportunity_id: str,
    body: Optional[OpportunityConfirmRequest] = None,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Confirm and execute a pending opportunity."""
    try:
        if engine is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Engine not available",
            )

        if opportunity_id not in engine.pending_opportunities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Opportunity '{opportunity_id}' not found or already processed",
            )

        segment = "EQ"
        if body is not None:
            segment = body.segment

        result = await engine.confirm_opportunity(opportunity_id, segment=segment)

        if result.get("status") == "error":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=result.get("error", "Failed to confirm opportunity"),
            )

        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to confirm opportunity: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to confirm opportunity: {str(exc)}",
        )


@router.post("/{opportunity_id}/skip")
async def skip_opportunity(
    opportunity_id: str,
    body: Optional[OpportunitySkipRequest] = None,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Skip a pending opportunity."""
    try:
        if engine is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Engine not available",
            )

        if opportunity_id not in engine.pending_opportunities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Opportunity '{opportunity_id}' not found or already processed",
            )

        reason = None
        if body is not None:
            reason = body.reason

        result = await engine.skip_opportunity(opportunity_id, reason=reason)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to skip opportunity: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to skip opportunity: {str(exc)}",
        )


@router.post("/{opportunity_id}/remind")
async def remind_opportunity(
    opportunity_id: str,
    body: Optional[OpportunityRemindRequest] = None,
    username: str = Depends(get_current_user),
    engine: UltraBotEngine = Depends(get_engine),
) -> Dict[str, Any]:
    """Defer an opportunity – it stays in the pending list for later review."""
    try:
        if engine is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Engine not available",
            )

        if opportunity_id not in engine.pending_opportunities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Opportunity '{opportunity_id}' not found or already processed",
            )

        remind_after = 5
        if body is not None:
            remind_after = body.remind_after_minutes

        # Update the opportunity's remind_at field if present
        opp = engine.pending_opportunities[opportunity_id]
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        opp["remind_at"] = (datetime.now(IST) + timedelta(minutes=remind_after)).isoformat()

        return {
            "message": f"Opportunity '{opportunity_id}' deferred for {remind_after} minutes",
            "opportunity_id": opportunity_id,
            "remind_at": opp["remind_at"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to remind opportunity: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to defer opportunity: {str(exc)}",
        )
