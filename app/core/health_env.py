"""Health endpoint environment resolution + config validation.

Split out of ``app.core.health_route`` (de-godding, behavior-preserving):
this module owns service-URL resolution (Docker vs dev defaults) and the
required/optional env-var validation surfaced by ``GET /health`` as
``config_issues``.

See ``app/core/health_route.py`` for the endpoints, ``health_stores.py``
for the store probes, and ``health_rag.py`` for the RAG pipeline probe.
"""

from __future__ import annotations

import os

# ── Environment ──────────────────────────────────────────────────
_ENV = os.getenv("ENVIRONMENT", "production").lower()
_IS_DEV = _ENV in ("dev", "development", "local", "test")


def _get_url(key: str, docker: str, local: str) -> str:
    """Resolve a service URL: explicit env var wins, otherwise pick the
    Docker-Compose default (production) or the localhost default (dev).
    """
    val = os.getenv(key)
    return val if val is not None else (local if _IS_DEV else docker)


# ── Service URLs (module-level for reuse) ────────────────────────
# Defaults use Docker Compose service names so containers resolve
# each other without explicit env vars.  Dev / test mode overrides
# to localhost so ``pytest`` and local ``uvicorn`` work out of the box.
QDRANT_URL = _get_url(
    "QDRANT_URL",
    docker="http://rmi-qdrant:6333",
    local="http://127.0.0.1:6333",
)
CLICKHOUSE_URL = _get_url(
    "CLICKHOUSE_URL",
    docker="http://rmi-clickhouse:8123/",
    local="http://127.0.0.1:8123/",
)
MINIO_URL = _get_url(
    "MINIO_URL",
    docker="http://rmi-minio:9000",
    local="http://127.0.0.1:9000",
)
# reth typically runs on the host or as a sidecar — same default for both
RETH_URL = _get_url(
    "RETH_URL",
    docker="http://127.0.0.1:8545",
    local="http://127.0.0.1:8545",
)

# Fallback constants used by inline os.getenv() in the check functions.
# These mirror the _get_url pattern so module-level validation can
# inspect them without duplicating the URL logic.
_DATABASE_URL_DOCKER = "postgresql://rmi:***@rmi-postgres:5432/rmi"
_DATABASE_URL_DEV = "postgresql://rmi:***@127.0.0.1:5432/rmi"
_NEO4J_URI_DOCKER = "bolt://rmi-neo4j:7687"
_NEO4J_URI_DEV = "bolt://localhost:7687"
_REDIS_URL_DOCKER = "redis://rmi-redis:6379/0"
_REDIS_URL_DEV = "redis://localhost:6379/0"

# ── Config validation schema ────────────────────────────────────
# Required — health check considers these critical
_REQUIRED_CONFIG: dict[str, str] = {
    "DATABASE_URL": "PostgreSQL connection string",
    "REDIS_URL": "Redis connection string (used by app.core.redis)",
    "CLICKHOUSE_URL": "ClickHouse HTTP endpoint",
}
# Optional — health check tolerates these being unset or placeholder
_OPTIONAL_CONFIG: dict[str, str] = {
    "QDRANT_URL": "Qdrant HTTP endpoint",
    "MINIO_URL": "MinIO S3-compatible storage endpoint",
    "NEO4J_URI": "Neo4j bolt URI (NEO4J_URL is also accepted)",
    "RETH_URL": "Ethereum RPC endpoint",
    "CLICKHOUSE_USER": "ClickHouse username (default: rmi)",
    "NEO4J_USER": "Neo4j username (default: neo4j)",
}


def check_config() -> list[dict[str, str]]:
    """Validate required / optional env vars.

    Returns a list of issues (empty = all good).  Each issue has
    ``key``, ``description``, and ``severity`` (``"missing"`` or
    ``"placeholder"``).
    """
    issues: list[dict[str, str]] = []

    _placeholder_patterns = ("***", "changeme", "placeholder", "your_")

    for key, desc in _REQUIRED_CONFIG.items():
        val = os.getenv(key)
        if not val:
            issues.append({"key": key, "description": desc, "severity": "missing"})
        elif any(p in val.lower() for p in _placeholder_patterns):
            issues.append({"key": key, "description": desc, "severity": "placeholder"})

    for key, desc in _OPTIONAL_CONFIG.items():
        val = os.getenv(key)
        if val and any(p in val.lower() for p in _placeholder_patterns):
            issues.append({"key": key, "description": desc, "severity": "placeholder"})

    return issues
