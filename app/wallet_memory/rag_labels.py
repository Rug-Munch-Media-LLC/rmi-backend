"""Wallet label RAG bridge — embeds wallet labels into Qdrant for semantic search.

Wires the wallet label system (``app/wallet_memory``) to the RAG system:

- ``embed_wallet_label``      — upsert a label into the Qdrant ``wallet_labels``
                                collection (rugbrain ingest lane, with a
                                direct-Qdrant fallback mirroring
                                ``rugbrain_client``'s search fallback).
- ``search_similar_labels``   — semantic search over embedded labels.
- ``auto_expand_labels``      — find similar wallets via behavior-profile
                                embeddings and suggest labels with confidence.
- ``run_label_sync``          — scheduled job body: embed labels added in the
                                last window, then auto-expand each one.
- ``label_stats``             — counters for the admin stats endpoint.

Fail-open everywhere: RAG/Qdrant unavailability never raises to callers.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.rugbrain_client import (
    _deepinfra_embed,
    _qdrant_base,
    rugbrain_ingest,
    rugbrain_search,
)

log = get_logger(__name__)

WALLET_LABELS_COLLECTION = "wallet_labels"
_EMBED_DIM = 2560  # DeepInfra Qwen3-Embedding-4B, matches the Qdrant corpus
_SUGGESTIONS_KEY = "wm:rag:suggestions"  # Redis set of pending auto-suggestions


# ── Text building ────────────────────────────────────────────────────────


def _label_text(label: dict[str, Any]) -> str:
    """Build the embeddable text for a wallet label (address + labels + entity + risk)."""
    parts = [
        f"address={label.get('address') or label.get('wallet_address') or ''}",
        f"chain={label.get('chain') or label.get('chain_id') or ''}",
        f"label={label.get('label') or label.get('entity_label') or ''}",
        f"category={label.get('category') or label.get('entity_category') or ''}",
        f"source={label.get('source', '')}",
        f"risk={label.get('risk_level') or ''} score={label.get('risk_score', '')}",
    ]
    return " ".join(p for p in parts if not p.endswith("="))


def _label_doc_id(label: dict[str, Any]) -> str:
    """Stable doc id so re-embedding the same wallet label upserts in place."""
    addr = str(label.get("address") or label.get("wallet_address") or "").lower()
    chain = str(label.get("chain") or label.get("chain_id") or "")
    raw = f"{chain}:{addr}" if addr else json.dumps(label, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── Qdrant direct write lane (fallback when rugbrain HTTP is unreachable) ─


async def _ensure_collection() -> bool:
    """Create the ``wallet_labels`` collection if missing. False on failure."""
    base = _qdrant_base()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{base}/collections/{WALLET_LABELS_COLLECTION}")
        if r.status_code == 200:
            return True
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(
                f"{base}/collections/{WALLET_LABELS_COLLECTION}",
                json={
                    "vectors": {"size": _EMBED_DIM, "distance": "Cosine"},
                },
            )
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning("wallet_labels_ensure_collection_failed err=%s", e)
        return False


async def _qdrant_direct_upsert(
    doc_id: str,
    content: str,
    metadata: dict[str, Any],
) -> bool:
    """Upsert one point into ``wallet_labels`` with a locally-computed vector."""
    vector = await _deepinfra_embed(content)
    if not vector:
        return False
    if not await _ensure_collection():
        return False
    base = _qdrant_base()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(
                f"{base}/collections/{WALLET_LABELS_COLLECTION}/points?wait=true",
                json={
                    "points": [
                        {
                            "id": doc_id,
                            "vector": vector,
                            "payload": {
                                "content": content,
                                "doc_id": doc_id,
                                "collection": WALLET_LABELS_COLLECTION,
                                "metadata": metadata,
                            },
                        }
                    ]
                },
            )
        return r.status_code == 200
    except Exception as e:
        log.warning("wallet_labels_direct_upsert_failed err=%s", e)
        return False


# ── Public API ───────────────────────────────────────────────────────────


async def embed_wallet_label(label: dict[str, Any]) -> bool:
    """Embed a wallet label in Qdrant ``wallet_labels`` collection for semantic search.

    Lane 1: rugbrain ``POST /api/v1/ingest`` (embeds server-side).
    Lane 2 (container fallback): direct Qdrant upsert with a DeepInfra
    Qwen3-Embedding-4B vector — same pattern as ``rugbrain_search``.
    """
    content = _label_text(label)
    if not content.strip(" ="):
        return False
    doc_id = _label_doc_id(label)
    metadata = {
        "address": label.get("address") or label.get("wallet_address") or "",
        "chain": label.get("chain") or label.get("chain_id") or "",
        "label": label.get("label") or label.get("entity_label") or "",
        "category": label.get("category") or label.get("entity_category") or "",
        "source": label.get("source", ""),
        "confidence": label.get("confidence", 0.0),
        "risk_score": label.get("risk_score", 0.0),
        "risk_level": label.get("risk_level", ""),
        "embedded_at": int(time.time()),
    }
    ok = await rugbrain_ingest(
        content=content,
        collection=WALLET_LABELS_COLLECTION,
        doc_id=doc_id,
        metadata=metadata,
        source="wallet_memory",
        severity="info",
        chain=str(metadata["chain"]),
    )
    if ok:
        return True
    return await _qdrant_direct_upsert(doc_id, content, metadata)


async def search_similar_labels(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Semantic search for wallet labels matching a query.

    Returns hits in the rugbrain-compatible shape (``content`` / ``score`` /
    ``metadata``), each with a ``similarity`` score. ``[]`` on failure.
    """
    if not query:
        return []
    return await rugbrain_search(query, collections=[WALLET_LABELS_COLLECTION], limit=limit)


async def auto_expand_labels(address: str, chain: str) -> list[dict[str, Any]]:
    """Use RAG to find similar wallets and suggest labels.

    When a wallet is labeled (e.g. ``exchange_hot``), embed its behavior
    profile (funding pattern, tx frequency, counterparties, risk) and search
    Qdrant for similar behavior profiles. Returns suggested labels with
    confidence scores for wallets that do not already carry a label.
    """
    from app.wallet_memory.engine import get_wallet_engine

    addr = address.lower().strip()
    try:
        engine = get_wallet_engine()
        profile = await engine.get_wallet_profile(addr, chain)
    except Exception as e:
        log.warning("auto_expand_profile_failed addr=%s err=%s", addr, e)
        return []
    if not profile:
        return []

    # Behavior profile text: what this wallet DOES, not just what it is named.
    behavior = " ".join(
        str(part)
        for part in [
            f"wallet behavior profile chain={chain}",
            f"category={profile.get('entity_category') or profile.get('category') or ''}",
            f"risk_level={profile.get('risk_level', '')}",
            f"transactions={profile.get('total_transactions', 0)}",
            f"volume={profile.get('total_volume', 0)}",
            f"counterparties={profile.get('unique_counterparties', 0)}",
            f"labels={','.join(str(lb.get('label', '')) for lb in (profile.get('labels') or []) if isinstance(lb, dict))}",
        ]
        if str(part).strip() and "=" not in str(part)[-1:]
    )
    if not behavior.strip():
        return []

    hits = await search_similar_labels(behavior, limit=10)
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        meta = hit.get("metadata") or {}
        hit_addr = str(meta.get("address") or "").lower()
        label = str(meta.get("label") or "")
        if not label or hit_addr == addr or label in seen:
            continue
        seen.add(label)
        similarity = float(hit.get("similarity") or hit.get("score") or 0.0)
        suggestions.append(
            {
                "address": addr,
                "chain": chain,
                "suggested_label": label,
                "suggested_category": meta.get("category", ""),
                "confidence": round(similarity, 4),
                "based_on": hit_addr,
                "pending_review": True,
            }
        )
    if suggestions:
        await _record_suggestions(suggestions)
    return suggestions


async def _record_suggestions(suggestions: list[dict[str, Any]]) -> None:
    """Persist auto-suggestions to Redis for the pending-review queue (fail-open).

    Stored as a JSON list (read-modify-write, capped) so ``label_stats`` can
    count pending items with the storage layer's string-only ``redis_get``.
    """
    try:
        from app.wallet_memory.storage import get_storage

        storage = get_storage()
        raw = await storage.redis_get(_SUGGESTIONS_KEY)
        existing: list[dict[str, Any]] = json.loads(raw) if raw else []
        if not isinstance(existing, list):
            existing = []
        known = {json.dumps(s, sort_keys=True) for s in existing}
        for s in suggestions:
            key = json.dumps(s, sort_keys=True)
            if key not in known:
                existing.append(s)
                known.add(key)
        await storage.redis_set(_SUGGESTIONS_KEY, json.dumps(existing[-1000:]), ttl=30 * 86400)
    except Exception as e:
        log.debug("suggestion_record_failed err=%s", e)


# ── Scheduled sync (lifespan, every 6h) ──────────────────────────────────


async def _recent_labeled_wallets(hours: int = 6, limit: int = 500) -> list[dict[str, Any]]:
    """Fetch wallets labeled/updated in ClickHouse within the last ``hours``."""
    from app.wallet_memory.storage import get_storage

    storage = get_storage()
    await storage._ensure_connections()
    ch = getattr(storage, "_ch", None)
    if ch is None:
        return []
    try:
        rows = ch.execute(
            """
            SELECT wallet_address, chain_id, entity_label, entity_category,
                   risk_score, risk_level, updated_at
            FROM wallet_profiles
            WHERE updated_at > now() - INTERVAL %(hours)s HOUR
              AND entity_label != ''
            ORDER BY updated_at DESC
            LIMIT %(limit)s
            """,
            {"hours": hours, "limit": limit},
        )
    except Exception as e:
        log.warning("recent_labeled_wallets_query_failed err=%s", e)
        return []
    out: list[dict[str, Any]] = []
    for r in rows or []:
        out.append(
            {
                "address": r[0],
                "chain": r[1],
                "label": r[2],
                "category": r[3],
                "risk_score": float(r[4] or 0.0),
                "risk_level": r[5],
                "updated_at": str(r[6]) if r[6] else "",
            }
        )
    return out


async def run_label_sync(hours: int | None = None) -> dict[str, Any]:
    """Embed newly labeled wallets into Qdrant, then auto-expand each one.

    Scheduled every 6 hours from ``app/lifespan.py``. Never raises.
    """
    window = hours if hours is not None else max(1, int(os.getenv("RMI_WALLET_LABEL_SYNC_HOURS", "6")))
    wallets = await _recent_labeled_wallets(hours=window)
    embedded = 0
    suggestions = 0
    for w in wallets:
        try:
            if await embed_wallet_label(w):
                embedded += 1
            found = await auto_expand_labels(w["address"], w["chain"])
            suggestions += len(found)
        except Exception as e:
            log.warning("label_sync_item_failed addr=%s err=%s", w.get("address"), e)
    log.info(
        "wallet_label_sync_complete fetched=%d embedded=%d suggestions=%d",
        len(wallets),
        embedded,
        suggestions,
    )
    return {"fetched": len(wallets), "embedded": embedded, "suggestions": suggestions}


# ── Stats (admin endpoint) ───────────────────────────────────────────────


async def _qdrant_collection_count() -> int:
    """Total points in the ``wallet_labels`` collection (0 on failure)."""
    base = _qdrant_base()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{base}/collections/{WALLET_LABELS_COLLECTION}")
        if r.status_code != 200:
            return 0
        result = r.json().get("result", {})
        return int(result.get("points_count") or 0)
    except Exception as e:
        log.warning("wallet_labels_count_failed err=%s", e)
        return 0


async def label_stats() -> dict[str, Any]:
    """Counters for GET /api/v1/admin/wallet-labels/stats. Never raises."""
    total = await _qdrant_collection_count()
    last_24h = await _recent_labeled_wallets(hours=24, limit=5000)
    pending: list[dict[str, Any]] = []
    try:
        from app.wallet_memory.storage import get_storage

        storage = get_storage()
        raw = await storage.redis_get(_SUGGESTIONS_KEY)
        loaded = json.loads(raw) if raw else []
        if isinstance(loaded, list):
            pending = loaded
    except Exception as e:
        log.debug("suggestion_count_failed err=%s", e)
    return {
        "collection": WALLET_LABELS_COLLECTION,
        "total_labels_in_qdrant": total,
        "labels_added_last_24h": len(last_24h),
        "auto_suggested_pending_review": len(pending),
    }
