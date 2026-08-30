"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.bundle_detector`."""

from __future__ import annotations

import sys

from app.detectors import bundle_detector as _impl

sys.modules[__name__] = _impl
