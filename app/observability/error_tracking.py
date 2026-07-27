"""
GlitchTip / Sentry-compatible error tracking.

Drop-in Sentry SDK wrapper for GlitchTip (self-hosted, Sentry-compatible).
Just change the DSN — no API changes, no vendor lock-in.

Two usage modes:
  1. **Quick init** — call `init_error_tracking()` at startup (reads env vars)
  2. **Manual capture** — use `capture_exception()` / `capture_message()` anywhere

Key principle: NEVER leak secrets. The before-send hook strips
authorization headers, X-API-Key, passwords, tokens, etc. before
events leave the process.
"""

from __future__ import annotations

import os
from typing import Any

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# ── Public re-exports from core (single source of truth) ──────────────
# The production implementation lives in app.core.observability.
# This module wraps it with a cleaner public API.

from app.core.observability import (
    capture_exception as capture_exception,  # noqa: F401
    capture_message as capture_message,  # noqa: F401
    flush_sentry as flush,  # noqa: F401
)


def init_error_tracking(
    dsn: str | None = None,
    environment: str | None = None,
    release: str | None = None,
) -> bool:
    """Initialize GlitchTip error tracking (Sentry-compatible).

    Reads DSN from GLITCHTIP_DSN (or fallback SENTRY_DSN) env var if
    not explicitly provided.  Fully opt-in — no DSN means no tracking.

    Parameters
    ----------
    dsn : str, optional
        GlitchTip DSN.  Falls back to settings.glitchtip_dsn →
        settings.sentry_dsn → GLITCHTIP_DSN env → SENTRY_DSN env.
    environment : str, optional
        Deployment environment label.  Defaults to settings.environment.
    release : str, optional
        Release version string.  Defaults to APP_VERSION env.

    Returns
    -------
    bool
        True if initialised, False if DSN not configured or sentry-sdk
        is not installed.
    """
    resolved_dsn = (
        dsn
        or settings.glitchtip_dsn
        or settings.sentry_dsn
        or os.environ.get("GLITCHTIP_DSN")
        or os.environ.get("SENTRY_DSN")
    )

    if not resolved_dsn:
        log.info("error_tracking_disabled no_glitchtip_dsn")
        return False

    try:
        from app.core.observability import setup_sentry

        ok = setup_sentry()
        if ok:
            log.info(
                "error_tracking_initialized env=%s",
                environment or settings.environment,
            )
        return ok
    except Exception as exc:
        log.warning("error_tracking_init_failed err=%s", exc)
        return False
