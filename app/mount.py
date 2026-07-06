"""RMI Backend - router mounting.

Single source of truth for every router. Adding a new domain:
  1. Create the module with a `router = APIRouter(...)` attribute
  2. Add the import path to ROUTER_MODULES below
  3. Done - factory.create_app() picks it up automatically

Per v4.0 §T01 + ADR-0001, this replaces the hardcoded lists that
existed in the old main.py. Each mount is isolated - one failure
doesn't break the others.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Final

log = logging.getLogger(__name__)


# Canonical router list (single source of truth)
# Order matters for path resolution. v1 routers are mounted at /api/v1/*.
# Domain routers (news, reports, x402) self-prefix.
ROUTER_MODULES: Final[list[str]] = [
    # Core system routes
    "app.core.health_route",  # /health, /live, /ready
    "app.core.metrics",  # /metrics
    "app.homepage",  # /, /version
    # v1 API (thin HTTP layer)
    "app.api.v1.auth.alerts",  # /api/v1/alerts/*
    "app.api.v1.public.wallet",  # /api/v1/wallet/*
    "app.api.v1.public.token",  # /api/v1/token/*
    "app.api.v1.public.scanner",  # /api/v1/scanner/*
    "app.api.v1.rag.search",  # /api/v1/rag/v2/*
    "app.api.v1.admin.alerts_webhook",  # /api/v1/admin/alerts/webhook
    "app.api.v1.admin.glitchtip_test",  # /api/v1/_test/* (T07)
    "app.api.v1.catalog",  # /api/v1/catalog/*
    "app.api.v1.mcp",  # /mcp/* (JSON-RPC + plain JSON)
    # Domain facades (per v4.0 T28-T34)
    "app.domain.news",  # /api/v1/news/*
    "app.domain.news.admin_router",  # /api/v1/news/_admin/*
    "app.domain.reports",  # /api/v1/reports/*
    "app.domain.x402",  # /api/v1/x402/*
    # x402 MCP + Discovery + Docs
    "app.routers.x402_mcp_handler",  # /mcp/x402, /.well-known/x402
    "app.routers.x402_docs",  # /x402/docs, /x402/sandbox/*
    "app.routers.x402_growth",  # /api/v1/x402/webhooks/*, /usage, /pay, /affiliates
    "app.routers.x402_docs_v2",  # /docs/v2/quickstart, /docs/v2/api-reference
    "app.routers.stripe_integration",  # /api/v1/stripe/*, /api/v1/credits/*
    # Wire-in Wave 1 - AUDIT-2026-Q3 Phase 2
    # Mounted 2026-07-07. Re-validated "dead" routers, all import cleanly,
    # all prefixes non-conflicting with existing mounts.
    #
    # Routers (24) - total ~140 routes added:
    "app.routers.bulletin",  # /api/v1/bulletin/*  (21 routes, supabase)
    "app.routers.bulletin_board",  # /api/v1/bulletin-board/*  (22 routes, app/bulletin_board.py)
    "app.routers.intelligence_router",  # /api/v1/intelligence/*  (19 routes, n8n-fed)
    "app.routers.intelligence_panel",  # /api/v1/intelligence/*  (4 routes, /feed /latest /crypto-fear-index)
    "app.routers.chat",  # /api/v1/chat/*  (5 routes, AI chat surface)
    "app.routers.address_profiler",  # /api/v1/address-profiler/*  (1 route, scanner ext)
    "app.routers.chain_comparability",  # /api/v1/chain-compare/*  (2 routes, multi-chain)
    "app.routers.honeypot_map",  # /api/v1/honeypot-map/*  (3 routes, unique viz)
    "app.routers.label_lookup",  # /api/v1/labels/*  (1 route, CF worker enrichment)
    "app.routers.whale_alerts",  # /api/v1/whale-alerts/*  (5 routes, whale tracker)
    "app.routers.lp_health",  # /api/v1/lp-health/*  (5 routes, LP monitor)
    "app.routers.ai_stream",  # /api/v1/ai/*  (3 routes, SSE infra)
    "app.routers.rugcharts",  # /api/v1/rugcharts/*  (3 routes, RugCharts backend)
    "app.routers.bubble_maps_router",  # /api/v1/rugmaps/*  (7 routes, RugMaps viz)
    "app.routers.mev_advisory",  # /api/v1/mev-advisory/*  (2 routes, MEV warning)
    "app.routers.impersonation_detector",  # /api/v1/impersonation/*  (1 route, forensics)
    "app.routers.rug_recovery",  # /api/v1/rug-recovery/*  (2 routes, recovery indexer)
    "app.routers.laundry_matcher",  # /api/v1/laundry-matcher/*  (2 routes, copycat detect)
    "app.routers.death_clock",  # /api/v1/death-clock/*  (1 route, Token Death Clock)
    "app.routers.token_cv",  # /api/v1/token-cv/*  (2 routes, On-Chain CV)
    "app.routers.tvl_verifier",  # /api/v1/tvl-verify/*  (2 routes, DeFiLlama mismatch)
    "app.routers.contract_analyzer",  # /api/v1/contract-analyzer/*  (2 routes, SENTINEL ext)
    "app.routers.criminal_clusters",  # /api/v1/criminal-clusters/*  (3 routes, Real-CATS)
    "app.rag_metrics_api",  # /api/v1/rag/metrics  (1 route, uses LIVE rag_observability)
    #
    # Wire-in Wave 2 — AUDIT-2026-Q3.md Phase 2.2 — mounted 2026-07-07.
    # All routers below import cleanly with LIVE deps; no dead imports.
    # Mounted 2026-07-07 — 6 medium-wiring routers + news retry:
    "app.routers.news",  # /api/v1/news/{feed,headlines,sources,sentiment,categories,stats,comment,comments,internal/scanner-alert}  (9 routes, news_service.py live). Wave 1 deferred; resolved by moving domain.news catch-all to /api/v1/articles/{news_id}.
    "app.routers.market_intel_router",  # /api/v1/market/*  (28 routes, CoinGecko + DexScreener + Polymarket). Uses LIVE app.services.prediction_market_intel.
    "app.routers.prediction_market_router",  # /api/v1/prediction-markets/*  (6 routes, prediction_market_service.py live)
    "app.routers.scam_school",  # /scam-school/*  (11 routes, education platform). No prefix — paths absolute.
    "app.routers.wallet_clustering_router",  # /api/v1/wallet-clusters/*  (8 routes, wallet_clustering.py live; depends on app.{ai_router,bundle_detector,cluster_detection,entity_registry,fraud_gnn,unified_provider,wallet_clustering,spam_registry,chain_cache})
    "app.routers.security_intel",  # /api/v1/security/*  (31 routes, crypto security stack). Depends on LIVE app.{cross_chain_correlator,ml_anomaly,onchain_analyzer}.
    "app.routers.mev_sniper",  # /api/v1/mev-sniper/*  (2 routes: /signals + /chains). No internal app.* deps. MEV Sniper = premium scanner feature.
    #
    # DEFERRED (1) — needs APIRouter:
    #   "app.tool_fingerprint" — module only (no `router` APIRouter attribute).
    #   773 lines, scam-infra detection logic. Wire-in requires a thin
    #   router wrapper exposing the fingerprint functions as endpoints
    #   (e.g. POST /api/v1/tool-fingerprint/analyze). Defer until wrapper
    #   is written.
]


def mount_all(app: Any) -> int:
    """Mount every router. Returns count of successful mounts.

    Each mount is isolated - one failure does not break the rest.
    """
    mounted = 0
    for module_path in ROUTER_MODULES:
        if _try_mount(app, module_path):
            mounted += 1
    return mounted


def _try_mount(app: Any, module_path: str) -> bool:
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
            module_path,
            type(exc).__name__,
            exc,
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
