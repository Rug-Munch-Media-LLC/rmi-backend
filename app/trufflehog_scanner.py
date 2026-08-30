"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.trufflehog_scanner`."""

from __future__ import annotations

import sys

from app.detectors import trufflehog_scanner as _impl

sys.modules[__name__] = _impl
