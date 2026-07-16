"""x402 Python SDK - pay-per-call AI tool access.

Usage:
  pip install x402-sdk

  from x402_sdk import x402

  # Initialize
  client = x402(api_key="your-key")
  # or without API key (free trials only)
  client = x402()

  # List available tools
  tools = client.list_tools(category="intelligence")
  for t in tools:
      print(f"{t['id']}: ${t['price_usd']}")

  # Create invoice and get payment challenge
  invoice = client.create_invoice(tool="token_scan")

  # Pay on-chain (user connects wallet, sends USDC)
  # Then verify:
  result = client.verify_payment(
      tx_hash="0x...",
      tool="token_scan",
  )
  print(f"Verified: {result['verified']}")

  # Check balance and usage
  balance = client.check_balance()
  usage = client.get_usage(days=7)
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass
class Invoice:
    id: str
    tool: str
    amount_usd: float
    amount_wei: int
    pay_to: str
    chain: str
    signature: str
    created_at: str
    expires_at: float


@dataclass
class VerificationResult:
    verified: bool
    tx_hash: str = ""
    tool: str = ""
    amount_usd: float = 0.0
    chain: str = ""
    payer: str = ""


@dataclass
class ToolInfo:
    id: str
    price_usd: float
    category: str
    trials: int
    description: str = ""


class x402:
    """x402 client - pay-per-call AI tool access.

    Args:
        api_key: Optional API key for authenticated access.
        base_url: x402 gateway URL (default: https://mcp.rugmunch.io)
    """

    def __init__(self, api_key: str = "", base_url: str = "https://mcp.rugmunch.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=30,
        )

    def list_tools(self, category: str = "") -> list[ToolInfo]:
        """List all available x402 tools with prices and trial info.

        Args:
            category: Optional filter (intelligence, premium, monitoring, etc.)

        Returns:
            List of ToolInfo with id, price_usd, category, trials, description
        """
        params = {}
        if category:
            params["category"] = category
        r = self._http.post(
            f"{self.base_url}/mcp/x402",
            json={
                "method": "tools/call",
                "params": {"name": "x402_list_tools", "arguments": params},
            },
        )
        data = r.json().get("result", {})
        text = json.loads(data.get("content", [{}])[0].get("text", "{}"))
        return [ToolInfo(**t) for t in text.get("tools", [])]

    def create_invoice(self, tool: str, agent_id: str = "") -> Invoice:
        """Create a payment challenge for a tool.

        Returns an invoice with amount, pay_to address, chain, and HMAC
        signature. Pay on-chain, then call verify_payment().

        Args:
            tool: Tool ID (e.g., 'token_scan', 'rug_probability')
            agent_id: Optional agent identifier

        Returns:
            Invoice with payment details
        """
        r = self._http.post(
            f"{self.base_url}/mcp/x402",
            json={
                "method": "tools/call",
                "params": {
                    "name": "x402_create_invoice",
                    "arguments": {"tool": tool, "agent_id": agent_id or ""},
                },
            },
        )
        data = r.json().get("result", {})
        text = json.loads(data.get("content", [{}])[0].get("text", "{}"))
        return Invoice(**text)

    def verify_payment(self, tx_hash: str, tool: str, amount_usd: float = 0) -> VerificationResult:
        """Verify an on-chain payment.

        Once verified, the caller is authorized to use the tool.

        Args:
            tx_hash: Transaction hash from the on-chain payment
            tool: Tool ID that was paid for
            amount_usd: Expected payment amount

        Returns:
            VerificationResult with verification status
        """
        r = self._http.post(
            f"{self.base_url}/mcp/x402",
            json={
                "method": "tools/call",
                "params": {
                    "name": "x402_verify_payment",
                    "arguments": {"tx_hash": tx_hash, "tool": tool, "amount_usd": amount_usd},
                },
            },
        )
        data = r.json().get("result", {})
        text = json.loads(data.get("content", [{}])[0].get("text", "{}"))
        return VerificationResult(**text)

    def check_balance(self, agent_id: str = "") -> dict:
        """Check remaining free trials and usage.

        Args:
            agent_id: Agent identifier

        Returns:
            Dict with balances per tool
        """
        r = self._http.post(
            f"{self.base_url}/mcp/x402",
            json={
                "method": "tools/call",
                "params": {
                    "name": "x402_check_balance",
                    "arguments": {"agent_id": agent_id or "sdk_user"},
                },
            },
        )
        data = r.json().get("result", {})
        return json.loads(data.get("content", [{}])[0].get("text", "{}"))

    def get_usage(self, agent_id: str = "", days: int = 7) -> dict:
        """View usage history and spending.

        Args:
            agent_id: Agent identifier
            days: Days of history

        Returns:
            Dict with usage stats
        """
        r = self._http.post(
            f"{self.base_url}/mcp/x402",
            json={
                "method": "tools/call",
                "params": {
                    "name": "x402_get_usage",
                    "arguments": {"agent_id": agent_id or "sdk_user", "days": days},
                },
            },
        )
        data = r.json().get("result", {})
        return json.loads(data.get("content", [{}])[0].get("text", "{}"))
