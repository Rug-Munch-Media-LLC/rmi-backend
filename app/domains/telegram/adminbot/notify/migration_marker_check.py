"""Whether migration completion was already announced."""

from __future__ import annotations

import os

from .migration_marker import MARKER


def already_announced() -> bool:
    """True when the completion alert has already been sent.

    Synchronous on purpose: a single stat, so the async caller does no blocking
    filesystem work on the event loop.
    """
    return os.path.exists(MARKER)
