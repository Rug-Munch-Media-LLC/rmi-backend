"""Catalog domain - the unified read/write API for RMI.

T27 of the v4.0 guide. Every store read/write goes through CatalogService.
Domain facades call the catalog; they never touch stores directly.

Architecture (per v4.0 §T27):
  app/catalog/models.py      Pydantic v2 entity schemas
  app/catalog/service.py     CatalogService - fan-out reads, fan-in writes
  app/catalog/reputation.py  Deployer reputation scoring (T31)
  app/catalog/rag_bridge.py  Bridge to existing app/rag/ engine
  app/catalog/llm_router.py  LiteLLM proxy for AI analysis

Cross-store ref shape (per v4.0):
  "chain:address"            Wallet, Token, Contract IDs
  UUID                       Entity, Alert, NewsItem, RAGFinding, Report IDs
  Qdrant point_id            RAGFinding.vector_id, rag_embedding_id on Token

Public API (re-exported):
  CatalogService, get_catalog
  Entity, Wallet, Deployer, Token, Alert, NewsItem, RAGFinding, ScanReport
  DeployerReputation, RECIPES
"""

from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth

async def _health_check() -> DomainHealth:
    """Catalog health: which stores are reachable + how many entities."""
    try:
        from app.catalog.service import get_catalog

        cat = get_catalog()
        reach = await cat.probe_stores()
        healthy = any(reach.values())
        return DomainHealth(
            name="catalog",
            healthy=healthy,
            details={
                "stores_reachable": dict(reach.items()),
                "primary": "redis+rag" if healthy else "none",
            },
        )
    except Exception as e:
        return DomainHealth(name="catalog", healthy=False, error=str(e))

health_mod.register_health_check("catalog", _health_check)

# Public API
from app.catalog.models import (  # noqa: E402
    CHAIN_REGISTRY,
    COLLECTIONS,
    Alert,
    Chain,
    Deployer,
    Entity,
    EntityLabel,
    NewsItem,
    RAGFinding,
    RiskTier,
    ScanReport,
    Token,
    Wallet,
)
from app.catalog.service import CatalogService, get_catalog  # noqa: E402

__all__ = [
    "CHAIN_REGISTRY",
    "COLLECTIONS",
    "Alert",
    "CatalogService",
    "Chain",
    "Deployer",
    "Entity",
    "EntityLabel",
    "NewsItem",
    "RAGFinding",
    "RiskTier",
    "ScanReport",
    "Token",
    "Wallet",
    "get_catalog",
]
