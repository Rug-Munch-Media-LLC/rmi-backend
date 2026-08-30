"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.rug_pull_predictor`."""

from __future__ import annotations

import sys

from app.detectors import rug_pull_predictor as _impl

sys.modules[__name__] = _impl
