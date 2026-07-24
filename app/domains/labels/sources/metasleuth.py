"""MetaSleuth (BlockSec AML) source - live API at aml.blocksec.com.

Endpoint: POST https://aml.blocksec.com/address-label/api/v3/labels
  Headers: API-KEY: <key>
  Body: {"chain_id": 1, "address": "0x..."}

Verified working: 2026-06-22 (Binance 0x3d12... returned main_entity=Binance,
entity categories EXCHANGE + OTC DESK).

Limit: 10 calls/day per API key on the free tier. So we cache aggressively.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

log = get_logger(__name__)

METASLEUTH_BASE = "https://aml.blocksec.com/address-label/api/v3"
# Free tier: 10 calls/day per key on risk-level, similar on labels
DEFAULT_TIMEOUT = 8.0

# Chain name -> MetaSleuth chain_id
_CHAIN_MAP = {
    "ethereum": 1,
    "bitcoin": -1,
    "tron": -2,
    "solana": -3,
    "monero": -4,
    "sui": 5,
    "optimism": 10,
    "cronos": 25,
    "bsc": 56,
    "base": 8453,
    "arbitrum": 42161,
    "polygon": 137,
}


async def query_metasleuth(address: str, chain: str = "ethereum") -> list[Label]:
    """Query MetaSleuth for an address's entity info.

    Returns:
        - 1 entity label with the main entity name (e.g. "Binance")
        - Optional category labels (EXCHANGE, OTC DESK, etc.)
        - Attributes (DEPOSIT ADDRESS, etc.)
    """
    api_key = os.getenv("METASLEUTH_LABELS_API_KEY", "")
    if not api_key:
        # Try gopass via the standard pattern
        try:
            import subprocess

            result = subprocess.run(
                ["gopass", "show", "-o", "rmi/infra/metasleuth_labels_api_key"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                api_key = result.stdout.strip()
        except (subprocess.TimeoutExpired):
            pass

    if not api_key:
        log.debug("metasleuth_no_api_key configured, skipping")
        return []

    chain_id = _CHAIN_MAP.get(chain.lower())
    if chain_id is None:
        log.debug("metasleuth_unsupported_chain chain=%s", chain)
        return []

    def _call_api() -> dict[str, Any]:
        """Sync HTTP call (run in thread)."""
        try:
            r = httpx.post(
                f"{METASLEUTH_BASE}/labels",
                headers={"API-KEY": api_key, "Content-Type": "application/json"},
                json={"chain_id": chain_id, "address": address},
                timeout=DEFAULT_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") == 200000:
                return data.get("data") or {}
            log.warning("metasleuth_api_err code=%s msg=%s", data.get("code"), data.get("message"))
            return {}
        except httpx.HTTPError as e:
            log.warning("metasleuth_api_http_err: %s", e)
            return {}
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e :
            log.warning("metasleuth_api_err: %s", e)
            return {}

    data = await asyncio.to_thread(_call_api)
    if not data:
        return []

    labels: list[Label] = []
    main_entity = data.get("main_entity")
    if main_entity:
        labels.append(
            Label(
                address=address.lower(),
                chain=chain,
                label_type="entity",
                label_value=main_entity,
                source=LabelSource.METASLEUTH,
                confidence=0.95,
                verified_at=datetime.now(UTC),
                entity=main_entity,
                entity_category=_first_category(data),
                attributes=_extract_attributes(data),
            )
        )

    # Also emit labels for each category
    categories = data.get("main_entity_info", {}).get("categories", [])
    if not isinstance(categories, list):
        categories = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cat_name = cat.get("name")
        if cat_name and cat_name != main_entity:
            labels.append(
                Label(
                    address=address.lower(),
                    chain=chain,
                    label_type="category",
                    label_value=cat_name,
                    source=LabelSource.METASLEUTH,
                    confidence=0.9,
                    verified_at=datetime.now(UTC),
                    entity=main_entity,
                    entity_category=cat_name,
                    attributes={"code": cat.get("code")},
                )
            )

    return labels


def _first_category(data: dict[str, Any]) -> str | None:
    """Extract first category name from MetaSleuth response."""
    cats = data.get("main_entity_info", {}).get("categories", [])
    if cats and isinstance(cats[0], dict):
        return cats[0].get("name")
    return None


def _extract_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Extract attributes from MetaSleuth response."""
    attrs = data.get("main_entity_info", {}).get("attributes", [])
    out = {}
    for a in attrs:
        if isinstance(a, dict):
            name = a.get("name", "")
            if name:
                out[name] = a.get("code")
    # Add description (website/twitter) if present
    desc = data.get("main_entity_info", {}).get("description", {})
    if isinstance(desc, dict):
        for k, v in desc.items():
            if v:
                out[k] = v
    return out
