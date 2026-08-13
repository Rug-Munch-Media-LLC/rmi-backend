"""
RMI Apify Integration -- Free/cheap external actors as MCP tools.
Actors: Arkham wallet intelligence, web scraping, Twitter data.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rmi_apify")

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
APIFY_BASE = "https://api.apify.com/v2"


def _apify_call(actor_id: str, run_input: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    """Run an Apify actor and return results."""
    if not APIFY_TOKEN:
        return {"error": "APIFY_API_TOKEN not configured"}

    import httpx

    try:
        # Start the actor run
        resp = httpx.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs?waitForFinish={timeout}",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}", "Content-Type": "application/json"},
            json=run_input,
            timeout=timeout + 30,
        )
        if resp.status_code != 200 and resp.status_code != 201:
            return {"error": f"Actor start failed: HTTP {resp.status_code}"}

        run_data = resp.json().get("data", {})
        dataset_id = run_data.get("defaultDatasetId")
        if not dataset_id:
            return {"error": "No dataset ID returned"}

        # Fetch results
        items_resp = httpx.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
            timeout=30,
        )
        if items_resp.status_code != 200:
            return {"error": f"Dataset fetch failed: HTTP {items_resp.status_code}"}

        return {"data": items_resp.json(), "run_id": run_data.get("id")}

    except Exception as e:
        return {"error": str(e)[:200]}


def arkham_wallet_intel(address: str) -> dict[str, Any]:
    """Get Arkham Intelligence wallet data. Near-free via Apify (~$0.03/wallet)."""
    return _apify_call(
        "BFRkJAsA9XBVgzoce",
        {
            "walletAddresses": [address],
            "dataType": "intelligence",
            "proxyConfiguration": {"useApifyProxy": True},
        },
    )


def arkham_wallet_portfolio(address: str, date: str | None = None) -> dict[str, Any]:
    """Get Arkham wallet portfolio/holdings."""
    return _apify_call(
        "BFRkJAsA9XBVgzoce",
        {
            "walletAddresses": [address],
            "dataType": "portfolio",
            "portfolioDate": date,
            "proxyConfiguration": {"useApifyProxy": True},
        },
    )
