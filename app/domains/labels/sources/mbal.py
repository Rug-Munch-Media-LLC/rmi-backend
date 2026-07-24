"""

MBAL query adapter for the federated label API.

Implements the MBAL source API for the federated label system.

Usage (called by federated.py):
    labels = await query_mbal("0x...", "ethereum")

Pattern: async function that returns list[Label]
"""

from __future__ import annotations

from app.domains.labels.models import Label
from app.domains.labels.sources.mbal_source import get_mbalsy_service

async def query_mbal(address: str, chain: str) -> list[Label]:
    """
    Query MBAL for labels for an address.

    Args:
        address: The wallet address to look up
        chain: The blockchain network (ethereum, bitcoin, etc.)

    Returns:
        List of labels from MBAL
    """
    # Get or initialize the MBAL service
    service = get_mbalsy_service()
    return await service.get_labels(address, chain)
