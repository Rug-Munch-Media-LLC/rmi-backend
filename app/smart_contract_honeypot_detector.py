"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.smart_contract_honeypot_detector`."""

from __future__ import annotations

import sys

from app.detectors import smart_contract_honeypot_detector as _impl

sys.modules[__name__] = _impl
