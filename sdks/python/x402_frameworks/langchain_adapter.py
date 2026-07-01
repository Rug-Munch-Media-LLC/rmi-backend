"""x402 LangChain adapter — transparent payment for LangChain tools.

Usage:
    from x402_frameworks import x402LangChain
    from langchain.tools import tool

    # Initialize x402
    x402 = x402LangChain(api_key="your-key", auto_pay=True)

    # Wrap any tool — payment is handled automatically
    @x402.wrap_tool("token_scan")
    @tool
    def scan_token(address: str) -> dict:
        \"\"\"Scan a token for rug pull indicators.\"\"\"
        return {"risk": "low", "score": 85}

    # Use with any LangChain agent
    from langchain.agents import create_react_agent
    agent = create_react_agent(llm, [scan_token], prompt)
    # When agent calls scan_token, x402 checks payment first
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional

from .base import x402Adapter

logger = logging.getLogger("x402.langchain")


class x402LangChain(x402Adapter):
    """LangChain adapter — wraps tools with automatic x402 payment.

    Every tool call checks:
    1. Free trial available? → use it
    2. Prepaid credits? → deduct
    3. Auto-pay enabled? → create invoice (dev must fund wallet)
    4. None? → raise PaymentRequired error
    """

    def wrap_tool(self, tool_name: str, tool_func: Optional[Callable] = None) -> Callable:
        """Decorator: wrap a LangChain tool function with x402 payment.

        Usage as decorator:
            @x402.wrap_tool("token_scan")
            @tool
            def my_tool(address: str): ...

        Usage as wrapper:
            wrapped = x402.wrap_tool("token_scan")(my_tool_func)
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                available, price, source = self.check_available(tool_name)
                if not available:
                    invoice = self.get_invoice(tool_name)
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


class PaymentRequiredError(Exception):
    """Raised when a tool requires payment before execution.

    The caller should display the invoice to the user for payment.
    """

    def __init__(
        self,
        tool: str = "",
        price_usd: float = 0.01,
        pay_to: str = "",
        chain: str = "base",
        invoice_id: str = "",
    ):
        self.tool = tool
        self.price_usd = price_usd
        self.pay_to = pay_to
        self.chain = chain
        self.invoice_id = invoice_id
        super().__init__(
            f"Payment required: ${price_usd} for {tool}. "
            f"Send USDC on {chain} to {pay_to[:16]}... (invoice: {invoice_id[:16]}...)"
        )
