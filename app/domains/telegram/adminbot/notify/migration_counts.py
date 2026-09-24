"""Count migrated versus source points across the corpus."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

QDRANT = os.environ.get("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
EXEMPT = "agent_vectors"


def counts() -> tuple[int, int]:
    """Return (migrated, total) for the live corpus.

    Exact counts are requested because the incremental field lags, and a lagging
    number would report a finished migration as incomplete.
    """
    try:
        body = json.loads(urllib.request.urlopen(f"{QDRANT}/collections", timeout=30).read())  # noqa: S310 - loopback qdrant
        names = [c["name"] for c in body["result"]["collections"]]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return 0, 0
    from .migration_count import exact_count

    migrated = sum(exact_count(n, QDRANT) for n in names if n.endswith("_8b"))
    total = sum(exact_count(n, QDRANT) for n in names if not n.endswith("_8b") and n != EXEMPT)
    return migrated, total
