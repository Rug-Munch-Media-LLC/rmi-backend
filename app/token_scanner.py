"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.token_scanner`."""

from __future__ import annotations

import sys

from app.detectors import token_scanner as _impl

sys.modules[__name__] = _impl

# Explicit re-export for type checkers (mypy cannot follow the sys.modules swap).
from app.detectors.token_scanner import scan_token as scan_token  # noqa: E402
