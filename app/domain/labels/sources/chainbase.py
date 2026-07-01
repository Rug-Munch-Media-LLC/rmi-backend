"""Chainbase source — placeholder, requires API key.

TODO: add CHAINBASE_API_KEY to gopass + env. Free tier: 50K calls/month.

For now returns []. Stub.
"""
from __future__ import annotations

import logging

from app.domain.labels.models import Label

log = logging.getLogger(__name__)


async def query_chainbase(address: str, chain: str = "ethereum") -> list[Label]:
    """Query Chainbase for an address's labels.

    Returns [] until CHAINBASE_API_KEY is configured.
    """
    return []
