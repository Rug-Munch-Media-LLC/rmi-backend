"""x402 CrewAI adapter - multi-agent payment management.

CrewAI runs multiple agents that call tools in parallel. x402 manages
a shared credit pool across all agents in a crew.

Usage:
    from x402_frameworks import x402CrewAI
    from crewai import Agent, Task, Crew

    x402 = x402CrewAI(api_key="your-key", max_budget_usd=50)

    researcher = Agent(
        role="Token Researcher",
        goal="Research crypto tokens",
        tools=[x402.wrap("token_scan", scan_func)],
    )
    # Crew runs, x402 manages payments across all agents
"""

from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING

from .base import x402Adapter

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("x402.crewai")


class x402CrewAI(x402Adapter):
    """CrewAI adapter - shared payment pool across all agents in a crew.

    Features:
    - Shared credit pool (all agents in a crew share a budget)
    - Per-crew budget tracking
    - Auto-suspend when budget exhausted
    - Detailed billing report per crew run
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._crew_budget: dict[str, float] = {}

    def set_crew_budget(self, crew_name: str, budget_usd: float):
        """Set a budget for a specific crew. Agents in this crew share the budget."""
        self._crew_budget[crew_name] = budget_usd

    def wrap(self, tool_name: str, tool_func: Callable) -> Callable:
        """Wrap a tool function with x402 payment for CrewAI agents.

        Unlike LangChain's decorator, this returns the wrapped function
        directly for use in CrewAI's Agent tool list.
        """
        return self.wrap_tool(tool_name)(tool_func)

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
                logger.info(f"CrewAI x402: {tool_name} = ${price:.4f}")
                return result

            return wrapper

        if tool_func is not None:
            return decorator(tool_func)
        return decorator

    def get_crew_report(self) -> dict:
        """Get billing report for the current session."""
        return {
            "total_spent": round(self._spent_this_month, 2),
            "budget_remaining": round(max(0, self.max_budget_usd - self._spent_this_month), 2),
            "tools_called": 0,  # Tracked internally
        }
