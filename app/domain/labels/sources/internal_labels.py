"""Internal labels source - our own growing set in Postgres/Redis.

Currently returns []. The schema for internal labels (community
contributions, RMI-curated tags) is being designed separately.

TODO: query Postgres internal_labels table once schema lands.
"""
from __future__ import annotations

import logging

from app.domain.labels.models import Label

log = logging.getLogger(__name__)


async def query_internal(address: str, chain: str = "ethereum") -> list[Label]:
    """Query internal labels database.

    Returns [] until internal_labels table is provisioned.
    """
    return []
