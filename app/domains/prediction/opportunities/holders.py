"""Public-wallet concentration evidence (Polymarket Data API v2).

Endpoint + parameters are corpus-verified against
``Drakkar-Software/OctoBot`` ``ccxt/polymarket_async.py::data_public_get_holders``:

    GET https://data-api.polymarket.com/holders?market=<conditionId>&limit=&offset=

``market`` takes comma-separated **condition IDs**. Only public, already-published
holder rows are read — no wallet enumeration, no private data.

[REVIEW] The response envelope is not schematised by the public docs we hold, so
parsing is defensive: we accept a bare list or an ``holders``/``data`` key, and we
read wallet/amount keys through a small alias table. When nothing parses we return
``available=False`` with a reason instead of inventing numbers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger
from app.domains.prediction.opportunities.models import HolderEntry, WhaleEvidence

if TYPE_CHECKING:
    from app.domains.prediction.opportunities.rate_limit import RateLimitTracker

logger = get_logger(__name__)

DATA_API_HOST = "data-api.polymarket.com"
DATA_API_HOLDERS_URL = f"https://{DATA_API_HOST}/holders"
HOLDER_LIMIT = 20
WALLET_KEYS = ("proxyWallet", "wallet", "address", "proxy_wallet")
NAME_KEYS = ("name", "pseudonym", "bio", "displayName")
AMOUNT_KEYS = ("amount", "size", "shares", "quantity")
OUTCOME_KEYS = ("outcome", "outcomeIndex", "title")


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    """Flatten every holder dict out of an unverified envelope."""
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = payload.get("holders") or payload.get("data") or []
        if isinstance(candidates, dict):
            candidates = [candidates]
    else:
        return []

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("holders")
        if isinstance(nested, list):
            rows.extend(r for r in nested if isinstance(r, dict))
        else:
            rows.append(candidate)
    return rows


def build_evidence(condition_id: str, payload: Any, source_url: str) -> WhaleEvidence:
    """Turn a Data API payload into concentration evidence (never fabricated)."""
    rows = _extract_rows(payload)
    holders: list[HolderEntry] = []
    for row in rows:
        wallet = _first(row, WALLET_KEYS)
        if not wallet:
            continue
        holders.append(
            HolderEntry(
                wallet=str(wallet),
                name=(str(_first(row, NAME_KEYS)) if _first(row, NAME_KEYS) else None),
                outcome=(str(_first(row, OUTCOME_KEYS)) if _first(row, OUTCOME_KEYS) is not None else None),
                amount=_as_float(_first(row, AMOUNT_KEYS)),
            )
        )
    if not holders:
        return WhaleEvidence(
            available=False,
            source_url=source_url,
            condition_id=condition_id,
            reason="data_api_holders_unparsable",
        )

    amounts = [h.amount for h in holders if h.amount is not None]
    total = sum(amounts) if amounts else 0.0
    top5 = sorted(amounts, reverse=True)[:5]
    top5_share = round(sum(top5) / total, 6) if total > 0 else None
    for holder in holders:
        if holder.amount is not None and total > 0:
            holder.share = round(holder.amount / total, 6)

    return WhaleEvidence(
        available=True,
        source_url=source_url,
        condition_id=condition_id,
        top_holders=sorted(holders, key=lambda h: h.amount or 0.0, reverse=True)[:10],
        holder_count=len(holders),
        top5_share=top5_share,
        concentration_score=min(1.0, top5_share) if top5_share is not None else None,
        reason=None if top5_share is not None else "holder_amounts_missing",
    )


async def fetch_holder_evidence(
    client: httpx.AsyncClient,
    condition_id: str,
    tracker: RateLimitTracker,
) -> WhaleEvidence:
    """Read public holder rows for one market. Failure is reported, not raised."""
    source_url = f"{DATA_API_HOLDERS_URL}?market={condition_id}&limit={HOLDER_LIMIT}"
    if not tracker.allow(DATA_API_HOST):
        return WhaleEvidence(
            available=False,
            source_url=source_url,
            condition_id=condition_id,
            reason="data_api_budget_exhausted",
        )
    try:
        response = await client.get(DATA_API_HOLDERS_URL, params={"market": condition_id, "limit": HOLDER_LIMIT})
    except httpx.HTTPError as exc:
        logger.warning("polymarket.opportunities.holders_transport_error", extra={"error": str(exc)})
        return WhaleEvidence(
            available=False,
            source_url=source_url,
            condition_id=condition_id,
            reason="data_api_transport_error",
        )
    if response.status_code == 429:
        tracker.penalise(DATA_API_HOST, 30.0)
        return WhaleEvidence(
            available=False, source_url=source_url, condition_id=condition_id, reason="data_api_rate_limited"
        )
    if response.status_code != 200:
        return WhaleEvidence(
            available=False,
            source_url=source_url,
            condition_id=condition_id,
            reason=f"data_api_http_{response.status_code}",
        )
    try:
        return build_evidence(condition_id, response.json(), source_url)
    except ValueError:
        return WhaleEvidence(
            available=False, source_url=source_url, condition_id=condition_id, reason="data_api_bad_json"
        )
