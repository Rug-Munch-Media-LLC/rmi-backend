"""Agent tools — wrappers around RMI capabilities for LLM tool-use."""

from __future__ import annotations

from typing import Any


class AgentTool:
    """Base tool for agent use."""

    name: str
    description: str

    async def execute(self, **kwargs) -> Any:
        raise NotImplementedError

class ScanTokenTool(AgentTool):
    name = "scan_token"
    description = "Scan a token for rug pull risks"

    async def execute(self, chain: str, address: str) -> dict:
        # TODO: Wire to actual scanner
        return {"chain": chain, "address": address, "status": "pending integration"}

class WalletLookupTool(AgentTool):
    name = "wallet_lookup"
    description = "Look up wallet profile and transaction history"

    async def execute(self, address: str) -> dict:
        return {"address": address, "status": "pending integration"}
