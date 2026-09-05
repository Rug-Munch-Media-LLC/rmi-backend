"""RMI Backend - lifespan (startup/shutdown).

Per v4.0 §T01 + ADR-0001, lifespan wires up cross-cutting concerns:
  - Structured logging (structlog)
  - Typed error handlers (AppError -> JSON)
  - Event bus + CQRS mediator
  - Long-term memory (M1: fact_store seed)
  - Observability (M4: OpenTelemetry + Langfuse)

All initializations are isolated - one failure does not break startup.
Per v3 unfuck rule #7: add_middleware must be at module level, NOT
in lifespan. So this file only does setup() calls and yield.

Cleanup: HTTP client, Langfuse, Sentry, OTel are flushed on shutdown.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Wire cross-cutting at startup. Failures are logged, never fatal."""
    log.info("rmi_backend_starting")

    # 0. Security configuration checks
    try:
        admin_key = os.getenv("ADMIN_API_KEY", "")
        if not admin_key:
            log.critical("ADMIN_API_KEY not configured - admin endpoints will reject all requests")
        else:
            log.info("admin_api_key_configured")
        rmi_token = os.getenv("RMI_AUTH_TOKEN", "")
        if not rmi_token:
            log.critical("RMI_AUTH_TOKEN not configured - API auth is disabled")
        else:
            log.info("rmi_auth_token_configured")
    except Exception as exc:
        log.warning("security_config_check_failed err=%s", exc)

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

    # 3. Event bus + CQRS mediator (added 2026-07-24)
    try:
        from app.core.events import get_event_bus, register_core_subscribers

        bus = get_event_bus()
        register_core_subscribers()
        log.info("event_bus_initialized subscribers=%d", len(bus._subscriptions))
    except Exception as exc:
        log.warning("event_bus_init_failed err=%s", exc)

    try:
        from app.core.cqrs import get_mediator

        get_mediator()
        log.info("cqrs_mediator_initialized")
    except Exception as exc:
        log.warning("cqrs_mediator_init_failed err=%s", exc)

    # 4. M1 - long-term memory (fact_store seed)
    try:
        from app.agents.fact_store import seed_facts

        seeded = await seed_facts()
        log.info("fact_store_seeded count=%d", seeded)
    except Exception as exc:
        log.info("fact_store_seed_skipped err=%s", exc)

    # 5. M4 - OpenTelemetry tracing
    try:
        from app.core.tracing import setup_otel  # type: ignore[attr-defined]  # optional; guarded by try/except

        otel_ok = setup_otel()
        log.info("otel_init ok=%s", otel_ok)
    except Exception as exc:
        log.info("otel_init_failed err=%s", exc)

    # 6. M4 - Langfuse (LLM tracing)
    try:
        from app.core.langfuse import init_langfuse

        lf_ok = init_langfuse()
        log.info("langfuse_init ok=%s", lf_ok)
    except Exception as exc:
        log.info("langfuse_init_failed err=%s", exc)

    # 7. T07 - GlitchTip error tracking
    try:
        from app.core.observability import setup_sentry

        sentry_ok = setup_sentry()
        log.info("sentry_init ok=%s", sentry_ok)
    except Exception as exc:
        log.info("sentry_init_failed err=%s", exc)

    # 8. T12 - CertStream phishing domain monitor (background task)
    certstream_task = None
    try:
        from app.domains.threat.certstream_listener import start_listener

        certstream_task = await start_listener()
        log.info("certstream_started has_task=%s", certstream_task is not None)
    except Exception as exc:
        log.info("certstream_start_failed err=%s", exc)

    # 9. Webhook engine (poll-based webhook dispatch). Optional — disabled via
    # WEBHOOK_ENGINE_ENABLED=false. Failure to start never breaks app startup.
    webhook_engine = None
    if os.getenv("WEBHOOK_ENGINE_ENABLED", "true").lower() not in ("false", "0", "no", "off"):
        try:
            from app.domains.scanner.webhooks.engine import WebhookEngine

            webhook_engine = WebhookEngine()
            await webhook_engine.start()
            log.info("webhook_engine_started")
        except Exception as exc:
            webhook_engine = None
            log.warning("webhook_engine_start_failed err=%s", exc)
    else:
        log.info("webhook_engine_disabled")

    # 10. Background news cache — seeds on startup, refreshes every 5 min.
    #    This fixes the "headlines: null" issue by always serving from Redis.
    news_refresh_task = None
    try:
        import asyncio as _asyncio

        from app.databus.news_provider import refresh_news_cache

        async def _news_cache_loop() -> None:
            """Run news cache refresh immediately, then every 5 minutes."""
            await _asyncio.sleep(5)  # Let app start first
            while True:
                try:
                    await refresh_news_cache()
                except Exception as exc:
                    log.warning("news_cache_refresh_failed err=%s", exc)
                await _asyncio.sleep(300)  # 5 minutes

        news_refresh_task = _asyncio.create_task(_news_cache_loop())
        log.info("news_cache_background_started")
    except Exception as exc:
        log.warning("news_cache_start_failed err=%s", exc)

    # 11. Background RAG news ingestion — runs every 30 minutes.
    #    Pulls from 200+ RSS feeds through aggregate_all_news into Qdrant news_v2.
    #    This keeps the chatbot's RAG context fresh with the latest crypto news.
    rag_ingest_task = None
    try:
        import asyncio as _asyncio

        from app.databus.rag_news_realtime import rag_news_realtime

        async def _rag_ingest_loop() -> None:
            """Ingest news into Qdrant every 30 minutes on a staggered schedule."""
            await _asyncio.sleep(30)  # Let app settle before first ingest
            while True:
                try:
                    result = await rag_news_realtime()
                    ingested = result.get("ingested", 0)
                    errors = result.get("errors", [])
                    if errors:
                        log.warning("rag_news_ingest_partial ingested=%d errors=%d", ingested, len(errors))
                    else:
                        log.info("rag_news_ingest_complete ingested=%d", ingested)
                except Exception as exc:
                    log.warning("rag_news_ingest_failed err=%s", exc)
                await _asyncio.sleep(1800)  # 30 minutes

        rag_ingest_task = _asyncio.create_task(_rag_ingest_loop())
        log.info("rag_news_background_started")
    except Exception as exc:
        log.warning("rag_news_background_start_failed err=%s", exc)

    # 12. Background X (Twitter) shill ingest — pulls tracked-KOL tweets via
    #     the ApiPass probe and writes shill evidence (Qdrant threat_intel_v2
    #     + shill_evidence SQLite). Idempotent on tweet ID; budget-bounded by
    #     the ApiPass governor inside apipass_x. Skipped entirely when no
    #     ApiPass key is configured.
    x_shill_task = None
    try:
        import asyncio as _asyncio
        import os as _os

        from app.domains.shill import apipass_x as _apipass
        from app.domains.shill.x_ingest import ingest_x_shill

        if _apipass.api_key_configured():
            _x_interval = max(300, int(_os.getenv("RMI_X_SHILL_INGEST_INTERVAL", "3600")))

            async def _x_shill_loop() -> None:
                """Ingest KOL X tweets into shill evidence every interval."""
                await _asyncio.sleep(60)  # Let app settle before first pull
                while True:
                    try:
                        await ingest_x_shill()
                    except Exception as exc:
                        log.warning("x_shill_ingest_failed err=%s", exc)
                    await _asyncio.sleep(_x_interval)

            x_shill_task = _asyncio.create_task(_x_shill_loop())
            log.info("x_shill_ingest_background_started interval_s=%d", _x_interval)
        else:
            log.info("x_shill_ingest_background_skipped no_apipass_key")
    except Exception as exc:
        log.warning("x_shill_ingest_background_start_failed err=%s", exc)

    # 13. Scheduled Telegram channel ingest — sweeps monitored channels
    #     (TELEGRAM_MONITORED_CHANNELS env, falling back to the enabled
    #     news-kind registry channels) every 15 minutes and embeds the
    #     normalized articles into Qdrant news_v2.
    tg_ingest_task = None
    try:
        import asyncio as _asyncio
        import os as _os

        from app.domains.news_ingest.scheduler import start_ingest_scheduler

        _tg_interval = max(5, int(_os.getenv("RMI_TG_INGEST_INTERVAL_MINUTES", "15")))
        tg_ingest_task = await start_ingest_scheduler(_tg_interval, first_delay_seconds=90)
        log.info("tg_news_ingest_background_started interval_min=%d", _tg_interval)
    except Exception as exc:
        log.warning("tg_news_ingest_background_start_failed err=%s", exc)

    # 14. Scheduled Pryscraper news ingest — searches the Pry web
    #     intelligence engine for crypto news every 30 minutes and embeds
    #     the quality-gated articles into Qdrant news_v2.
    pry_ingest_task = None
    try:
        import asyncio as _asyncio
        import os as _os

        from app.domains.news_ingest.scheduler import start_pryscraper_scheduler

        _pry_interval = max(5, int(_os.getenv("RMI_PRY_INGEST_INTERVAL_MINUTES", "30")))
        pry_ingest_task = await start_pryscraper_scheduler(_pry_interval, first_delay_seconds=120)
        log.info("pryscraper_news_ingest_background_started interval_min=%d", _pry_interval)
    except Exception as exc:
        log.warning("pryscraper_news_ingest_background_start_failed err=%s", exc)

    yield

    # -- Shutdown --------------------------------------------------------------
    # Cleanup order: network clients first, then observability flushes.
    try:
        from app.core.dependencies import close_http_client

        await close_http_client()
    except Exception as exc:
        log.warning("http_client_close_failed err=%s", exc)

    # Telegram headless client shutdown
    try:
        from app.domains.telegram.headless import shutdown_headless_client

        await shutdown_headless_client()
        log.info("telegram_headless_client_shutdown")
    except Exception as exc:
        log.debug("telegram_headless_shutdown_skipped err=%s", exc)

    try:
        from app.domains.threat.certstream_listener import stop_listener

        await stop_listener(certstream_task)
    except (AttributeError, ImportError) as e:
        log.debug(f"_x_shill_loop failed: {e}")

    # RAG news ingestion background task shutdown
    if rag_ingest_task is not None:
        try:
            rag_ingest_task.cancel()
            log.info("rag_news_ingest_background_cancelled")
        except Exception as exc:
            log.debug("rag_news_ingest_cancel_failed err=%s", exc)

    # X shill ingest background task shutdown
    if x_shill_task is not None:
        try:
            x_shill_task.cancel()
            log.info("x_shill_ingest_background_cancelled")
        except Exception as exc:
            log.debug("x_shill_ingest_cancel_failed err=%s", exc)

    # Scheduled Telegram channel ingest background task shutdown
    if tg_ingest_task is not None:
        try:
            from app.domains.news_ingest.scheduler import stop_ingest_scheduler

            await stop_ingest_scheduler(tg_ingest_task)
        except Exception as exc:
            log.debug("tg_news_ingest_cancel_failed err=%s", exc)

    # Scheduled Pryscraper news ingest background task shutdown
    if pry_ingest_task is not None:
        try:
            pry_ingest_task.cancel()
            log.info("pryscraper_news_ingest_background_cancelled")
        except Exception as exc:
            log.debug("pryscraper_news_ingest_cancel_failed err=%s", exc)

    # Webhook engine shutdown (only if it was started)
    if webhook_engine is not None:
        try:
            await webhook_engine.stop()
            log.info("webhook_engine_shutdown")
        except Exception as exc:
            log.warning("webhook_engine_stop_failed err=%s", exc)

    # News cache background task shutdown
    if news_refresh_task is not None:
        try:
            news_refresh_task.cancel()
            log.info("news_cache_background_cancelled")
        except Exception as exc:
            log.debug("news_cache_cancel_failed err=%s", exc)

    try:
        from app.core.langfuse import flush_langfuse
        from app.core.observability import flush_sentry
        from app.core.tracing import shutdown_otel  # type: ignore[attr-defined]  # optional; guarded by try/except

        flush_sentry(timeout=2.0)
        flush_langfuse()
        shutdown_otel()
    except (AttributeError, ImportError) as e:
        log.debug(f"_x_shill_loop failed: {e}")
    log.info("rmi_backend_shutdown")
