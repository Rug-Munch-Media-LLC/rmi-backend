"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.wallet_drain_scanner`."""

from __future__ import annotations

import sys

from app.detectors import wallet_drain_scanner as _impl

sys.modules[__name__] = _impl
