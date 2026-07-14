"""Chainbase source - placeholder, requires API key.

TODO: add CHAINBASE_API_KEY to gopass + env. Free tier: 50K calls/month.

For now returns []. Stub.
"""
from __future__ import annotations

from app.core.logging import get_logger

from app.domains.labels.models import Label

log = get_logger(__name__)


async def query_chainbase(address: str, chain: str = "ethereum") -> list[Label]:
    """Query Chainbase for an address's labels.

    Returns [] until CHAINBASE_API_KEY is configured.
    """
    return []
