import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_user, get_repository
from db.repository import Repository
from models.error_log import (
    ErrorLogResponse,
    ErrorResolveRequest,
    ErrorStatsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/errors", tags=["errors"])


def _parse_json_field(value: Optional[str]) -> Any:
    """Parse a JSON string field, returning empty dict on failure."""
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


@router.get("")
async def get_errors(
    resolved: Optional[bool] = Query(None),
    severity: Optional[str] = Query(None),
    error_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Get error log with optional filters."""
    try:
        errors = await repo.get_errors(
            resolved=resolved,
            severity=severity,
            error_type=error_type,
            limit=limit,
            offset=offset,
        )
        total_count = await repo.get_errors_count(
            resolved=resolved,
            severity=severity,
            error_type=error_type,
        )

        results = []
        for e in errors:
            results.append({
                "id": e.id,
                "error_code": e.error_code,
                "error_type": e.error_type,
                "severity": e.severity,
                "what_happened": e.what_happened,
                "why_happened": e.why_happened,
                "how_to_fix": e.how_to_fix,
                "context": _parse_json_field(e.context),
                "stack_trace": e.stack_trace,
                "is_resolved": e.is_resolved,
                "resolved_at": e.resolved_at,
                "resolution_note": e.resolution_note,
                "auto_recovery_attempted": e.auto_recovery_attempted,
                "auto_recovery_result": e.auto_recovery_result,
                "session_id": e.session_id,
                "extra": _parse_json_field(e.extra),
                "created_at": e.created_at,
                "updated_at": e.updated_at,
            })

        return {
            "errors": results,
            "count": len(results),
            "total": total_count,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get errors: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch errors: {str(exc)}",
        )


@router.get("/stats", response_model=ErrorStatsResponse)
async def get_error_statistics(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> ErrorStatsResponse:
    """Get aggregate error statistics."""
    try:
        stats = await repo.get_error_stats()

        # Get severity breakdown
        severity_breakdown: Dict[str, int] = {}
        try:
            all_errors = await repo.get_errors(resolved=None, limit=500)
            for e in all_errors:
                sev = e.severity or "unknown"
                severity_breakdown[sev] = severity_breakdown.get(sev, 0) + 1
        except Exception:
            pass

        # Get recent errors
        recent = []
        try:
            recent_errors = await repo.get_errors(resolved=False, limit=5)
            for e in recent_errors:
                recent.append(ErrorLogResponse.model_validate(e))
        except Exception:
            pass

        return ErrorStatsResponse(
            total_errors=stats.get("total_errors", 0),
            unresolved=stats.get("unresolved", 0),
            today_count=stats.get("today_count", 0),
            critical_unresolved=stats.get("critical_unresolved", 0),
            by_type=stats.get("by_type", {}),
            by_severity=severity_breakdown,
            recent_errors=recent,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get error stats: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get error statistics: {str(exc)}",
        )


@router.get("/{error_id}")
async def get_error_detail(
    error_id: str,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Get detailed info for a single error."""
    try:
        error = await repo.get_error_log(error_id)
        if error is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Error '{error_id}' not found",
            )

        return {
            "id": error.id,
            "error_code": error.error_code,
            "error_type": error.error_type,
            "severity": error.severity,
            "what_happened": error.what_happened,
            "why_happened": error.why_happened,
            "how_to_fix": error.how_to_fix,
            "context": _parse_json_field(error.context),
            "stack_trace": error.stack_trace,
            "is_resolved": error.is_resolved,
            "resolved_at": error.resolved_at,
            "resolution_note": error.resolution_note,
            "auto_recovery_attempted": error.auto_recovery_attempted,
            "auto_recovery_result": error.auto_recovery_result,
            "session_id": error.session_id,
            "extra": _parse_json_field(error.extra),
            "created_at": error.created_at,
            "updated_at": error.updated_at,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get error detail: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch error detail: {str(exc)}",
        )


@router.put("/{error_id}/resolve")
async def resolve_error(
    error_id: str,
    body: Optional[ErrorResolveRequest] = None,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Mark an error as resolved."""
    try:
        note = ""
        if body is not None:
            note = body.resolution_note

        resolved = await repo.resolve_error(error_id, resolution_note=note)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Error '{error_id}' not found",
            )

        return {
            "message": f"Error '{error_id}' marked as resolved",
            "id": resolved.id,
            "resolved_at": resolved.resolved_at,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to resolve error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resolve error: {str(exc)}",
        )
