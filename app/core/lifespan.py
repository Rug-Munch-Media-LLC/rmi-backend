"""RMI Backend - Application lifespan (startup/shutdown events)."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import bind_trace_context, get_logger
from app.core.secrets import get_secret_or_none

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)

# Keep strong references to background tasks so they are never garbage-collected.
_background_tasks: list[Any] = []

# Background task imports (lazy, at startup time)


async def lifespan(app: FastAPI) -> Any:
    """Application lifespan: startup checks, background tasks, graceful shutdown."""
    # Bind permanent service-level trace context for background tasks
    bind_trace_context(service="rmi-backend")

    vault_pw = os.getenv("WALLET_VAULT_PASSWORD", "").strip()
    if not vault_pw:
        raise RuntimeError(
            "CRITICAL: WALLET_VAULT_PASSWORD environment variable is missing or empty. "
            "The backend will not start without it to prevent silent wallet key loss."
        )

    # Fail startup loudly when the RAG API key is unset — otherwise the
    # RAG scam-check integration silently degrades to pattern matching.
    from app.config import settings

    rag_api_key = (settings.rmi_rag_api_key or "").strip()
    if not rag_api_key:
        raise RuntimeError(
            "CRITICAL: RMI_RAG_API_KEY is missing or empty. The RAG scam-check "
            "integration cannot authenticate to rugbrain-api; the backend will "
            "not start to prevent silent degradation of scam checks."
        )

    # Phoenix (Arize) RAG observability — fail-open, never blocks startup.
    # Guarded by PHOENIX_ENABLED; setup_phoenix() itself never raises.
    try:
        from app.integrations.rag_helpers.tracing import setup_phoenix

        if setup_phoenix():
            logger.info("phoenix_tracing_started")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("phoenix_tracing_startup_failed err=%s", exc)

    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20), timeout=10.0
    )

    await _verify_indexes(app)
    await _start_background_tasks(app)

    yield  # App runs here

    await app.state.http_client.aclose()
    logger.info("shutdown_complete")


async def _start_background_tasks(app: FastAPI) -> None:
    """Start all background monitoring / cleanup tasks."""
    tasks = [
        ("facilitator_health", "app.routers.facilitator_health", "health_check_loop", "60s"),
        ("status_page", "app.routers.status_page", "status_monitor_loop", "30s"),
        ("webhook_dispatcher", "app.routers.webhook_dispatcher", "webhook_dispatcher_loop", "5s"),
        ("x402_trial_cleanup", "app.routers.x402_enforcement", "trial_cleanup_loop", "hourly"),
        ("auto_sweep", "app.wallet_manager_v2", "auto_sweep_loop", ""),
    ]

    for name, module_path, func_name, interval in tasks:
        try:
            mod = __import__(module_path, fromlist=[func_name])
            fn = getattr(mod, func_name)
            _background_tasks.append(asyncio.create_task(fn()))
            logger.info("background_task_started", task=name, interval=interval)
        except Exception as e:
            logger.warning("background_task_failed", task=name, error=str(e))

    # Cache warmer (passes app instance). Loaded by module path (string)
    # like the background tasks above: core/lifespan is the composition
    # root and must not statically import the services-tier databus
    # package — that would let app.core reach app.domains through the
    # databus client, violating the core-is-lowest contract.
    try:
        import importlib

        _databus_core = importlib.import_module("app.databus.core")

        cache_warm_loop = getattr(_databus_core, "cache_warm_loop", None)
        if cache_warm_loop:
            _background_tasks.append(asyncio.create_task(cache_warm_loop(app)))
            logger.info("background_task_started", task="databus_cache_warmer", interval="")
        else:
            logger.warning("background_task_failed", task="databus_cache_warmer", error="cache_warm_loop not found")
    except Exception as e:
        logger.warning("background_task_failed", task="databus_cache_warmer", error=str(e))


async def _verify_indexes(app: FastAPI) -> None:
    """Verify and create database indexes on startup."""
    try:
        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_key = (
            get_secret_or_none("SUPABASE_SERVICE_KEY", "")
            or get_secret_or_none("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY", "")
        )
        if not supabase_url or not supabase_key:
            return

        indexes = [
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scan_results_created_at ON scan_results(created_at DESC);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scan_results_token_address ON scan_results(token_address);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_whale_alerts_created_at ON whale_alerts(created_at DESC);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_security_alerts_created_at ON security_alerts(created_at DESC);",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_x402_payments_tx_hash ON x402_payments(tx_hash);",
        ]

        for sql in indexes:
            try:
                res = await app.state.http_client.post(
                    f"{supabase_url}/rest/v1/rpc/exec_sql",
                    json={"query": sql},
                    headers={
                        "apikey": supabase_key,
                        "Authorization": f"Bearer {supabase_key}",
                        "Content-Type": "application/json",
                    },
                )
                if res.status_code in [200, 204]:
                    logger.info("index_verified", table=sql.split("ON ")[1].split("(")[0].strip())
                else:
                    logger.warning("index_skipped", status=res.status_code, sql=sql[:50])
            except Exception as e:
                logger.warning("index_verify_failed", error=str(e))
    except Exception as e:
        logger.warning("index_verification_skipped", error=str(e))
