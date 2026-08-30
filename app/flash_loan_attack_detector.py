"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.flash_loan_attack_detector`."""

from __future__ import annotations

import sys

from app.detectors import flash_loan_attack_detector as _impl

sys.modules[__name__] = _impl
