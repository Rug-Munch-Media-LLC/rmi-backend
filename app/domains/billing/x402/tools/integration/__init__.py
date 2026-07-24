"""x402 integration tools package.

Re-exports from focused sub-modules for backward compatibility.
Previously a single 1690-line god file (integration.py), now split into:
  - core.py       — router, framework endpoints, MCP proxy, bundles, catch-all dispatchers
  - payments.py   — payment processing & super-tool endpoints
  - webhooks.py   — webhook handling (placeholder)
  - models.py     — data models & constants
  - utils.py      — utility functions
"""

from __future__ import annotations

from app.domains.billing.x402.tools.integration import core, models, payments, utils, webhooks
from app.domains.billing.x402.tools.integration.core import sub_router

__all__ = [
    "core",
    "models",
    "payments",
    "sub_router",
    "utils",
    "webhooks",
]
