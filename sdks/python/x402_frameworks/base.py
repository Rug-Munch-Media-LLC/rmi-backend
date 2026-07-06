"""x402 Framework Adapter - base class for all AI framework integrations.

Same pattern for every framework:
  1. Wrap a tool call
  2. Check if user has credits/trials
  3. If not, create invoice and request payment
  4. Execute tool
  5. Deduct credits

Developer writes normal code. x402 handles the payment transparently.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger("x402.framework")

X402_BASE_URL = os.getenv("X402_BASE_URL", "https://mcp.rugmunch.io/mcp/x402")


class x402Adapter(ABC):
    """Base adapter for AI framework x402 integration.

    Args:
        api_key: x402 API key for authenticated access
        base_url: x402 MCP endpoint URL
        auto_pay: If True, automatically pay invoices (requires funded account)
        max_budget_usd: Monthly budget cap (0 = unlimited)
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = X402_BASE_URL,
        auto_pay: bool = False,
        max_budget_usd: float = 0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.auto_pay = auto_pay
        self.max_budget_usd = max_budget_usd
        self._spent_this_month: float = 0.0
        self._client = httpx.Client(
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=30,
        )
        self._cache: dict[str, Any] = {}

    def _mcp_call(self, tool: str, arguments: dict) -> dict:
        """Call an x402 MCP tool."""
        r = self._client.post(
            self.base_url,
            json={
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        data = r.json().get("result", {})
        text = data.get("content", [{}])[0].get("text", "{}")
        return json.loads(text) if isinstance(text, str) else text

    def check_available(self, tool: str) -> tuple[bool, float, str]:
        """Check if a tool can be called - returns (available, price_usd, message).

        First checks trials, then checks prepaid credits/budget.
        """
        # Check trial availability
        pricing = self._mcp_call("x402_check_balance", {"agent_id": f"framework:{id(self)}"})
        if pricing.get("balances"):
            for tid, bal in pricing["balances"].items():
                if tid == tool and bal.get("remaining", 0) > 0:
                    return True, 0.0, "trial"

        # Check budget
        if self.max_budget_usd > 0 and self._spent_this_month >= self.max_budget_usd:
            return False, 0.0, f"Monthly budget ${self.max_budget_usd} exhausted"

        # Look up price
        tools = self._mcp_call("x402_list_tools", {"category": ""})
        for t in tools.get("tools", []):
            if t.get("id") == tool:
                price = t.get("price_usd", 0.01)
                return True, price, "paid"

        return False, 0.0, f"Unknown tool: {tool}"

    def get_invoice(self, tool: str) -> dict:
        """Get a payment invoice for a tool. Returns invoice with amount and pay_to address."""
        return self._mcp_call("x402_create_invoice", {"tool": tool, "agent_id": f"framework:{id(self)}"})

    def record_usage(self, tool: str, cost_usd: float):
        """Record tool usage for budget tracking."""
        self._spent_this_month += cost_usd
        logger.info(f"x402: called {tool} - ${cost_usd:.4f} (monthly: ${self._spent_this_month:.2f})")

    def list_tools(self, category: str = "") -> list[dict]:
        """List available tools with prices."""
        return self._mcp_call("x402_list_tools", {"category": category}).get("tools", [])

    @abstractmethod
    def wrap_tool(self, tool_name: str, tool_func: Callable) -> Callable:
        """Wrap a tool function with x402 payment processing.

        The returned function checks payment, calls the tool, deducts cost.
        Framework-specific implementations handle the tool registration.
        """
        ...
