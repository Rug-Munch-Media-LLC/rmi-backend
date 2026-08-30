"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.oracle_manipulation_detector`."""

from __future__ import annotations

import sys

from app.detectors import oracle_manipulation_detector as _impl

sys.modules[__name__] = _impl
