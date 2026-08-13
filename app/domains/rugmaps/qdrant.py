"""RugMaps Qdrant client — thin async wrapper over the official client.

Phase 0 of the RugMaps migration: manages the ``rgm_*`` collections
(wallet embeddings, clusters, cluster patterns, entity labels) on the
Qdrant instance shared with the RAG stack.

Design notes
------------
- Uses ``AsyncQdrantClient`` (the async variant of the official
  ``QdrantClient``) so callers in the FastAPI event loop never block
  on network I/O.
- Every collection uses a **named dense vector** (``{"dense": ...}``)
  matching the convention established by ``app.qdrant_ann`` — bare
  vectors are rejected by Qdrant against named-vector collections.
- Collection configs are immutable in Qdrant, so ``create_collections``
  is idempotent: it never touches an existing collection.
- HNSW tuned for wallet-graph workloads: ``m=16``, ``ef_construct=200``
  (same family as the RAG ``wallet_embeddings`` collection).

No secrets, no RPC URLs. Qdrant URL comes from env (QDRANT_URL) with the
same Docker-compose default as the rest of the backend.
"""

from __future__ import annotations

from typing import Any, cast

from qdrant_client import AsyncQdrantClient, models

from app.config import resolve_service_url
from app.core.logging import get_logger

logger = get_logger(__name__)

# Resolved via app.config (env var wins; Docker name inside the network;
# host-mapped localhost on host-side systemd runs).
QDRANT_URL = resolve_service_url("QDRANT_URL", docker="http://rmi-qdrant:6333", local="http://localhost:6333").rstrip(
    "/"
)

# RugMaps Phase 0 collections: name -> dense vector dimension.
COLLECTIONS: dict[str, int] = {
    "rgm_wallets": 1024,  # wallet embedding vectors (identity)
    "rgm_clusters": 1024,  # wallet cluster centroids/embeddings
    "rgm_cluster_patterns": 1024,  # behavioral patterns per cluster
    "rgm_entity_labels": 1024,  # entity label embeddings
}

DENSE_VECTOR_NAME = "dense"


class RugMapsQdrant:
    """Thin async wrapper around ``AsyncQdrantClient`` for ``rgm_*`` collections.

    Example
    -------
    >>> qdrant = RugMapsQdrant()
    >>> await qdrant.create_collections()
    >>> await qdrant.health()
    >>> await qdrant.close()
    """

    def __init__(self, url: str | None = None) -> None:
        self.url = (url or QDRANT_URL).rstrip("/")
        self.client = AsyncQdrantClient(url=self.url)

    async def create_collections(self) -> dict[str, bool]:
        """Create every ``rgm_*`` collection that does not yet exist.

        Returns ``{collection_name: created}`` where ``created`` is True
        when the collection was newly created (False when it already
        existed or creation failed).
        """
        results: dict[str, bool] = {}
        for name, dim in COLLECTIONS.items():
            try:
                exists = await self.client.collection_exists(name)
            except Exception as e:
                logger.warning("rugmaps_qdrant_exists_error collection=%s err=%s", name, e)
                results[name] = False
                continue
            if exists:
                results[name] = False
                continue
            try:
                await self.client.create_collection(
                    collection_name=name,
                    vectors_config={
                        DENSE_VECTOR_NAME: models.VectorParams(
                            size=dim,
                            distance=models.Distance.COSINE,
                        )
                    },
                    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
                )
                logger.info("rugmaps_qdrant_created collection=%s dim=%d", name, dim)
                results[name] = True
            except Exception as e:
                logger.warning("rugmaps_qdrant_create_error collection=%s err=%s", name, e)
                results[name] = False
        return results

    async def collection_info(self, name: str) -> dict[str, Any]:
        """Return Qdrant collection info dict (or ``{}`` on error)."""
        try:
            return cast("dict[str, Any]", await self.client.get_collection(name))
        except Exception as e:
            logger.warning("rugmaps_qdrant_info_error collection=%s err=%s", name, e)
            return {}

    async def audit(self) -> list[dict[str, Any]]:
        """Audit the ``rgm_*`` collections: existence, dims, distance, points.

        Used by ``scripts/ops/rugmaps_qdrant.py --audit``. Each row carries
        ``exists`` (collection present) and ``error`` (Qdrant unreachable /
        info fetch failed) so health checks can distinguish "missing
        collection" from "Qdrant down".
        """
        rows: list[dict[str, Any]] = []
        for name, expected_dim in COLLECTIONS.items():
            try:
                info = await self.client.get_collection(name)
            except Exception as e:
                logger.warning("rugmaps_qdrant_info_error collection=%s err=%s", name, e)
                rows.append({"collection": name, "exists": False, "error": str(e)})
                continue
            if info is None:
                rows.append({"collection": name, "exists": False, "error": None})
                continue
            # Qdrant returns a typed CollectionInfo model (pydantic), not a dict.
            try:
                config = info.config
                vectors = config.params.vectors
                dense = vectors.get(DENSE_VECTOR_NAME) if isinstance(vectors, dict) else None
                dim = dense.size if dense is not None else None
                distance = str(dense.distance) if dense is not None else "?"
            except Exception as e:  # pragma: no cover - defensive against API shape drift
                logger.warning("rugmaps_qdrant_info_parse_error collection=%s err=%s", name, e)
                rows.append({"collection": name, "exists": True, "error": str(e)})
                continue
            rows.append(
                {
                    "collection": name,
                    "exists": True,
                    "points_count": info.points_count or 0,
                    "dim": dim if dim is not None else "?",
                    "distance": distance,
                    "expected_dim": expected_dim,
                    "mismatch": bool(dim is not None and dim != expected_dim),
                    "error": None,
                }
            )
        return rows

    async def health(self) -> dict[str, Any]:
        """Health metrics: Qdrant reachability + per-collection point counts."""
        total_points = 0
        collections_status: list[dict[str, Any]] = []
        try:
            rows = await self.audit()
        except Exception as e:
            logger.warning("rugmaps_qdrant_health_error err=%s", e)
            return {"status": "error", "url": self.url, "error": str(e)}

        for row in rows:
            points = row.get("points_count", 0) if row.get("exists") else 0
            total_points += int(points)
            collections_status.append(
                {
                    "collection": row["collection"],
                    "exists": row["exists"],
                    "points_count": points,
                    "error": row.get("error"),
                }
            )

        # Qdrant is unreachable when every rgm_* info fetch failed.
        errors = [c["error"] for c in collections_status if c.get("error")]
        if len(errors) == len(COLLECTIONS):
            return {
                "status": "error",
                "url": self.url,
                "error": errors[0] or "Qdrant unreachable",
                "collections": len(COLLECTIONS),
                "total_points": 0,
                "collections_status": collections_status,
            }

        return {
            "status": "ok",
            "url": self.url,
            "collections": len(COLLECTIONS),
            "total_points": total_points,
            "collections_status": collections_status,
        }

    async def close(self) -> None:
        """Close the underlying async client (call on shutdown)."""
        try:
            await self.client.close()
        except Exception as e:  # pragma: no cover - close is best-effort
            logger.warning("rugmaps_qdrant_close_error err=%s", e)

    async def __aenter__(self) -> RugMapsQdrant:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


async def get_rugmaps_qdrant() -> RugMapsQdrant:
    """Factory: create a ``RugMapsQdrant`` instance (FastAPI dependency)."""
    return RugMapsQdrant()
