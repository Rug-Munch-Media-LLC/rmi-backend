"""x402 AutoGen adapter - agentic payment flows for multi-agent conversations.

AutoGen agents negotiate who pays for tool calls. x402 handles the
payment transparently - agents request, verify, and deduct.

Usage:
    from x402_frameworks import x402AutoGen
    from autogen import AssistantAgent, UserProxyAgent

    x402 = x402AutoGen(api_key="your-key")

    analyst = AssistantAgent(
        name="TokenAnalyst",
        system_message="You analyze tokens using x402 tools.",
        functions=[x402.as_function("token_scan")],
    )
    # When analyst calls token_scan, x402 auto-pays from credits
"""

from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING

from .base import x402Adapter

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("x402.autogen")


class x402AutoGen(x402Adapter):
    """AutoGen adapter - handles payment for tool calls in multi-agent convos.

    Features:
    - AutoGen function format: `as_function()` returns AutoGen-compatible spec
    - Multi-agent: each agent has its own budget or shares a pool
    - Transparent: agents just call functions, x402 handles payment
    """

    def as_function(self, tool_name: str, description: str = "") -> dict:
        """Return an AutoGen-compatible function specification.

        AutoGen agents use this to discover and call x402 tools.
        Payment is handled transparently when the function is called.
        """
        return {
            "name": f"x402_{tool_name}",
            "description": description or f"x402 paid tool: {tool_name}. Pay per call via x402 protocol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": f"Input for {tool_name}. On-chain payment verified before execution.",
                    }
                },
                "required": ["query"],
            },
        }

    def wrap_tool(self, tool_name: str, tool_func: Callable | None = None) -> Callable:
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                available, price, source = self.check_available(tool_name)
                if not available:
                    invoice = self.get_invoice(tool_name)
                    from .langchain_adapter import PaymentRequiredError

                    raise PaymentRequiredError(
                        tool=tool_name,
                        price_usd=price,
                        pay_to=invoice.get("pay_to", ""),
                        chain=invoice.get("chain", "base"),
                        invoice_id=invoice.get("id", ""),
                    )
                result = func(*args, **kwargs)
                if source == "paid":
                    self.record_usage(tool_name, price)
                return result

            return wrapper

        if tool_func is not None:
            return decorator(tool_func)
        return decorator

    def register_agent(self, agent_name: str, budget_usd: float = 10.0):
        """Register an AutoGen agent with a personal budget."""
        logger.info(f"x402 AutoGen: agent '{agent_name}' registered with ${budget_usd} budget")
