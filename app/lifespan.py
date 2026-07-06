"""RMI Backend - lifespan (startup/shutdown).

Per v4.0 §T01 + ADR-0001, lifespan wires up cross-cutting concerns:
  - Structured logging (structlog)
  - Typed error handlers (AppError → JSON)
  - Long-term memory (M1: fact_store seed)
  - Observability (M4: OpenTelemetry + Langfuse)

All initializations are isolated - one failure does not break startup.
Per v3 unfuck rule #7: add_middleware must be at module level, NOT
in lifespan. So this file only does setup() calls and yield.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Wire cross-cutting at startup. Failures are logged, never fatal."""
    log.info("rmi_backend_starting")

    # 1. Structured logging
    try:
        from app.core.logging import setup_logging
        setup_logging(os.getenv("LOG_LEVEL", "INFO"))
        log.info("logging_initialized")
    except Exception as exc:
        log.warning("logging_setup_failed err=%s", exc)

    # 2. Typed error handlers
    try:
        from app.core.errors import register_error_handlers
        register_error_handlers(_app, debug=os.getenv("ENVIRONMENT") == "dev")
        log.info("error_handlers_initialized")
    except Exception as exc:
        log.warning("error_handlers_skipped err=%s", exc)

    # 3. M1 - long-term memory (fact_store seed)
    try:
        from app.agents.fact_store import seed_facts
        seeded = await seed_facts()
        log.info("fact_store_seeded count=%d", seeded)
    except Exception as exc:
        log.info("fact_store_seed_skipped err=%s", exc)

    # 4. M4 - OpenTelemetry tracing
    try:
        from app.core.tracing import setup_otel
        otel_ok = setup_otel()
        log.info("otel_init ok=%s", otel_ok)
    except Exception as exc:
        log.info("otel_init_failed err=%s", exc)

    # 5. M4 - Langfuse (LLM tracing)
    try:
        from app.core.langfuse import init_langfuse
        lf_ok = init_langfuse()
        log.info("langfuse_init ok=%s", lf_ok)
    except Exception as exc:
        log.info("langfuse_init_failed err=%s", exc)

    # 6. T07 - GlitchTip error tracking
    try:
        from app.core.observability import setup_sentry
        sentry_ok = setup_sentry()
        log.info("sentry_init ok=%s", sentry_ok)
    except Exception as exc:
        log.info("sentry_init_failed err=%s", exc)

    # 7. T12 - CertStream phishing domain monitor (background task)
    certstream_task = None
    try:
        from app.domain.threat.certstream_listener import start_listener
        certstream_task = await start_listener()
        log.info("certstream_started has_task=%s", certstream_task is not None)
    except Exception as exc:
        log.info("certstream_start_failed err=%s", exc)

    yield

    # Shutdown
    try:
        from app.domain.threat.certstream_listener import stop_listener
        await stop_listener(certstream_task)
    except Exception:
        pass
    try:
        from app.core.langfuse import flush_langfuse
        from app.core.observability import flush_sentry
        from app.core.tracing import shutdown_otel
        flush_sentry(timeout=2.0)
        flush_langfuse()
        shutdown_otel()
    except Exception:
        pass
    log.info("rmi_backend_shutdown")
