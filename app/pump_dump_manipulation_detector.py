"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.pump_dump_manipulation_detector`."""

from __future__ import annotations

import sys

from app.detectors import pump_dump_manipulation_detector as _impl

sys.modules[__name__] = _impl
