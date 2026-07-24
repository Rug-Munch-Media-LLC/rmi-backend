"""T07 GlitchTip test endpoint.

POST /api/v1/_test/glitchtip
  {"type": "error", "message": "test error"}
  POST /api/v1/_test/exception
    → triggers a real exception, captured by GlitchTip
  POST /api/v1/_test/message
    → captures an info-level message

Used for:
  - Verifying the GlitchTip pipeline works
  - Smoke testing after deploys
  - Demonstrating the secret-scrubbing before_send hook
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/_test", tags=["test"])

class GlitchtipTestRequest(BaseModel):
    type: str = "error"  # error | exception | message
    message: str = "test event from RMI"
    secret: str | None = None  # should be REDACTED in Sentry

@router.post("/glitchtip")
async def test_glitchtip(req: GlitchtipTestRequest) -> dict:
    """Trigger a test event. Tests the secret-scrubbing before_send hook."""
    if req.type == "exception":
        try:
            raise ValueError(req.message)
        except Exception as e:
            try:
                from app.core.observability import capture_exception

                capture_exception(e, secret=req.secret, route="/api/v1/_test/glitchtip")
            except ImportError:
                log.exception("test_exception_no_sentry")
            return {"captured": "exception", "message": req.message}
    if req.type == "message":
        try:
            from app.core.observability import capture_message

            capture_message(req.message, level="warning", secret=req.secret)
        except ImportError:
            log.warning(f"test_message_no_sentry: {req.message}")
        return {"captured": "message", "message": req.message}
    # default: error log + capture
    log.error(f"test_error: {req.message} (secret={req.secret})")
    try:
        from app.core.observability import capture_message

        capture_message(req.message, level="error", secret=req.secret)
    except ImportError:
        pass
    return {"captured": "error", "message": req.message, "secret_redacted_in_sentry": True}
