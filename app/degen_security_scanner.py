"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.degen_security_scanner`."""

from __future__ import annotations

import sys

from app.detectors import degen_security_scanner as _impl

sys.modules[__name__] = _impl
