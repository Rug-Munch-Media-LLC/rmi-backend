"""
RMI DataBus — The Single Source of Truth for ALL Data
=====================================================

Every API call, MCP tool, x402 tool, scanner, and frontend hook routes through here.
No raw HTTP calls to external APIs anywhere else. Period.

Architecture (request flow):
  Request → SecurityGate → CreditGate → CacheLayer → ProviderChain → Result
        ↓                      ↓             ↓              ↓            ↓
   admin check         free-first logic   L1→L2→L3      local-first!   RAG index
   vault keys          auto-rotate      Redis+R2      OUR data 1st    WS broadcast

What it REPLACES (do NOT use these anymore):
  - app/cache_manager.py (RMICache)          → databus.cache
  - app/caching_shield/unified_layer.py       → databus.fetch()
  - app/caching_shield/data_fallback.py       → databus.fetch()
  - app/caching_shield/api_registry.py        → databus.key_pool
  - app/caching_shield/rate_limiter.py         → databus.rate_limiter
  - app/arkham_connector.py                    → databus.providers.arkham
  - app/coingecko_connector.py                → databus.providers.coingecko
  - Direct .env reads for API keys            → databus.vault (encrypted in-memory)

OWN DATA FIRST:
  Our crown jewels — Wallet Memory Bank, RAG (17K docs), SENTINEL scanner,
  Consensus RPC, Funding Tracer, ClickHouse, Price Consensus, News Network,
  Bundle Detection, Label Import (169K+) — these are FAST, FREE, and OURS.
  They go FIRST in every fallback chain. External APIs only augment or fill gaps.

Usage:
    from app.databus import databus

    # Simple fetch — auto-selects best provider chain
    result = await databus.fetch("token_price", mint="So11111111111111111111111111111111111111111")

    # Explicit chain override
    result = await databus.fetch("wallet_labels", address="7EcD...",
                                  chain="local_first")

    # Admin-only data (requires admin key in request)
    result = await databus.fetch("arkham_entity", address="0x...",
                                  admin_key="...")

    # Check system health
    health = await databus.health()

    # Get capacity report (credits, rate limits, recommendations)
    report = databus.capacity_report()
"""

from app.databus.access_control import AccessController, ConsumerType, access_controller
from app.databus.core import DataBus, databus
from app.databus.key_affinity import KeyAffinitySelector, key_affinity
from app.databus.response_schema import SchemaValidator, schema_validator
from app.databus.social import SocialDataAggregator, XTwitterProvider

__all__ = [
    "AccessController",
    "ConsumerType",
    "DataBus",
    "KeyAffinitySelector",
    "SchemaValidator",
    "SocialDataAggregator",
    "XTwitterProvider",
    "access_controller",
    "databus",
    "key_affinity",
    "schema_validator",
]
