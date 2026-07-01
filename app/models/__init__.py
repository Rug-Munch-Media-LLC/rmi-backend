"""Shared Pydantic v2 models used across modules.

Prefer domain-local models in `app/domain/<name>/models.py` — this module
is only for cross-domain shared types (e.g., pagination envelope, error body).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error envelope returned by all endpoints."""

    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable message")
    details: dict | None = Field(default=None, description="Optional context")
    correlation_id: str | None = Field(default=None, description="Request ID for tracing")


class PaginationParams(BaseModel):
    """Cursor-based pagination (preferred over offset/limit at scale)."""

    cursor: str | None = Field(default=None, description="Opaque pagination cursor")
    limit: int = Field(default=50, ge=1, le=200, description="Max items per page")


class Page(BaseModel):
    """Paginated response envelope."""

    items: list = Field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = Field(default=None, description="Total if known, None otherwise")
