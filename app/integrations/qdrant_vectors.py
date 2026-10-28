"""Qdrant named-vector detection (flat config vs named ``dense`` vector).

``news_v2_8b`` and ``codebase_reference_v2_8b`` publish a named vector: a
``{"vector": [...]}`` body 400s against them, the body must be
``{"vector": {"name": "dense", "vector": [...]}}``. ``defi_exploits_8b`` and
``regulation_enforcement_8b`` are flat. The collection config is static, so the
verdict is memoised per process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    import httpx

log = get_logger(__name__)

_NAMED: dict[str, str | None] = {}


async def vector_name(client: httpx.AsyncClient, base: str, collection: str) -> str | None:
    """Return the named vector to query, or ``None`` when the collection is flat."""
    if collection in _NAMED:
        return _NAMED[collection]
    name: str | None = None
    try:
        resp = await client.get(f"{base}/collections/{collection}")
        cfg = (
            (((resp.json().get("result") or {}).get("config") or {}).get("params") or {}).get("vectors")
        )
        if isinstance(cfg, dict) and cfg and "size" not in cfg and all(isinstance(v, dict) for v in cfg.values()):
            name = "dense" if "dense" in cfg else next(iter(cfg))
            log.info("qdrant_named_vector collection=%s name=%s", collection, name)
    except Exception as exc:  # unknown config -> keep the flat body
        log.warning("qdrant_config_failed collection=%s err=%s", collection, type(exc).__name__)
    _NAMED[collection] = name
    return name
