"""Internal labels source - our own growing set in Postgres/Redis.

Currently returns []. The schema for internal labels (community
contributions, RMI-curated tags) is being designed separately.

TODO: query Postgres internal_labels table once schema lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.labels.models import Label

log = get_logger(__name__)


async def query_internal(address: str, chain: str = "ethereum") -> list[Label]:
    """Query internal labels database.

    Returns [] until internal_labels table is provisioned.
    """
    return []
