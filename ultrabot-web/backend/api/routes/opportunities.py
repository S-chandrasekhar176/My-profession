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
    """Get recently expired/invalidated opportunities from the engine.

    v0.4.20 (user report: "expired opportunities not showing in the expired
    list"): the pre-fix route served ONLY ``engine.invalidated_opportunities``
    — an in-memory dict that (a) is wiped to {} on EVERY engine start()
    and (b) only covers this session. Any restart (or page reload after one)
    left the UI's Expired tab permanently empty even though every expiry IS
    persisted in the signals table (status='EXPIRED', rejection_reason=the
    invalidation text — written by the engine's TTL sweep v0.4.11+).

    This route now MERGES both sources, DB being the durable one:
      1. engine.invalidated_opportunities (live session, richest payload)
      2. today's signals rows with status='EXPIRED' (survives restarts)
    Deduped by opportunity id, newest first, capped at 100.
    """
    merged: Dict[str, Dict[str, Any]] = {}

    def _add(opp_id: str, item: Dict[str, Any]) -> None:
        if not opp_id or not isinstance(item, dict):
            return
        if opp_id in merged:
            merged[opp_id].update(item)
        else:
            merged[opp_id] = item

    # Source 1: engine memory (current session)
    try:
        if engine is not None and hasattr(engine, "invalidated_opportunities"):
            for opp_id, item in dict(engine.invalidated_opportunities).items():
                _add(str(opp_id), item)
    except Exception as exc:
        logger.warning("Failed to read engine.invalidated_opportunities: %s", exc)

    # Source 2: DB — today's EXPIRED signal rows (restart-durable)
    try:
        if engine is not None:
            async with engine._repo_context() as repo:
                if repo is not None:
                    todays = await repo.get_todays_signals()
                    for sig in todays:
                        if str(getattr(sig, "status", "")).upper() != "EXPIRED":
                            continue
                        try:
                            sdata = getattr(sig, "signal_data", "{}") or "{}"
                            if isinstance(sdata, str):
                                import json as _json
                                sdata = _json.loads(sdata)
                            opp_snap = sdata.get("opportunity", {}) if isinstance(sdata, dict) else {}
                        except Exception:
                            opp_snap = {}
                        opp_id = str(
                            opp_snap.get("id")
                            or getattr(sig, "id", "")
                        )
                        raw_dir = str(getattr(sig, "direction", "") or opp_snap.get("direction", "")).upper()
                        _add(opp_id, {
                            "id": opp_id,
                            "symbol": getattr(sig, "symbol", "") or opp_snap.get("symbol", ""),
                            # UI direction vocabulary: BUY/SELL (DB stores LONG/SHORT)
                            "direction": "SELL" if raw_dir in ("SHORT", "SELL") else "BUY",
                            "strategy": getattr(sig, "strategy", "") or opp_snap.get("strategy", ""),
                            "entry": getattr(sig, "entry_price", None) or opp_snap.get("entry_price", 0),
                            "entry_price": getattr(sig, "entry_price", None) or opp_snap.get("entry_price", 0),
                            "stop_loss": getattr(sig, "stop_loss", None) or opp_snap.get("stop_loss", 0),
                            "target": getattr(sig, "target", None) or opp_snap.get("target", 0),
                            "confidence": getattr(sig, "confidence", 0) or opp_snap.get("confidence", 0),
                            "created_at": getattr(sig, "created_at", "") or opp_snap.get("created_at", ""),
                            "status": "expired",
                            "invalidation_reason": getattr(sig, "rejection_reason", None) or "Setup expired (TTL elapsed)",
                            "invalidated_at": getattr(sig, "updated_at", "") or getattr(sig, "created_at", ""),
                            "signal_id": str(getattr(sig, "id", "") or ""),
                        })
    except Exception as exc:
        logger.warning("Failed to read EXPIRED signals from DB: %s", exc)

    items = list(merged.values())
    # Newest invalidations first
    items.sort(key=lambda d: str(d.get("invalidated_at") or d.get("created_at") or ""), reverse=True)
    return items[:100]


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
