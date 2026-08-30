"""Shill-campaign intelligence domain.

Moves the coordinated-shill / ApiPass raadata out of the Telegram bot and into
the backend so both the bot AND the frontend consume it via the API. This
package owns:

  * the ApiPass X client (``apipass_x``) — real tweet intel with a hard
    ``≤5 createTask/hr`` rate governor + a disk-backed profile cache,
  * coordinated shill-campaign discovery (``detect``),
  * the shill evidence store (``evidence``) — ``shill_evidence`` rows + Qdrant
    ``threat_intel_v2`` points,
  * a thin service layer (``service``) that the router calls.

All I/O is fail-open by contract: a dead ApiPass key, rate limit, scanner, DB
or Qdrant outage never raises — the caller degrades to ``[]`` / ``None`` and
its existing rail.
"""

from __future__ import annotations

from app.domains.shill import apipass_x
from app.domains.shill.detect import ShillCampaign, detect_shill_campaigns
from app.domains.shill.service import (
    detect_campaigns_from_articles,
    get_evidence,
    list_campaigns,
    probe_handle,
    wall_of_shame_feed,
)

__all__ = [
    "ShillCampaign",
    "apipass_x",
    "detect_campaigns_from_articles",
    "detect_shill_campaigns",
    "get_evidence",
    "list_campaigns",
    "probe_handle",
    "wall_of_shame_feed",
]
