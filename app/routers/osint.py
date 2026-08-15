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
    * ``run_osint(target, target_type="auto", *, timeout=120) -> dict``
      — per-module ``results`` plus a flat ``findings`` list and ``errors``
    * ``run_aggregate(target, target_type="auto", *, timeout=120) -> dict``
      — same shape; all applicable modules run in parallel
    * ``TARGET_TYPES: list[str]`` (capability listing; fallback below)
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.routers.osint_schemas import AggregateReportModel, AggregateRequest

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


# ── Redis cache (thin layer — lookups are expensive) ─────────────────────────
#
# Sherlock / maigret / theHarvester / Amass / SpiderFoot hammer external
# services, so identical lookups within the TTL window are served from Redis
# instead of re-running. Any Redis failure degrades gracefully: the cache is
# skipped, the lookup runs, and a warning is logged — caching never blocks a
# request.
_CACHE_TTL_ENV: str = "OSINT_CACHE_TTL"
_DEFAULT_CACHE_TTL: int = 3600
# Face (image) lookups are path-dependent (input image → embeddings), so the
# same key almost never repeats and the payloads are large — excluded entirely.
_CACHE_EXCLUDED_TARGET_TYPES: frozenset[str] = frozenset({"face"})


def _cache_ttl() -> int:
    """TTL for OSINT cache entries: settings > env > 3600s default."""
    try:
        from app.config import settings as _settings

        return int(_settings.osint_cache_ttl)
    except Exception:  # noqa: S110 — settings may not be bootstrapped yet
        pass
    try:
        return int(os.getenv(_CACHE_TTL_ENV, str(_DEFAULT_CACHE_TTL)))
    except ValueError:
        return _DEFAULT_CACHE_TTL


def _cache_key(target_type: str, target: str) -> str:
    """Normalized Redis key: ``osint:{target_type}:{normalized_target}``.

    Targets are stripped + lowercased so ``Foo@Example.COM`` and
    ``foo@example.com`` share one entry (OSINT tooling is case-insensitive for
    usernames/emails/domains; wallet reports carry the canonical checksummed
    address regardless of input casing).
    """
    normalized = target.strip().lower()
    return f"osint:{target_type}:{normalized}"


def _cacheable(target_type: str) -> bool:
    """Face (image) lookups are path-dependent and large — never cached."""
    return target_type not in _CACHE_EXCLUDED_TARGET_TYPES


def _cache_get(target_type: str, target: str) -> dict[str, Any] | None:
    """Return a cached OSINT report dict, or None on miss / Redis-down."""
    if not _cacheable(target_type):
        return None
    try:
        from app.core.redis import get_redis

        client = get_redis()
        if client is None:
            return None
        raw = client.get(_cache_key(target_type, target))
        if raw is None:
            return None
        cached = json.loads(raw)
        return cast("dict[str, Any]", cached) if isinstance(cached, dict) else None
    except Exception as exc:  # Redis down / corrupt payload — never crash the lookup
        logger.warning("osint_cache_get_failed target_type=%s err=%s", target_type, exc)
        return None


def _cache_set(target_type: str, target: str, payload: dict[str, Any]) -> None:
    """Store a report dict under the normalized key with the configured TTL."""
    if not _cacheable(target_type):
        return
    try:
        from app.core.redis import get_redis

        client = get_redis()
        if client is None:
            return
        client.set(_cache_key(target_type, target), json.dumps(payload, default=str), ex=_cache_ttl())
    except Exception as exc:  # Redis down — a failed cache write is non-fatal
        logger.warning("osint_cache_set_failed target_type=%s err=%s", target_type, exc)


# ── Endpoints ────────────────────────────────────────────────────────────────
#
# Rate limiting (applied to BOTH GET + POST below):
#   ``get_rate_limit_dependency("osint", rate=0.2, burst=3)`` → ~1 lookup/5s
#   sustained per client, shared bucket for the whole OSINT category
#   (``dep:osint:{client_id}``). Deliberately NOT keyed per target_type: a
#   per-type bucket would let one client run 6 parallel lookups (one per type),
#   multiplying load on the external services (sherlock/maigret/theHarvester/
#   Amass/SpiderFoot). The shared per-client bucket is stricter and is the
#   correct shape for abuse protection — the Redis cache handles the repeat-hit
#   side of the equation.


@osint_router.get("/types")
def get_osint_types() -> dict[str, list[str]]:
    """List the OSINT target types the engine supports."""
    return {"target_types": _target_types()}


@osint_router.get("/{target_type}/{target}", response_model=AggregateReportModel)
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
) -> AggregateReportModel:
    """Run OSINT on a single target of the given type (e.g. domain, wallet, email)."""
    valid = _target_types()
    if target_type not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown target type '{target_type}'. Valid: {', '.join(valid)}",
        )
    cached = _cache_get(target_type, target)
    if cached is not None:
        return AggregateReportModel.model_validate({**cached, "cached": True})
    try:
        report = _call_engine("run_osint", target, target_type, timeout=timeout)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_osint_errors(exc) from exc
    payload = _to_report_dict(report)
    _cache_set(target_type, target, payload)
    return AggregateReportModel.model_validate(payload)


@osint_router.post("/aggregate", response_model=AggregateReportModel)
def aggregate_osint(
    request: AggregateRequest,
    timeout: int = Query(
        60,
        ge=5,
        le=600,
        description="Per-module timeout in seconds — OSINT lookups are slow.",
    ),
    _rl: None = Depends(get_rate_limit_dependency("osint", rate=0.2, burst=3)),
) -> AggregateReportModel:
    """Run OSINT across all modules for a target — combined report."""
    kwargs: dict[str, Any] = {}
    if request.target_type:
        kwargs["target_type"] = request.target_type
    # Aggregates run *every* applicable module, so they benefit most from the
    # cache. ``target_type`` is optional here — ``"auto"`` keeps a stable key
    # for auto-detected lookups (the engine's detection is deterministic).
    cache_type = request.target_type or "auto"
    cached = _cache_get(cache_type, request.target)
    if cached is not None:
        return AggregateReportModel.model_validate({**cached, "cached": True})
    try:
        report = _call_engine("run_aggregate", request.target, timeout=timeout, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_osint_errors(exc) from exc
    payload = _to_report_dict(report)
    _cache_set(cache_type, request.target, payload)
    return AggregateReportModel.model_validate(payload)
