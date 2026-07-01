"""RMI Backend — router mounting.

Single source of truth for every router. Adding a new domain:
  1. Create the module with a `router = APIRouter(...)` attribute
  2. Add the import path to ROUTER_MODULES below
  3. Done — factory.create_app() picks it up automatically

Per v4.0 §T01 + ADR-0001, this replaces the hardcoded lists that
existed in the old main.py. Each mount is isolated — one failure
doesn't break the others.
"""
from __future__ import annotations

import importlib
import logging
from typing import Final

log = logging.getLogger(__name__)


# ── Canonical router list (single source of truth) ─────────────────
# Order matters for path resolution. v1 routers are mounted at /api/v1/*.
# Domain routers (news, reports, x402) self-prefix.
ROUTER_MODULES: Final[list[str]] = [
    # ── Core system routes ─────────────────────────────────
    "app.core.health_route",       # /health, /live, /ready
    "app.core.metrics",             # /metrics
    "app.homepage",                 # /, /version

    # ── v1 API (thin HTTP layer) ───────────────────────────
    "app.api.v1.auth.alerts",       # /api/v1/alerts/*
    "app.api.v1.public.wallet",     # /api/v1/wallet/*
    "app.api.v1.public.token",      # /api/v1/token/*
    "app.api.v1.public.scanner",     # /api/v1/scanner/*
    "app.api.v1.rag.search",        # /api/v1/rag/v2/*
    "app.api.v1.admin.alerts_webhook",  # /api/v1/admin/alerts/webhook
    "app.api.v1.admin.glitchtip_test",  # /api/v1/_test/* (T07)
    "app.api.v1.catalog",           # /api/v1/catalog/*
    "app.api.v1.mcp",               # /mcp/* (JSON-RPC + plain JSON)

    # ── Domain facades (per v4.0 §T28-T34) ─────────────────
    "app.domain.news",              # /api/v1/news/*
    "app.domain.news.admin_router", # /api/v1/news/_admin/*
    "app.domain.reports",           # /api/v1/reports/*
    "app.domain.x402",              # /api/v1/x402/*

    # ── x402 MCP + Discovery + Docs ───────────────────────
    "app.routers.x402_mcp_handler",  # /mcp/x402, /.well-known/x402
    "app.routers.x402_docs",         # /x402/docs, /x402/sandbox/*
    "app.routers.x402_growth",       # /api/v1/x402/webhooks/*, /usage, /pay, /affiliates
    "app.routers.x402_docs_v2",      # /docs/v2/quickstart, /docs/v2/api-reference
    "app.routers.stripe_integration",  # /api/v1/stripe/*, /api/v1/credits/*
]


def mount_all(app) -> int:
    """Mount every router. Returns count of successful mounts.

    Each mount is isolated — one failure does not break the rest.
    """
    mounted = 0
    for module_path in ROUTER_MODULES:
        if _try_mount(app, module_path):
            mounted += 1
    return mounted


def _try_mount(app, module_path: str) -> bool:
    """Try to import + mount a single router. Returns True on success."""
    try:
        module = importlib.import_module(module_path)
        router = getattr(module, "router", None)
        if router is None:
            log.warning("router_missing path=%s (no `router` attribute)", module_path)
            return False
        app.include_router(router)
        log.info("router_mounted path=%s", module_path)
        return True
    except Exception as exc:
        log.warning(
            "router_mount_failed path=%s err=%s: %s",
            module_path, type(exc).__name__, exc,
        )
        return False


def register_external_router(module_path: str) -> None:
    """Allow third-party code to register additional routers at runtime.

    Plugins / MCP servers / worker modules can call this from their
    own __init__.py to add routes to the main app.
    """
    if module_path not in ROUTER_MODULES:
        ROUTER_MODULES.append(module_path)
        log.info("router_registered_external path=%s", module_path)
