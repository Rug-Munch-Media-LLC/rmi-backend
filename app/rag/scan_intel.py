"""Scan/shill/alert embedding pipeline — vectorize scanner + intel into Qdrant.

Makes scan results, scam alerts, and shill detections searchable via RAG:

* Scan results → ``scan_intel`` collection (created on first write, 2560-dim
  Cosine — same Qwen3-Embedding-4B space as every rmi-qdrant-v2 collection).
* Scam / security alerts → ``threat_intel_v2`` (payload ``kind=scam_alert``).
* Shill detections → ``social_intel`` (payload ``kind=shill_detection``).

Embedding uses the canonical lane in ``app.rag.embeddings`` (DeepInfra
Qwen3-Embedding-4B primary, rugbrain fallback). All writes are fire-and-forget
best-effort: callers get a scheduled task back and never block the scan/alert
path. Every failure is logged and swallowed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.rag.embeddings import EMBEDDING_DIM, get_embedding

logger = logging.getLogger(__name__)
_QDRANT_URL = (
    os.getenv("RMI_QDRANT_URL")
    or os.getenv("QDRANT_URL")
    or os.getenv("QDRANT_FALLBACK")
    or "http://rmi-qdrant-v2:6333"
).rstrip("/")

SCAN_COLLECTION = os.getenv("RMI_SCAN_INTEL_COLLECTION", "scan_intel")
ALERT_COLLECTION = os.getenv("RMI_ALERT_INTEL_COLLECTION", "threat_intel_v2")
SHILL_COLLECTION = os.getenv("RMI_SHILL_INTEL_COLLECTION", "social_intel")

_ensured: set[str] = set()


async def _ensure_collection(name: str) -> None:
    """Create the collection (2560-dim Cosine) if missing. Idempotent per process."""
    if name in _ensured:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_QDRANT_URL}/collections/{name}")
            if resp.status_code == 200:
                _ensured.add(name)
                return
            create = await client.put(
                f"{_QDRANT_URL}/collections/{name}",
                json={
                    "vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"},
                    "shard_number": 1,
                },
            )
            if create.status_code in (200, 201):
                _ensured.add(name)
                logger.info("scan_intel:collection_created name=%s dim=%d", name, EMBEDDING_DIM)
            else:
                logger.warning("scan_intel:collection_create_failed name=%s status=%d", name, create.status_code)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.debug("scan_intel:ensure_collection_failed name=%s err=%s", name, str(exc)[:120])


async def _upsert(collection: str, doc_id: str, text: str, payload: dict[str, Any]) -> bool:
    """Embed *text* and upsert one point. Returns True on success; never raises."""
    if not text.strip():
        return False
    vector = await get_embedding(text)
    if not vector:
        logger.debug("scan_intel:embed_unavailable collection=%s doc=%s", collection, doc_id)
        return False
    await _ensure_collection(collection)
    digest = hashlib.sha256(doc_id.encode()).hexdigest()
    point_id = str(uuid.UUID(int=int(digest, 16) % (2**96), version=4))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(
                f"{_QDRANT_URL}/collections/{collection}/points",
                params={"wait": True},
                json={"points": [{"id": point_id, "vector": vector, "payload": payload}]},
            )
        if resp.status_code in (200, 202):
            return True
        logger.debug("scan_intel:upsert_status=%d collection=%s body=%s", resp.status_code, collection, resp.text[:160])
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.debug("scan_intel:upsert_failed collection=%s err=%s", collection, str(exc)[:160])
    return False


def _bg(coro: Any) -> None:
    """Schedule a fire-and-forget embedding task; swallow all errors."""

    async def _runner() -> None:
        try:
            await coro
        except Exception as exc:
            logger.debug("scan_intel:bg_task_failed err=%s", str(exc)[:160])

    try:
        asyncio.get_running_loop().create_task(_runner())
    except RuntimeError:
        logger.debug("scan_intel:no_running_loop_skipped")


# ══════════════════════════════════════════════════════════════════════════
# SCAN RESULTS → scan_intel
# ══════════════════════════════════════════════════════════════════════════


def _scan_text(result: Any) -> str:
    """Human-readable scan representation for embedding."""
    wm = result.wallet_metrics if isinstance(result.wallet_metrics, dict) else {}
    bundle = result.bundle_analysis if isinstance(result.bundle_analysis, dict) else {}
    dev = result.dev_sold if isinstance(result.dev_sold, dict) else {}
    dex = result.dex_paid if isinstance(result.dex_paid, dict) else {}
    fb = result.first_buyers if isinstance(result.first_buyers, dict) else {}
    flags = ", ".join(str(f) for f in (result.risk_flags or [])[:20]) or "none"
    return (
        f"Token: {result.symbol or result.name or 'unknown'} ({result.chain})\n"
        f"Address: {result.token_address}\n"
        f"Risk: {result.risk_level} (score {round(result.safety_score)})\n"
        f"Flags: {flags}\n"
        f"Wallet: {wm.get('holders', 'n/a')} holders, {wm.get('fresh_wallet_pct', 'n/a')}% fresh wallets, "
        f"{wm.get('cex_funded_pct', 'n/a')}% CEX-funded\n"
        f"Bundle: {bundle.get('is_bundled', False)} ({round(float(bundle.get('confidence', 0) or 0))}%)\n"
        f"Dev Sold: {dev.get('dev_sold', dev.get('sold', 'unknown'))}\n"
        f"DEX Paid: {dex.get('dex_paid', dex.get('paid', 'unknown'))}\n"
        f"First Buyers: {fb.get('count', fb.get('first_buyers_count', 'n/a'))}\n"
        f"Authority: mint={result.risk_flags and any('mint' in str(f).lower() for f in result.risk_flags)}, "
        f"freeze={result.risk_flags and any('freeze' in str(f).lower() for f in result.risk_flags)}"
    )


async def embed_scan_result(result: Any) -> bool:
    """Embed one completed scan into ``scan_intel``. Best-effort, never raises."""
    try:
        text = _scan_text(result)
        payload = {
            "kind": "scan_result",
            "doc_id": f"scan:{result.chain}:{result.token_address}:{result.scanned_at}",
            "address": result.token_address,
            "chain": result.chain,
            "symbol": result.symbol or "",
            "name": result.name or "",
            "risk_score": round(100 - result.safety_score),
            "safety_score": round(result.safety_score),
            "risk_level": result.risk_level,
            "risk_flags": list(result.risk_flags or [])[:50],
            "scanned_at": result.scanned_at or datetime.now(UTC).isoformat(),
        }
        return await _upsert(SCAN_COLLECTION, payload["doc_id"], text, payload)
    except Exception as exc:
        logger.debug("scan_intel:embed_scan_failed err=%s", str(exc)[:160])
        return False


def embed_scan_result_bg(result: Any) -> None:
    """Fire-and-forget scan embedding — call from the persist path."""
    _bg(embed_scan_result(result))


# ══════════════════════════════════════════════════════════════════════════
# SCAM / SECURITY ALERTS → threat_intel_v2
# ══════════════════════════════════════════════════════════════════════════


async def embed_scam_alert(alert: dict[str, Any]) -> bool:
    """Embed one scam/security alert into ``threat_intel_v2``. Never raises."""
    try:
        token = str(alert.get("token_address") or alert.get("token") or "unknown")
        chain = str(alert.get("chain") or "unknown")
        scam_type = str(alert.get("event_type") or alert.get("category") or alert.get("type") or "scam")
        severity = str(alert.get("severity") or "medium")
        description = str(alert.get("description") or alert.get("title") or json.dumps(alert, default=str)[:400])
        text = f"Scam alert: {token} on {chain}\nType: {scam_type}\nSeverity: {severity}\nDetails: {description}"
        doc_id = f"scam_alert:{alert.get('alert_id') or alert.get('id') or token}:{severity}"
        payload = {
            "kind": "scam_alert",
            "doc_id": doc_id,
            "token": token[:80],
            "chain": chain[:40],
            "scam_type": scam_type[:60],
            "severity": severity,
            "description": description[:1000],
            "alerted_at": datetime.now(UTC).isoformat(),
        }
        return await _upsert(ALERT_COLLECTION, doc_id, text, payload)
    except Exception as exc:
        logger.debug("scan_intel:embed_alert_failed err=%s", str(exc)[:160])
        return False


def embed_scam_alert_bg(alert: dict[str, Any]) -> None:
    """Fire-and-forget scam-alert embedding."""
    _bg(embed_scam_alert(alert))


# ══════════════════════════════════════════════════════════════════════════
# SHILL DETECTIONS → social_intel
# ══════════════════════════════════════════════════════════════════════════


async def embed_shill_detection(record: dict[str, Any]) -> bool:
    """Embed one shill detection into ``social_intel``. Never raises."""
    try:
        handle = str(record.get("handle") or record.get("account") or "unknown").lstrip("@")
        token = str(record.get("token") or "unknown")
        platform = str(record.get("platform") or record.get("source") or "x")
        raw_engagement = record.get("engagement")
        engagement: dict[str, Any] = dict(raw_engagement) if isinstance(raw_engagement, dict) else {}
        likes = engagement.get("likes", record.get("likes") or "n/a")
        retweets = engagement.get("retweets", record.get("retweets") or "n/a")
        suspicion = record.get("suspicion_score") or record.get("suspicion") or "n/a"
        wallet = str(record.get("wallet") or "unknown")
        text = (
            f"Shill detected: @{handle} promoted {token} on {platform}\n"
            f"Engagement: {likes} likes, {retweets} RTs\n"
            f"Suspicion: {suspicion}\n"
            f"Wallet: {wallet}"
        )
        doc_id = f"shill_detect:{handle}:{token}:{record.get('tweet_id') or record.get('post_id') or ''}"
        payload = {
            "kind": "shill_detection",
            "doc_id": doc_id,
            "handle": handle[:60],
            "token": token[:80],
            "platform": platform[:30],
            "suspicion_score": suspicion,
            "wallet": wallet[:80],
            "detected_at": datetime.now(UTC).isoformat(),
        }
        return await _upsert(SHILL_COLLECTION, doc_id, text, payload)
    except Exception as exc:
        logger.debug("scan_intel:embed_shill_failed err=%s", str(exc)[:160])
        return False


def embed_shill_detection_bg(record: dict[str, Any]) -> None:
    """Fire-and-forget shill-detection embedding."""
    _bg(embed_shill_detection(record))


# ══════════════════════════════════════════════════════════════════════════
# SEARCH — scan_intel semantic query
# ══════════════════════════════════════════════════════════════════════════


async def search_scan_intel(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Semantic search over ``scan_intel``. Fail-open ``[]``."""
    vector = await get_embedding(query)
    if not vector:
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_QDRANT_URL}/collections/{SCAN_COLLECTION}/points/search",
                params={"limit": max(1, int(limit))},
                json={"vector": vector, "with_payload": True},
            )
        if resp.status_code != 200:
            logger.debug("scan_intel:search_http=%d", resp.status_code)
            return []
        data = resp.json() or {}
        return [
            {**(h.get("payload") or {}), "score": h.get("score", 0)}
            for h in data.get("result", [])
            if isinstance(h, dict)
        ]
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.debug("scan_intel:search_failed err=%s", str(exc)[:120])
        return []


__all__ = [
    "SCAN_COLLECTION",
    "embed_scam_alert",
    "embed_scam_alert_bg",
    "embed_scan_result",
    "embed_scan_result_bg",
    "embed_shill_detection",
    "embed_shill_detection_bg",
    "search_scan_intel",
]
