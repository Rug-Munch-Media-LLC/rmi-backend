"""Exact point count for one Qdrant collection."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def exact_count(collection: str, qdrant: str) -> int:
    """Return the exact point count, 0 when the collection is unreadable.

    Exact counts are requested because the incremental field lags, and a lagging
    number would report a finished migration as incomplete.
    """
    request = urllib.request.Request(  # noqa: S310 - loopback qdrant only
        f"{qdrant}/collections/{collection}/points/count",
        data=json.dumps({"exact": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        body = json.loads(urllib.request.urlopen(request, timeout=60).read())  # noqa: S310 - loopback qdrant
        return int(body["result"]["count"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return 0
