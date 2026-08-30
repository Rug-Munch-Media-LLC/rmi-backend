"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.token_scanner`."""

from __future__ import annotations

import sys

from app.detectors import token_scanner as _impl

sys.modules[__name__] = _impl
