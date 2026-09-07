"""Shadow-analytics API (v0.4.12 — Milestone-1 measurement layer).

Read-only reporting over the shadow_outcomes dataset:
  - GET /api/analytics/shadow           grouped baseline analytics
  - GET /api/analytics/shadow/weekly    ISO-week roll-up
  - GET /api/analytics/shadow/features  feature-snapshot coverage

Every endpoint returns an HONEST state: "insufficient_data" while the
dataset is thin (percentages over tiny samples lie), never fabricated
numbers. No endpoint mutates anything.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_user, get_repository
from db.repository import Repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_ALLOWED_GROUPS = (
    "strategy", "symbol", "regime", "session", "htf_trend", "direction", "kind",
)


@router.get("/shadow")
async def get_shadow_analytics(
    group_by: str = Query("strategy", description="Bucket key for the analytics"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    realtime_only: bool = Query(True, description="Ladder rule: realtime-verified rows only"),
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
):
    """Grouped shadow-outcome analytics: which strategies work, under what
    conditions, with what risk — measured, not assumed."""
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Repository unavailable")
    if group_by not in _ALLOWED_GROUPS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"group_by must be one of {list(_ALLOWED_GROUPS)}",
        )
    try:
        return await repo.get_shadow_analytics(
            group_by=group_by, days=days, realtime_only=realtime_only
        )
    except Exception:
        logger.warning("shadow analytics query failed", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Analytics query failed")


@router.get("/shadow/weekly")
async def get_shadow_weekly(
    group_by: str = Query("strategy", description="Per-week group breakdown"),
    weeks: int = Query(8, ge=1, le=52, description="Look-back window in weeks"),
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
):
    """ISO-week roll-up of resolved shadow outcomes."""
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Repository unavailable")
    if group_by not in _ALLOWED_GROUPS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"group_by must be one of {list(_ALLOWED_GROUPS)}",
        )
    try:
        return await repo.get_shadow_weekly(group_by=group_by, weeks=weeks)
    except Exception:
        logger.warning("shadow weekly query failed", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Analytics query failed")


@router.get("/shadow/features")
async def get_feature_coverage(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
):
    """Feature-snapshot coverage of the shadow dataset (data-quality gate
    for the future ML stage — legacy rows have no features by design)."""
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Repository unavailable")
    try:
        return await repo.get_feature_coverage()
    except Exception:
        logger.warning("feature coverage query failed", exc_info=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Coverage query failed")
