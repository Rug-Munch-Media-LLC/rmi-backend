"""RugMaps — wallet cluster intelligence domain.

Phase 0: Qdrant vector backend for wallet embeddings, cluster metadata,
cluster patterns, and entity labels. The ``RugMapsQdrant`` wrapper
(``app.domains.rugmaps.qdrant``) manages the ``rgm_*`` collections and
provides health/audit helpers used by ``scripts/ops/rugmaps_qdrant.py``.
"""

from app.domains.rugmaps.qdrant import (
    COLLECTIONS,
    RugMapsQdrant,
    get_rugmaps_qdrant,
)

__all__ = [
    "COLLECTIONS",
    "RugMapsQdrant",
    "get_rugmaps_qdrant",
]
