"""Pydantic V2 models for error log requests/responses."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class ErrorLogResponse(BaseModel):
    """Response model for an error log entry."""
    id: str
    error_code: str
    error_type: str
    severity: str
    what_happened: str
    why_happened: Optional[str] = None
    how_to_fix: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    stack_trace: Optional[str] = None
    is_resolved: bool
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = None
    auto_recovery_attempted: bool
    auto_recovery_result: Optional[str] = None
    session_id: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class ErrorResolveRequest(BaseModel):
    """Request to mark an error as resolved."""
    resolution_note: str = Field(default="", max_length=2000)


class ErrorStatsResponse(BaseModel):
    """Aggregate error statistics."""
    total_errors: int = 0
    unresolved: int = 0
    today_count: int = 0
    critical_unresolved: int = 0
    by_type: Dict[str, int] = Field(default_factory=dict)
    by_severity: Dict[str, int] = Field(default_factory=dict)
    recent_errors: List[ErrorLogResponse] = Field(default_factory=list)
