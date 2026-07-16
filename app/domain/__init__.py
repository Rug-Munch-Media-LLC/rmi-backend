"""domain - DEPRECATED shim. Use app.domains.

Phase 4.7 of AUDIT-2026-Q3.md renamed app/domain/ to app/domains/.
This shim re-exports for legacy callers.
"""

import importlib as _importlib
import sys as _sys

import app.domains as _new

_sys.modules["app.domain"] = _new

_sys.modules["app.domain.alerts"] = _importlib.import_module("app.domains.alerts")
_sys.modules["app.domain.auth"] = _importlib.import_module("app.domains.auth")
_sys.modules["app.domain.billing"] = _importlib.import_module("app.domains.billing")
_sys.modules["app.domain.databus"] = _importlib.import_module("app.domains.databus")
_sys.modules["app.domain.labels"] = _importlib.import_module("app.domains.labels")
_sys.modules["app.domain.news"] = _importlib.import_module("app.domains.news")
_sys.modules["app.domain.reports"] = _importlib.import_module("app.domains.reports")
_sys.modules["app.domain.scanner"] = _importlib.import_module("app.domains.scanner")
_sys.modules["app.domain.telegram"] = _importlib.import_module("app.domains.telegram")
_sys.modules["app.domain.threat"] = _importlib.import_module("app.domains.threat")
_sys.modules["app.domain.token"] = _importlib.import_module("app.domains.token")
_sys.modules["app.domain.tokens"] = _importlib.import_module("app.domains.tokens")
_sys.modules["app.domain.wallet"] = _importlib.import_module("app.domains.wallet")
_sys.modules["app.domain.x402"] = _importlib.import_module("app.domains.x402")
