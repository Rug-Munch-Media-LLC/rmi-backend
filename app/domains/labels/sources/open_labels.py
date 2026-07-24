"""Open Labels Initiative (OLI) source - placeholder.

TODO: wire up when OLI endpoint is confirmed. GitHub repo:
  https://github.com/openlabelsinitiative/OLI

For now returns []. Stub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.labels.models import Label

log = get_logger(__name__)

async def query_open_labels(address: str, chain: str = "ethereum") -> list[Label]:
    """Query OLI for an address's labels.

    Returns [] until OLI endpoint is confirmed and we have API access.
    """
    return []
