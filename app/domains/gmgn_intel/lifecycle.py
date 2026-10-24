"""Lifecycle staging: new -> bonding -> almost_graduated -> graduated.

GMGN ships three trenches buckets plus a rank feed. We re-derive the stage
from facts (bonding ``progress``, completion timestamp, launchpad status) so
the stage survives a bucket reshuffle on GMGN's side. ``trending`` is a
*surface*, not a stage: a token keeps its stage and additionally reports
``trending: true`` when it appears in our rank pull.
"""

from __future__ import annotations

from typing import Any

from app.domains.gmgn_intel.coerce import canonical_chain, gap, num, whole
from app.domains.gmgn_intel.normalize import normalize_row
from app.domains.gmgn_intel.scoring import score_token

STAGES = ("new", "bonding", "almost_graduated", "graduated", "trending")
NEW_MAX_PROGRESS = 0.10
ALMOST_GRADUATED_MIN_PROGRESS = 0.90
BUCKET_ENDPOINT = "POST /v1/trenches"


def classify_stage(row: dict[str, Any], bucket: str) -> dict[str, str]:
    """Derive our stage for one raw row, with the reason we chose it."""
    status = whole(row.get("launchpad_status")) or 0
    completed_at = num(row.get("complete_timestamp")) or 0
    progress = num(row.get("progress"))
    if bucket == "completed" or completed_at > 0 or status >= 1:
        return {"stage": "graduated", "reason": "completion timestamp or launchpad status set"}
    if bucket == "near_completion" or (progress is not None and progress >= ALMOST_GRADUATED_MIN_PROGRESS):
        return {"stage": "almost_graduated", "reason": "bonding progress at or above 90%"}
    if progress is not None and progress > NEW_MAX_PROGRESS:
        return {"stage": "bonding", "reason": "bonding progress above 10%"}
    return {"stage": "new", "reason": "fresh creation below 10% of the curve"}


def _trending_index(rank_rows: list[dict[str, Any]] | None) -> dict[str, int]:
    """Address -> 1-based position in *our* rank pull (GMGN order kept only as input)."""
    index: dict[str, int] = {}
    for position, row in enumerate(rank_rows or [], start=1):
        address = row.get("address")
        if isinstance(address, str) and address:
            index.setdefault(address, position)
    return index


def _rank_stage_order(stage: str) -> int:
    order = {"new": 0, "bonding": 1, "almost_graduated": 2, "graduated": 3}
    return order.get(stage, -1)


def build_lifecycle(
    buckets: dict[str, list[dict[str, Any]]],
    rank_rows: list[dict[str, Any]] | None,
    chain: str,
    observed_at: str,
) -> dict[str, Any]:
    """Normalize every trenches bucket, merge duplicates, score, count stages."""
    trending = _trending_index(rank_rows)
    merged: dict[str, dict[str, Any]] = {}
    for bucket in ("new_creation", "near_completion", "completed"):
        for row in buckets.get(bucket) or []:
            decision = classify_stage(row, bucket)
            token = normalize_row(row, decision["stage"], observed_at)
            address = token.get("address")
            if not address:
                continue
            token["evidence"].append({"source": BUCKET_ENDPOINT, "bucket": bucket, "stage_reason": decision["reason"]})
            previous = merged.get(address)
            if previous and _rank_stage_order(previous["stage"]) >= _rank_stage_order(token["stage"]):
                continue
            merged[address] = token

    tokens: list[dict[str, Any]] = []
    counts = dict.fromkeys(STAGES, 0)
    for token in merged.values():
        position = trending.get(token["address"] or "")
        token["trending"] = position is not None
        token["trending_rank"] = position
        scored = score_token(token)
        token["score"] = scored["score"]
        token["score_grade"] = scored["grade"]
        token["score_breakdown"] = scored
        if scored["blocked"]:
            token["missing"].append(gap("score", "blocked: honeypot or unsellable"))
        tokens.append(token)
        counts[token["stage"]] = counts.get(token["stage"], 0) + 1
        if token["trending"]:
            counts["trending"] += 1

    tokens.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
    return {
        "chain": canonical_chain(chain),
        "observed_at": observed_at,
        "sources": [BUCKET_ENDPOINT, "GET /v1/market/rank"],
        "counts": counts,
        "token_count": len(tokens),
        "tokens": tokens,
    }
