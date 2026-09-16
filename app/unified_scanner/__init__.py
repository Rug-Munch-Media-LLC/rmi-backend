"""DEPRECATED shim (2026-09-16) — wallet scanner moved into the canonical package.

Real implementation: ``app.domains.scanner.engines.wallet_scan``.
Path-preserving (re-exports + submodule aliases) so existing imports keep
working (protection.py, streaming_rag.py, tests) — including deep imports
like ``from app.unified_scanner.helius import ...`` and patch semantics
(the aliases are the SAME module objects). Remove once consumers migrate.
"""

from __future__ import annotations

import importlib
import sys

from app.domains.scanner.engines.wallet_scan import *  # noqa: F403
from app.domains.scanner.engines.wallet_scan import __all__ as __all__

_SUBMODULES = ("cache", "helius", "models", "providers", "scanner", "trending")
for _name in _SUBMODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"app.domains.scanner.engines.wallet_scan.{_name}")
