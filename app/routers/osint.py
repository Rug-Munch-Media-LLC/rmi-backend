"""OSINT REST API router — exposes the OSINT section to the website + bot.

Routes:
    GET  /api/osint/{target_type}/{target}  → run_osint(target, target_type)
    POST /api/osint/aggregate               → run_aggregate(target, ...)
    GET  /api/osint/types                   → {"target_types": [...]}

The engine module (``app.osint.engine``) is created by a parallel agent and is
loaded lazily via a guarded import. If it is missing, these endpoints return
503 "OSINT engine not available" instead of breaking app boot.

OSINT modules are slow (WHOIS / DNS / breach / social enumeration), so every
lookup endpoint exposes a ``timeout`` query param (default 60s, matching the
module contract in :mod:`app.osint`).

Composition contract (see :mod:`app.osint`):
    * ``run_osint(target, target_type, *, timeout=60) -> OsintReport``
    * ``run_aggregate(target, *, target_type=None, timeout=60) -> OsintReport``
    * ``TARGET_TYPES: list[str]`` (capability listing; fallback below)
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency

logger = get_logger(__name__)

# Prefix is applied at include time (app/main.py) — mirrors router convention
# where mount.py passes no prefix because routers own theirs. We own the prefix
# in the single registration line to keep the change isolated.
osint_router = APIRouter(tags=["osint"])

# Capability listing fallback (contract in app/osint/__init__.py). The engine
# may override this with its own TARGET_TYPES when available.
FALLBACK_TARGET_TYPES: list[str] = ["username", "email", "domain", "wallet", "phone", "spiderfoot"]

# ── Guarded engine import (parallel agent creates app/osint/engine.py) ──────
_engine: Any = None
try:
    _engine = importlib.import_module("app.osint.engine")
    logger.info("osint_engine_loaded module=%s", getattr(_engine, "__name__", "app.osint.engine"))
except ImportError as exc:
    # Engine not created yet — endpoints 503 until it lands.
    logger.debug("osint_engine_unavailable err=%s", exc)


class OsintAggregateRequest(BaseModel):
    """POST /api/osint/aggregate body."""

    target: str
    target_type: str | None = None


def _target_types() -> list[str]:
    """Return the engine's supported target types (or the contract fallback)."""
    if _engine is not None:
        types = getattr(_engine, "TARGET_TYPES", None)
        if types:
            return list(types)
    return list(FALLBACK_TARGET_TYPES)


def _engine_or_503() -> Any:
    """Return the loaded engine module or raise 503 if it is unavailable."""
    if _engine is None:
        raise HTTPException(status_code=503, detail="OSINT engine not available")
    return _engine


def _call_engine(fn_name: str, *args: Any, timeout: int, **kwargs: Any) -> Any:
    """Call ``fn_name`` on the engine with a timeout, tolerating signature drift.

    The engine is under parallel construction; if it does not yet accept the
    ``timeout`` kwarg, retry positionally rather than failing the request.
    """
    fn = getattr(_engine_or_503(), fn_name, None)
    if fn is None:
        raise HTTPException(status_code=503, detail=f"OSINT engine function '{fn_name}' not available")
    try:
        return fn(*args, timeout=timeout, **kwargs)
    except TypeError as exc:
        if "timeout" not in str(exc):
            raise
        return fn(*args, **kwargs)


def _map_osint_errors(exc: Exception) -> HTTPException:
    """Map engine exceptions to HTTP errors with clear messages."""
    from app.osint import OsintError, OsintNotAvailable

    if isinstance(exc, OsintNotAvailable):
        return HTTPException(status_code=503, detail=f"OSINT engine not available: {exc}")
    if isinstance(exc, OsintError):
        return HTTPException(status_code=500, detail=f"OSINT lookup failed: {exc}")
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=f"OSINT lookup failed: {exc}")


def _to_report_dict(report: Any) -> dict[str, Any]:
    """Normalize an OsintReport (or dict) into a JSON-safe dict.

    Uses ``default=str`` safe serialization so findings with non-JSON-native
    values (datetime, enums, bytes, ...) never break the response.
    """
    if hasattr(report, "to_dict"):
        report = report.to_dict()
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="OSINT engine returned an unexpected result")
    return json.loads(json.dumps(report, default=str))


# ── Endpoints ────────────────────────────────────────────────────────────────


@osint_router.get("/types")
def get_osint_types() -> dict[str, list[str]]:
    """List the OSINT target types the engine supports."""
    return {"target_types": _target_types()}


@osint_router.get("/{target_type}/{target}")
def get_osint_report(
    target_type: str,
    target: str,
    timeout: int = Query(
        60,
        ge=5,
        le=600,
        description="Per-module timeout in seconds — OSINT lookups are slow.",
    ),
    _rl: None = Depends(get_rate_limit_dependency("osint", rate=0.2, burst=3)),
) -> dict[str, Any]:
    """Run OSINT on a single target of the given type (e.g. domain, wallet, email)."""
    valid = _target_types()
    if target_type not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown target type '{target_type}'. Valid: {', '.join(valid)}",
        )
    try:
        report = _call_engine("run_osint", target, target_type, timeout=timeout)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_osint_errors(exc) from exc
    return _to_report_dict(report)


@osint_router.post("/aggregate")
def aggregate_osint(
    request: OsintAggregateRequest,
    timeout: int = Query(
        60,
        ge=5,
        le=600,
        description="Per-module timeout in seconds — OSINT lookups are slow.",
    ),
    _rl: None = Depends(get_rate_limit_dependency("osint", rate=0.2, burst=3)),
) -> dict[str, Any]:
    """Run OSINT across all modules for a target — combined report."""
    kwargs: dict[str, Any] = {}
    if request.target_type:
        kwargs["target_type"] = request.target_type
    try:
        report = _call_engine("run_aggregate", request.target, timeout=timeout, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_osint_errors(exc) from exc
    return _to_report_dict(report)
