"""domain - DEPRECATED shim. Use app.domains.

Phase 4.7 of AUDIT-2026-Q3.md renamed app/domain/ to app/domains/.
This shim re-exports for legacy callers.
"""

from __future__ import annotations

import importlib as _importlib
import sys as _sys

import app.domains as _new

_sys.modules["app.domain"] = _new

# Map all domain submodules
_domain_modules = [
    "admin",
    "airdrop",
    "alerts",
    "analysis",
    "auth",
    "billing",
    "bulletin",
    "databus",
    "detectors",
    "labels",
    "markets",
    "mcp",
    "news",
    "portfolio",
    "prediction",
    "referral",
    "reports",
    "scanner",
    "scanners",
    "telegram",
    "threat",
    "token",
    "tokens",
    "wallet",
    "x402",
]

for _mod in _domain_modules:
    _sys.modules[f"app.domain.{_mod}"] = _importlib.import_module(f"app.domains.{_mod}")
