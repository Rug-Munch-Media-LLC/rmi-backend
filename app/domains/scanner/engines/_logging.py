"""Scanner-local logging — structlog direct, zero backend dependency.

Extraction-prep (repo split phase 1): engines previously imported
``app.core.logging``; this shim provides the identical ``get_logger``
(structlog) without reaching outside the future standalone repo.
structlog's contextvars binding (request_id / trace_id) is global, so
correlation IDs flow exactly as before.
"""

from __future__ import annotations

from typing import cast

import structlog


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger — same contract as app.core.logging."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
