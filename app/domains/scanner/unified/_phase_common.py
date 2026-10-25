"""Phase-helper primitives — error recording and exception-safe awaits.

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25): every phase
runner records module failures the same way, so the recording contract lives
here instead of being re-implemented per phase.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


def _record_module_error(r: ScanResult, module: str, chain: str, reason: str) -> None:
    """Carry a module failure into ``ScanResult.errors`` + log it visibly.

    Errors are NEVER silent: every unavailable/failed module gets a WARNING
    log with structured fields and an entry in the response ``errors`` list
    so the API consumer sees WHY own-data was missing (2026-09-02 hardening).
    """
    reason = (reason or "unknown")[:200]
    logger.warning("scanner_module_failed", module=module, chain=chain, error=reason)
    with contextlib.suppress(AttributeError):  # defensive for legacy results
        r.errors.append({"module": module, "chain": chain, "error": reason})


def _record_module_unavailable(r: ScanResult, module: str, chain: str, reason: str) -> None:
    """Log + record a module that degraded to ``unavailable`` (INFO level)."""
    reason = (reason or "no data")[:200]
    logger.info("scanner_module_unavailable", module=module, chain=chain, reason=reason)
    with contextlib.suppress(AttributeError):
        r.errors.append({"module": module, "chain": chain, "error": f"unavailable: {reason}"})


async def _safe(coro: Any) -> Any:
    """Await a coroutine, converting any exception into a returned value.

    Replacement for ``asyncio.gather(..., return_exceptions=True)`` in the
    sequential wallet-analysis path — same contract, no concurrency.
    """
    try:
        return await coro
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # mirrors gather(return_exceptions=True)
        return exc
