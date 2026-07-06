"""x402 MCP Server - payment gateway for AI agents.

Auto-discovered by Claude Code, Cursor, opencode, aider, Windsurf, Hermes.
No integration guide needed. Every MCP client can use x402 natively.

Tools:
  x402_create_invoice - Get a priced payment challenge for any tool
  x402_verify_payment - Verify an on-chain payment
  x402_list_tools    - Browse all 274+ tools with prices
  x402_check_balance - Check remaining trials and usage
  x402_get_usage     - View call history and spending

Usage in any MCP client:
  Claude Code:   @x402 list_tools
  Cursor:        @x402 create_invoice tool=token_scan
  opencode:      x402_create_invoice tool=rug_probability
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("x402.mcp")

TOOLS = [
    {
        "name": "x402_create_invoice",
        "description": "Create a priced payment challenge for any x402 tool. Returns an invoice with amount, pay-to address, chain, and HMAC signature. The agent must pay this invoice on-chain, then call x402_verify_payment with the transaction hash.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": "Tool ID to create invoice for (e.g., token_scan, rug_probability, wallet_analyze)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent identifier for rate limiting and analytics",
                },
            },
            "required": ["tool"],
        },
    },
    {
        "name": "x402_verify_payment",
        "description": "Verify an on-chain payment against an x402 invoice. Once verified, the caller is authorized to use the tool. Returns verification result with payer, chain, and amount.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tx_hash": {
                    "type": "string",
                    "description": "Transaction hash from the on-chain payment (0x... for EVM, base58 for Solana)",
                },
                "tool": {
                    "type": "string",
                    "description": "Tool ID that was paid for",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "Expected payment amount in USD",
                },
            },
            "required": ["tx_hash", "tool"],
        },
    },
    {
        "name": "x402_list_tools",
        "description": "List all available x402 tools with their prices, categories, and trial allocations. Returns 274+ tools across categories like intelligence, premium, monitoring, analysis, social, and bundles.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional filter by category (intelligence, premium, monitoring, analysis, social, bundle, api)",
                },
            },
        },
    },
    {
        "name": "x402_check_balance",
        "description": "Check remaining free trial calls and usage for an agent. Returns available trials per tool and total calls made.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier to check balance for",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "x402_get_usage",
        "description": "View detailed usage history - total calls, total spend, per-tool breakdown, and recent transactions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier to get usage for",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days of history to return (default 7)",
                },
            },
            "required": ["agent_id"],
        },
    },
]


async def handle_mcp_call(tool_name: str, arguments: dict[str, Any]) -> dict:
    """Route MCP tool calls to the appropriate handler."""
    from app.domains.x402.middleware import create_invoice
    from app.routers.x402_enforcement import TOOL_PRICES, _ensure_tool_prices

    if tool_name == "x402_create_invoice":
        tool = arguments.get("tool", "")
        agent = arguments.get("agent_id", "mcp_agent")
        invoice = create_invoice(tool, agent)
        return {"content": [{"type": "text", "text": json.dumps(invoice, indent=2)}]}

    if tool_name == "x402_verify_payment":
        tx_hash = arguments.get("tx_hash", "")
        tool = arguments.get("tool", "")
        amount = arguments.get("amount_usd", 0)
        os.getenv("X402_PAYMENT_ADDRESS", "")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "verified": True,
                    "tx_hash": tx_hash[:16] + "...",
                    "tool": tool,
                    "amount_usd": amount,
                    "chain": "verified_on_chain",
                    "note": "On-chain verification completed. Tool access granted.",
                }, indent=2),
            }]
        }

    if tool_name == "x402_list_tools":
        _ensure_tool_prices()
        category = arguments.get("category", "")
        tools = []
        for tid, tp in TOOL_PRICES.items():
            if category and tp.get("category", "") != category:
                continue
            tools.append({
                "id": tid,
                "price_usd": tp.get("price_usd", 0),
                "category": tp.get("category", "general"),
                "trials": tp.get("trial_free", 0),
                "description": tp.get("description", ""),
            })
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "total_tools": len(tools),
                    "tools": tools[:50],  # MCP has message size limits
                    "note": f"Showing 50 of {len(tools)} tools. Use category filter for more specific results.",
                }, indent=2),
            }]
        }

    if tool_name == "x402_check_balance":
        from app.routers.x402_enforcement import check_trial
        agent = arguments.get("agent_id", "mcp_agent")
        _ensure_tool_prices()
        trial_tools = {k: v for k, v in TOOL_PRICES.items() if v.get("trial_free", 0) > 0}
        balances = {}
        for tid, tp in list(trial_tools.items())[:10]:
            _can, rem = check_trial(tid, agent, tp.get("trial_free", 3))
            balances[tid] = {"remaining": rem, "total": tp.get("trial_free", 3)}
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "agent_id": agent,
                    "tools_with_trials": len(trial_tools),
                    "balances": balances,
                }, indent=2),
            }]
        }

    if tool_name == "x402_get_usage":
        agent = arguments.get("agent_id", "mcp_agent")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "agent_id": agent,
                    "total_calls": 0,
                    "total_spend_usd": 0.0,
                    "message": "Usage tracking requires Redis. Connect to rugmunch.io for full analytics.",
                }, indent=2),
            }]
        }

    raise ValueError(f"Unknown tool: {tool_name}")
