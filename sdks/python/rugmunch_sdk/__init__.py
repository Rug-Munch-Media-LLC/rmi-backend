"""rugmunch_sdk - friendly Python wrapper around the RMI API.

v4.0 Sovereign-first. Auto-generated from /openapi.json then wrapped
with a friendly ergonomic interface.

Quick start:
    >>> from rugmunch_sdk import RMI
    >>> client = RMI(base_url="https://api.rugmunch.io", api_key="...")
    >>> risk = await client.get_token_risk(chain="solana", address="Dez...")
    >>> print(risk.tier, risk.score)
"""
from .client import RMI, RMI_sync, SyncRMI
from .models import (
    McpTool,
    NewsItem,
    RagHit,
    Report,
    TokenRisk,
)

__version__ = "0.1.0"
__all__ = [
    "RMI",
    "McpTool",
    "NewsItem",
    "RMI_sync",
    "RagHit",
    "Report",
    "SyncRMI",
    "TokenRisk",
]
