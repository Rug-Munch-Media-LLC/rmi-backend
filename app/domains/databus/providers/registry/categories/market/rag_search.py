"""
Market provider chains — DeFi protocol + RAG search.

Chain: rag_search. Split out of ``market/token_intel.py`` so the market facade
can register it after the optional-news chains, which is where the pre-split
``market.py`` put it. ``defi_protocols`` lives in ``market/defi_protocols.py``.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_rag_search_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """RAG-based semantic search across intelligence data."""
    return {
        "rag_search": ProviderChain(
            data_type="rag_search",
            description="RAG-based semantic search across intelligence data",
            providers=[
                Provider("local_rag", ProviderTier.LOCAL, g["_passthrough_rag"], weight=10.0),
            ],
        ),
    }
