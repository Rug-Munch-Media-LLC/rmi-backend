"""Backward-compatibility shim — implementation moved to :mod:`app.detectors.rug_imminence_predictor`."""

from __future__ import annotations

import sys

from app.detectors import rug_imminence_predictor as _impl

sys.modules[__name__] = _impl
