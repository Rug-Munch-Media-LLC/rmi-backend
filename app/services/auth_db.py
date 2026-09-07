"""DB accessor for auth flows.

Routers must not reach into ``app.db_client`` directly (import-boundary
contract ``no-router-db`` — see ``.importlinter``). This service-layer
accessor keeps auth DB access on the legal routers → services → db_client
path while preserving the exact behavior of ``app.db_client.get_db``.
"""

from __future__ import annotations

from app.db_client import RmiDatabase, get_db


def get_auth_db() -> RmiDatabase:
    """Return the shared RMI database client for auth operations."""
    return get_db()


__all__ = ["get_auth_db"]
