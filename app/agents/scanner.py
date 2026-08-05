"""Scanner Agent — token risk analysis via the RugMunchScan API.

Specialist agent built on Pydantic AI. Emits a typed ``TokenRiskResult`` by
consulting the local scanner service (``POST /api/v2/scanner/token/scan``).

The scan tool degrades gracefully: if the scanner service is unreachable it
returns a structured error dict so the model can still produce a
best-effort typed assessment instead of crashing the run.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.agents.config import (
    DEFAULT_CHAIN,
    DEFAULT_TEMPERATURE,
    HTTP_TIMEOUT,
    SCANNER_API_KEY,
    SCANNER_API_URL,
    get_agent_model,
)
from app.agents.models import TokenRiskResult

scanner_agent = Agent(
    get_agent_model(),
    output_type=TokenRiskResult,
    system_prompt=(
        "You are a crypto security agent specializing in token risk analysis. "
        "When given a token address, call scan_token() to fetch on-chain risk "
        "data, then summarize the findings into the TokenRiskResult schema. "
        "If the scan API is unavailable, still produce a best-effort "
        "assessment from the address and any information the caller provides."
    ),
    model_settings=ModelSettings(temperature=DEFAULT_TEMPERATURE),
)


async def _fetch_scan(address: str, chain: str) -> dict[str, Any]:
    """Call the RugMunchScan API. Returns a JSON-serializable dict, never raises."""
    headers: dict[str, str] = {}
    if SCANNER_API_KEY:
        # Service-to-service auth; also bypasses the cookie-based CSRF guard.
        headers["X-API-Key"] = SCANNER_API_KEY
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                SCANNER_API_URL,
                json={"address": address, "chain": chain},
                headers=headers,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
    except Exception as exc:  # tool must degrade gracefully
        return {
            "error": f"scan service unavailable: {exc}",
            "address": address,
            "chain": chain,
        }


@scanner_agent.tool_plain
async def scan_token(address: str, chain: str = DEFAULT_CHAIN) -> dict[str, Any]:
    """Call RugMunchScan API for token analysis.

    Args:
        address: Token contract address to analyze.
        chain: Blockchain name (e.g. "ethereum", "solana", "bsc").
    """
    return await _fetch_scan(address, chain)
