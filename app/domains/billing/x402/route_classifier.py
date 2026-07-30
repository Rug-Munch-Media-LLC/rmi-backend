"""Route classifier — determines whether a request path requires x402 payment.

Extracted from enforcement.py (P6 - complexity refactor).

Responsibility: path matching, free-path detection, tool ID extraction,
scanner/bot UA detection. Pure functions, no I/O.
"""

from __future__ import annotations

import re

FREE_GET_PATHS: set[str] = {
    "/api/v1/x402-tools/discovery",
    "/api/v1/x402-tools/frameworks",
    "/api/v1/x402-tools/openai-tools",
    "/api/v1/x402-tools/anthropic-tools",
    "/api/v1/x402-tools/gemini-tools",
    "/api/v1/x402-tools/langchain-tools",
    "/api/v1/x402-tools/catalog",
    "/api/v1/x402-tools/bundles",
    "/api/v1/x402-tools/payment-methods",
    "/api/v1/bulletin-board/x402/info",
}

FREE_PATHS: set[str] = {
    "/api/v1/x402-tools/discovery",
    "/api/v1/x402-tools/frameworks",
    "/api/v1/x402-tools/openai-tools",
    "/api/v1/x402-tools/anthropic-tools",
    "/api/v1/x402-tools/gemini-tools",
    "/api/v1/x402-tools/langchain-tools",
    "/api/v1/x402-tools/catalog",
    "/api/v1/x402-tools/bundles",
    "/api/v1/x402-tools/payment-methods",
    "/api/v1/bulletin-board/x402/info",
    "/api/v1/x402-tools/human-execute",
    "/api/v1/x402-tools/cache/stats",
    "/api/v1/x402-tools/cache/clear",
    "/api/v1/x402-tools/stream/alerts",
    "/api/v1/x402-tools/webhook_list",
}

FREE_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/x402/",
    "/api/v1/developer/",
    "/api/v1/analytics/",
    "/api/v1/state/",
    "/api/v1/status",
    "/api/v1/webhooks/",
    "/api/v1/tools/changelog",
    "/api/v1/tools/version",
    "/api/v1/tools/deprecated",
)

SCANNER_AGENTS: list[str] = [
    "masscan",
    "nmap",
    "nikto",
    "sqlmap",
    "dirbuster",
    "gobuster",
    "zgrab",
    "censysinspect",
]

TOOL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")


def is_x402_tool_path(path: str) -> bool:
    """Check if a request path is targeting the x402 paid-tool surface."""
    return path.startswith("/api/v1/x402-tools/")


def is_free_path(path: str, method: str = "GET") -> bool:
    """Determine if a path is exempt from payment enforcement.

    Covers:
    - Free tool endpoints (discovery, catalog, bundles, etc.)
    - Admin/developer/analytics prefix paths
    - GET requests to free-read-only endpoints
    """
    normalized = path.rstrip("/")
    if normalized in FREE_PATHS:
        return True
    if normalized.startswith(FREE_PATH_PREFIXES):
        return True
    return bool(method == "GET" and normalized in FREE_GET_PATHS)


def extract_tool_id(path: str) -> str | None:
    """Extract the tool identifier from a request path.

    e.g. /api/v1/x402-tools/wallet_scan -> wallet_scan
    """
    parts = path.rstrip("/").split("/")
    if len(parts) >= 5 and parts[-2] == "x402-tools":
        return parts[-1]
    return None


def validate_tool_id(tool_id: str) -> bool:
    """Check that a tool ID contains only allowed characters."""
    return bool(TOOL_ID_PATTERN.match(tool_id))


def is_scanner(user_agent: str) -> bool:
    """Check if the User-Agent header matches known scanners/bots."""
    ua_lower = (user_agent or "").lower()
    return any(bot in ua_lower for bot in SCANNER_AGENTS)
