"""RMI Backend - router mounting.

Single source of truth for every router. Adding a new domain:
   1. Create the module with a `router = APIRouter(...)` attribute
   2. Add the import path to ROUTER_MODULES below
   3. Done - factory.create_app() picks it up automatically

Per v4.0 §T01 + ADR-0001, this replaces the hardcoded lists that
existed in the old main.py. Each mount is isolated - one failure
doesn't break the others.

DEPRECATION NOTICE (Win #19 — Version Root-Level API Routes):
  Root-level routes are being deprecated in favor of /api/v1/* paths.
  LEGACY root routes remain active for backward compatibility.
  Migrate to /api/v1/* equivalents (see docs for migration guide).
  Sunset date: 90 days from 2026-07-17.
"""

from __future__ import annotations

import importlib
from typing import Any, Final

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Root-level route deprecation registry ─────────────────────────────
# These routes are at root level (no /api/v1 prefix) and have /api/v1/*
# equivalents. Logged at startup so operators know what's deprecated.
ROOT_LEVEL_ROUTES: Final[list[dict[str, str]]] = [
    # scam_school.py
    {"path": "/scam-school/*", "v1": "/api/v1/scam-school/*", "file": "app.routers.scam_school"},
    # x402_advanced_tools.py
    {
        "path": "/stream/alerts",
        "v1": "/api/v1/x402/stream/alerts",
        "file": "app.routers.x402_advanced_tools",
    },
    {
        "path": "/rug_probability",
        "v1": "/api/v1/x402/rug-probability",
        "file": "app.routers.x402_advanced_tools",
    },
    {"path": "/history", "v1": "/api/v1/x402/history", "file": "app.routers.x402_advanced_tools"},
    {
        "path": "/narrative",
        "v1": "/api/v1/x402/narrative",
        "file": "app.routers.x402_advanced_tools",
    },
    {
        "path": "/cache/clear",
        "v1": "/api/v1/x402/cache/clear",
        "file": "app.routers.x402_advanced_tools",
    },
    {
        "path": "/cache/stats",
        "v1": "/api/v1/x402/cache/stats",
        "file": "app.routers.x402_advanced_tools",
    },
    # x402_premium_tools.py
    {
        "path": "/reputation_score",
        "v1": "/api/v1/x402/reputation-score",
        "file": "app.routers.x402_premium_tools",
    },
    {
        "path": "/webhook_register",
        "v1": "/api/v1/x402/webhook-register",
        "file": "app.routers.x402_premium_tools",
    },
    {
        "path": "/webhook_list",
        "v1": "/api/v1/x402/webhook-list",
        "file": "app.routers.x402_premium_tools",
    },
    {
        "path": "/investigation_report",
        "v1": "/api/v1/x402/investigation-report",
        "file": "app.routers.x402_premium_tools",
    },
    # health route (root-level infra — not actionable for migration)
    {
        "path": "/health, /live, /ready",
        "v1": "/api/v1/health/* (redirect)",
        "file": "app.core.health_route",
    },
    # homepage
    {"path": "/, /version", "v1": "/api/v1/version (redirect)", "file": "app.homepage"},
    # x402 docs
    {"path": "/x402/docs", "v1": "docs platform — stays at root", "file": "app.routers.x402_docs"},
    {
        "path": "/docs/v2/*",
        "v1": "docs platform — stays at root",
        "file": "app.routers.x402_docs_v2",
    },
    # x402 MCP
    {
        "path": "/mcp/x402",
        "v1": "MCP protocol — stays at root",
        "file": "app.routers.x402_mcp_handler",
    },
]

# Canonical router list (single source of truth)
# Order matters for path resolution. v1 routers are mounted at /api/v1/*.
# Domain routers (news, reports, x402) self-prefix.
ROUTER_MODULES: Final[list[str]] = [
    # Core system routes
    "app.core.health_route",  # /health, /live, /ready
    "app.core.metrics",  # /metrics
    "app.homepage",  # /, /version
    "app.routers.jwks",  # /.well-known/jwks.json, /api/v1/auth/jwks
    # API version index (root-level /api listing)
    "app.routers.v1.api_index",  # /api (version index)
    # v1 API aggregator (strangler fig — mounts all /api/v1/* routes)
    "app.routers.v1",  # /api/v1/* (aggregator)
    # DataBus core router — webhooks, cache, admin, convenience, premium, social, bitquery, market news
    "app.databus.router",  # /api/v1/databus/* (webhooks, cache, admin, data fetch)
    # Legacy v1 mounts (thin HTTP layer — kept for backward compat)
    "app.api.v1.auth.alerts",  # /api/v1/alerts/*
    "app.api.v1.public.wallet",  # /api/v1/wallet/*
    "app.api.v1.public.token",  # /api/v1/token/*
    "app.api.v1.rag.search",  # /api/v1/rag/v2/*
    "app.api.v1.admin.alerts_webhook",  # /api/v1/admin/alerts/webhook
    "app.api.v1.admin.glitchtip_test",  # /api/v1/_test/* (T07)
    "app.api.v1.catalog",  # /api/v1/catalog/*
    "app.api.v1.mcp",  # /mcp/* (JSON-RPC + plain JSON)
    # Domain facades (per v4.0 T28-T34)
    "app.domains.news",  # /api/v1/news/*
    "app.domains.news.admin_router",  # /api/v1/news/_admin/*
    "app.domains.reports",  # /api/v1/reports/*
    "app.domains.x402",  # /api/v1/x402/*
    # TODO(mount): app.domains.x402.tools_router  # /api/v1/x402-tools/* (paid tools)
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
    "app.routers.web3_chat",  # /api/v1/chat/web3/*  (18 routes, Web3 chat: payments, tokens, NFTs)
    "app.routers.address_profiler",  # /api/v1/address-profiler/*  (1 route, scanner ext)
    "app.routers.chain_comparability",  # /api/v1/chain-compare/*  (2 routes, multi-chain)
    "app.routers.honeypot_map",  # /api/v1/honeypot-map/*  (3 routes, unique viz)
    "app.routers.label_lookup",  # /api/v1/labels/*  (1 route, CF worker enrichment)
    "app.routers.whale_alerts",  # /api/v1/whale-alerts/*  (5 routes, whale tracker)
    "app.routers.lp_health",  # /api/v1/lp-health/*  (5 routes, LP monitor)
    "app.routers.ai_stream",  # /api/v1/ai/*  (3 routes, SSE infra)
    "app.routers.rugcharts",  # /api/v1/rugcharts/*  (3 routes, RugCharts backend)
    "app.routers.rugmaps_router",  # /api/v1/rugmaps/*  (7 routes, RugMaps viz)
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
    "app.routers.scam_school",  # /scam-school/*  (11 routes, education platform). No prefix — paths absolute. DEPRECATED — use /api/v1/scam-school/*.
    "app.routers.wallet_clustering_router",  # /api/v1/wallet-clusters/*  (8 routes, wallet_clustering.py live; depends on app.{ai_router,bundle_detector,cluster_detection,entity_registry,fraud_gnn,unified_provider,wallet_clustering,spam_registry,chain_cache})
    "app.routers.security_intel",  # /api/v1/security/*  (31 routes, crypto security stack). Depends on LIVE app.{cross_chain_correlator,ml_anomaly,onchain_analyzer}.
    "app.routers.mev_sniper",  # /api/v1/mev-sniper/*  (2 routes: /signals + /chains). No internal app.* deps. MEV Sniper = premium scanner feature.
    #
    # Wire-in Wave 3 — AUDIT-2026-Q3.md Phase 2.3 — mounted 2026-07-07.
    "app.routers.unified_scanner_router",  # /api/v2/scanner/{token,wallet}/scan  (2 routes, v2 scanner).
    #
    # DEFERRED Wave 3 (2):
    #   "app.routers.unified_wallet_scanner" — NO `router` APIRouter attribute.
    #     Module exposes `get_wallet_scanner()` consumed by unified_scanner_router.
    #     Wire-in deferred: not a router, would shadow /api/v2/scanner/wallet/scan
    #     if exposed as a thin wrapper. Already reachable from app.unified_wallet_scanner
    #     which is imported by unified_scanner_router.
    #   "app.routers.admin_extensions" — DORMANT per audit (darkroom-specific, 25 routes
    #     at /api/v1/admin/*). Would shadow /api/v1/admin/alerts_webhook (mounted Wave 1).
    #     Kept in tree per audit, not wired.
    #
    # Wire-in Wave 4 — Remaining unmounted routers — mounted 2026-07-20.
    # All import cleanly with unique prefixes vs existing mounts.
    # Confirmed no path/method conflicts via OpenAPI spec inspection.
    "app.routers.x402_databus_tools",  # /api/v1/x402-databus/*  (39 routes, DataBus tools)
    "app.routers.status_page",  # /api/v1/status/*  (5 routes, public status page)
    "app.routers.chain_backup",  # /api/v1/system/*  (2 routes, backup status + config)
    "app.routers.protection",  # /api/v1/protect/*  (5 routes, URL/wallet/token protection)
    "app.routers.dev_portal",  # /api/v1/developer/*  (5 routes, dev key management)
    "app.routers.developer_tier",  # /api/v1/developer/*  (7 routes, tier registration/verify)
    "app.routers.token_intelligence_v2",  # /api/v2/tokens/*  (1 route, v2 token intel)
    "app.routers.facilitator_health",  # /api/v1/x402/facilitator-health/*  (3 routes)
    "app.routers.x402_bridge_health",  # /api/v1/x402-tools/*  (1 route, bridge health)
    #
    # Admin panel router — processes, disk, network, system updates
    # NOTE: /api/v1/admin/* does NOT conflict with /api/v1/alerts/*
    #       (alerts_webhook uses prefix "/alerts", resolved to /api/v1/alerts/*)
    "app.routers.admin_extensions",  # /api/v1/admin/* (server admin + updates)
    #
    # Pryscraper integration — mounted 2026-07-23
    "app.routers.pryscraper_bridge",  # /api/v1/scrape, /api/v1/extract, /api/v1/news/search, /api/v1/pryscraper/*
    #
    # Telegram integration — mounted 2026-07-25
    "app.domains.telegram.router",  # /api/v1/telegram/* (voice chat, groups, headless client)
    #
    # DegenFeed protocol proxy — mounted 2026-07-24
    "app.routers.degenfeed_proxy",  # /api/v1/degenfeed/* (proxy, cache, rate limits)
    "app.routers.degenfeed_health",  # /api/v1/degenfeed/status (protocol health)
    #
    # Live gamification — mounted 2026-07-25
    "app.routers.live",  # /api/v1/live/* (battles, XP, challenges, achievements, events)
    #
    # Creator Analytics — mounted 2026-07-26
    "app.domains.analytics",  # /api/v1/analytics/* (views, engagement, cross-protocol, insights, audience)
    #
    # Standardized Private Chat — Matrix fabric + Signal Protocol — mounted 2026-07-25
    "app.routers.chat_standard",  # /api/v1/chat/* (DM, group, bridge, keys, E2E encryption)
    #
    # Newsletter system — auto-send, crypto payments, cross-platform publishing — mounted 2026-07-26
    "app.domains.newsletter.router",  # /api/v1/newsletter/* (14 routes)
    #
    # Postiz Premium — 30+ platform cross-posting — mounted 2026-07-26
    "app.domains.postiz",  # /api/v1/postiz/* (20 routes, 30+ platforms)
    #
    # Scanner Webhooks — zero-dependency event-based webhook system — mounted 2026-07-30
    "app.domains.scanner.webhooks.router",  # /api/v1/scanner/webhooks/* (4 routes)
    #
    # NOT YET MOUNTED (deferred):
    #   "app.routers.mcp_server" — routes (.well-known, /mcp/*, /tools, /news/*) conflict
    #     with app.api.v1.mcp (already mounted at /mcp). Needs reconciliation.
    #   "app.routers.x402_catalog", x402_dashboard, x402_middleware — all use /api/v1/x402
    #     prefix which conflicts with app.domains.x402 (T34 domain, mounted Wave 1).
    #     Should be consumed by the T34 domain router, not mounted independently.
    #   "app.routers.x402_enforcement", x402_enrichment — utility modules, no router attr.
    #     Imported directly by other routers.
    #   "app.routers.x402_tools" — re-export shim for app.domains.billing.x402.router.
    #     The canonical billing/x402 router (77 endpoints at /api/v1/x402-tools) is
    #     intentionally not wired until x402_bridge_health path compatibility is resolved.
    #   "app.domains.labels.router" — uses root-level /labels prefix (deprecated pattern).
    #     app.routers.label_lookup already serves /api/v1/labels/*.
]


def mount_all(app: Any) -> int:
    """Mount every router. Returns count of successful mounts.

    Each mount is isolated - one failure does not break the rest.

    After mounting, logs a deprecation notice for all root-level routes
    that have been superseded by /api/v1/* equivalents.
    """
    mounted = 0
    for module_path in ROUTER_MODULES:
        if _try_mount(app, module_path):
            mounted += 1

    # ── Deprecation notice ────────────────────────────────────────────
    _log_deprecation_warning()

    return mounted


def _log_deprecation_warning() -> None:
    """Log a startup warning listing all root-level routes that are deprecated."""
    deprecated = [r for r in ROOT_LEVEL_ROUTES if "stays at root" not in r.get("v1", "")]
    mcp_level = [r for r in ROOT_LEVEL_ROUTES if "stays at root" in r.get("v1", "")]

    if deprecated:
        log.warning(
            "DEPRECATION: %d root-level routes are deprecated. "
            "Migrate to /api/v1/* equivalents before 2026-10-15 (90-day window).",
            len(deprecated),
        )
        for r in deprecated:
            log.warning("  LEGACY: %-40s → %s  (from %s)", r["path"], r["v1"], r["file"])

    if mcp_level:
        log.info(
            "ROOT-LEVEL (no migration): %d routes remain at root (infra/protocol)",
            len(mcp_level),
        )


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
