"""GET /api/v1/assistant/context/{page} — grounded page context for the assistant.

Auth: required (JWT via require_auth). Rate limit: "market" category bucket.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.v1.assistant.dispatch import build_page_context
from app.api.v1.assistant.models import PageContextResponse, PageName, PageParams
from app.auth import require_auth
from app.core.rate_limiter import get_rate_limit_dependency

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("/context/{page}", response_model=PageContextResponse)
async def get_page_context(
    page: PageName,
    params: Annotated[PageParams, Query()],
    _user: dict[str, Any] = Depends(require_auth),
    _rate: Any = Depends(get_rate_limit_dependency("market")),
) -> PageContextResponse:
    """Return compact, grounded context for one product page."""
    return await build_page_context(page, params)
