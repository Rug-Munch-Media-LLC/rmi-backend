"""Eth-labels router — /api/v1/eth-labels/*.

Exposes the 115K-address eth-labels dataset (dawsbot) through the REST
surface consumed by honeypot_map, impersonation_detector,
criminal_clusters, address_profiler and rug_recovery.

The provider returns raw rows ({chain_id, address, label, name_tag}); this
router normalizes every row to a stable consumer-facing shape with
`contract_address`, `label`, `name`, `chain` and `risk_score` keys so that
consumers' `.get()` calls never crash on missing fields.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.databus.eth_labels_provider import fetch_eth_labels

logger = get_logger("eth-labels.router")

router = APIRouter(prefix="/api/v1/eth-labels", tags=["eth-labels"])

# chain_id -> canonical chain name (15 chains in eth-labels.db)
CHAIN_NAMES: dict[int, str] = {
    1: "ethereum",
    10: "optimism",
    56: "bsc",
    100: "gnosis",
    137: "polygon",
    250: "fantom",
    1284: "moonbeam",
    1285: "moonriver",
    42161: "arbitrum",
    42220: "celo",
    43114: "avalanche",
    8453: "base",
    59144: "linea",
    534352: "scroll",
    7777777: "zora",
    204: "opbnb",
}

# label keyword -> risk keyword groups. The DB has no risk column; derive a
# heuristic score so consumers can render a risk gauge without crashing.
_RISK_KEYWORDS: tuple[tuple[str, int], ...] = (
    ("honeypot", 90),
    ("scam", 85),
    ("phish", 85),
    ("fraud", 80),
    ("rug", 80),
    ("exploit", 75),
    ("malicious", 75),
    ("hack", 70),
    ("mixer", 70),
    ("tornado", 65),
    ("sanction", 60),
    ("mev-bot", 60),
)

_SAFE_KEYWORDS: tuple[str, ...] = (
    "exchange",
    "cex",
    "bridge",
    "wallet",
    "genesis",
    "burn",
    "blocked",
    "charity",
    "nonprofit",
)

# label keyword groups for the ?type= filter and per-entry `type` field
_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "contract": (
        "contract",
        "token",
        "pool",
        "swap",
        "vault",
        "factory",
        "router",
        "bridge",
        "staking",
        "yield",
    ),
    "deployer": ("deployer", "creator", "owner", "eoa"),
    "wallet": ("wallet", "users", "account", "multisig"),
}


def _chain_name(chain_id: int) -> str:
    return CHAIN_NAMES.get(chain_id, str(chain_id))


def _risk_score(label: str) -> int:
    low = label.lower()
    for kw, score in _RISK_KEYWORDS:
        if kw in low:
            return score
    if any(kw in low for kw in _SAFE_KEYWORDS):
        return 10
    return 30


def _entry_type(label: str) -> str:
    low = label.lower()
    for etype, kws in _TYPE_KEYWORDS.items():
        if any(k in low for k in kws):
            return etype
    return "wallet"


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a provider row into the consumer-facing label shape."""
    chain_id = int(row.get("chain_id") or 1)
    label = str(row.get("label") or "")
    address = str(row.get("address") or "")
    name = str(row.get("name_tag") or label or address)
    return {
        "contract_address": address,
        "label": label,
        "name": name,
        "chain": _chain_name(chain_id),
        "risk_score": _risk_score(label),
        # extra keys the consumers read directly:
        "type": _entry_type(label),
        "address": address,
    }


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize(r) for r in rows]


def _rows_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    if "error" in data:
        raise HTTPException(status_code=500, detail=str(data["error"]))
    return list(data.get("labels") or data.get("results") or [])


@router.get("/search")
async def search_labels(
    address: str | None = Query(None, description="Address to look up (full or partial match)"),
    q: str | None = Query(None, description="Label / name-tag keyword search"),
    limit: int = Query(20, ge=1, le=100),
    chain: str | None = Query(None, description="Filter by chain name (e.g. ethereum, bsc, base)"),
    type: str | None = Query(None, description="Filter by entry type (contract, deployer, wallet)"),
) -> dict[str, Any]:
    """Search the eth-labels dataset by address or label keyword."""
    if not address and not q:
        raise HTTPException(status_code=400, detail="Provide either `address` or `q`")

    try:
        if address:
            data = await fetch_eth_labels(address=address, limit=limit)
        else:
            data = await fetch_eth_labels(label=q, limit=limit)
    except Exception as e:
        logger.error("eth_labels_search_error err=%s", e)
        raise HTTPException(status_code=500, detail="eth-labels database query failed") from e

    entries = _normalize_rows(_rows_from(data))

    if chain:
        chain_key = chain.lower()
        entries = [e for e in entries if e["chain"] == chain_key]

    if type:
        kws = _TYPE_KEYWORDS.get(type.lower())
        if kws:
            entries = [e for e in entries if any(k in e["label"].lower() for k in kws)]

    return {
        "query": address or q,
        "matches": len(entries),
        "labels": entries,
        "source": "eth-labels (dawsbot)",
    }


@router.get("/lookup/{address}")
async def lookup_address(address: str) -> dict[str, Any]:
    """Look up every label known for an address (exact, then partial match)."""
    try:
        data = await fetch_eth_labels(address=address, limit=50)
    except Exception as e:
        logger.error("eth_labels_lookup_error address=%s err=%s", address[:10], e)
        raise HTTPException(status_code=500, detail="eth-labels database query failed") from e

    rows = _rows_from(data)
    entries = _normalize_rows(rows)

    return {
        "address": address,
        "matches": len(entries),
        "labels": entries,
        "source": "eth-labels (dawsbot)",
    }
