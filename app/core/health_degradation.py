"""Degradation-registry bridge for the health endpoints (Win #14).

Split out of ``health_stores.py`` (debt wave, 2026-10-25) so the store probes
module stays under the file ceiling and this side-effect bridge is reviewed on
its own.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("health_degradation")


def _sync_degradation_from_health(stores: dict[str, dict[str, Any]]) -> None:
    """Win #14: Sync health check results to the degradation registry."""
    try:
        from app.core.degradation import get_registry

        registry = get_registry()
        for name in ("clickhouse", "neo4j", "minio", "redis"):
            if name in stores:
                if stores[name].get("healthy"):
                    registry.mark_healthy(name)
                else:
                    registry.mark_down(name)
    except (AttributeError, ImportError) as e:
        logger.debug(f"_sync_degradation_from_health failed: {e}")


def _get_degradation_section() -> dict[str, Any]:
    """Win #14: Get degradation section for health endpoint."""
    try:
        from app.core.degradation import get_degradation_health

        return {"degradation": get_degradation_health()}
    except (AttributeError, ImportError) as exc:
        logger.debug("_get_degradation_section_failed", error=str(exc))
        return {}
