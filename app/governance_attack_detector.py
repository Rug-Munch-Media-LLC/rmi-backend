"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.governance_attack_detector`."""

from __future__ import annotations

import sys

from app.detectors import governance_attack_detector as _impl

sys.modules[__name__] = _impl
