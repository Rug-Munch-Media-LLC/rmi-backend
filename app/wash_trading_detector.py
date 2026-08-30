"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.wash_trading_detector`."""

from __future__ import annotations

import sys

from app.detectors import wash_trading_detector as _impl

sys.modules[__name__] = _impl
