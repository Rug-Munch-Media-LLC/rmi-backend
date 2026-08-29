"""Whale transfer RAG indexing — first-class ``whale_transfers`` Qdrant collection.

Powers the bot chat and ``/whales`` "any whale moves on X?" history by
indexing every whale_move event detected by the scanner webhook engine into
a dedicated Qdrant collection, plus a bounded backfill from ClickHouse so
the collection has history at launch.

Pipeline::

    WhaleMoveProvider.poll()
        └── WebhookEngine._poll_loop
                └── index_whale_event(raw_event)     ← this module
                        ├── build compact doc string
                        ├── embed via get_embedding()  (qwen3-embedding-4b, 2560d)
                        └── upsert into whale_transfers
                                payload {token, address, chain, wallet,
                                         value_usd, side, ts, tx_hash}

Fail-open by design: Qdrant down, embedding backend down or ClickHouse
unavailable never raise into the webhook engine poll loop — the event is
dropped with a warning and the cursor still advances.

Vector size is 2560 to match every existing Qdrant collection in this repo
(verified 2026-08-29: scam_patterns, news_articles, wallet_embeddings, scam_intel_v2,
whale_transfers, and every other rmi-qdrant-v2 collection are 2560-dim Cosine)
and the primary embedder (``qwen3-embedding-4b`` →
``EMBEDDING_DIM == 2560``). Search flows through the existing Qdrant
fallback in ``app/rag/engine.py`` (``three_pillar_search`` with
``collection="whale_transfers"``), which reads ``content`` / ``metadata``
payload keys — the same convention used here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings
from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.webhooks.providers.utils import get_clickhouse
from app.rag.embeddings import get_embedding

log = get_logger(__name__)

# ── Collection configuration ───────────────────────────────────────────
COLLECTION = "whale_transfers"

# All existing Qdrant collections in this repo are 2560-dim Cosine
# (nomic embeddings are NOT used here — the primary embedder is qwen3-embedding-4b).
VECTOR_SIZE = 2560
DISTANCE = "Cosine"

# Resolved via app.config (env var wins; Docker-compose name inside the
# network; host-mapped localhost on host-side systemd runs).
_QDRANT_URL = settings.qdrant_url.rstrip("/")

# Minimum USD value matching WhaleMoveProvider.DEFAULT_MIN_VALUE — used by
# the backfill so it only pulls events that qualify as "whale" moves.
MIN_WHALE_VALUE_USD = 10_000.0

# Backfill bound — keep the launch seed small and cheap.
MAX_BACKFILL = 500

# Collection created once per process (mirrors the _QDRANT_PROBED pattern
# in app/rag/engine.py).
_COLLECTION_ENSURED = False


# ── Payload / doc construction ─────────────────────────────────────────


def _short_label(address: str) -> str:
    """Derive a compact token label from an address when no symbol exists."""
    addr = address or ""
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


def extract_whale_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a whale_move event into the whale_transfers payload shape.

    The scanner provider emits::

        {chain, block_timestamp, transaction_hash, from_address, to_address,
         token_address, value_decimal, transfer_type}

    ``value_decimal`` is the USD-equivalent value (the provider filters
    ``> DEFAULT_MIN_VALUE`` = $10k). ``wallet`` defaults to the receiving
    (accumulating) wallet; ``side`` is inferred as "sold" when the wallet is
    the sender, otherwise "bought". Explicit ``wallet`` / ``side`` /
    ``smart_money`` keys on the event win when present.
    """
    chain = str(event.get("chain") or event.get("chain_id") or "unknown")
    tx_hash = str(event.get("transaction_hash") or event.get("tx_hash") or "")
    token_address = str(event.get("token_address") or event.get("address") or "")
    from_addr = str(event.get("from_address") or "")
    to_addr = str(event.get("to_address") or "")

    wallet = str(event.get("wallet") or "").strip() or (to_addr or from_addr) or "unknown"

    side = str(event.get("side") or "").lower().strip()
    if side not in ("bought", "sold"):
        if wallet == from_addr and from_addr:
            side = "sold"
        elif wallet == to_addr and to_addr:
            side = "bought"
        else:
            side = "bought"

    value_usd = float(event.get("value_decimal") or event.get("value_usd") or 0.0)
    ts = str(event.get("block_timestamp") or event.get("ts") or "").strip()
    if not ts:
        ts = datetime.now(UTC).isoformat()

    token = str(event.get("token") or event.get("token_symbol") or "").strip() or _short_label(token_address)

    return {
        "token": token,
        "address": token_address,
        "chain": chain,
        "wallet": wallet,
        "value_usd": round(value_usd, 2),
        "side": side,
        "ts": ts,
        "tx_hash": tx_hash,
        "smart_money": bool(event.get("smart_money") or False),
    }


def build_whale_doc(payload: dict[str, Any]) -> str:
    """Build the compact semantic doc indexed into Qdrant.

    Format::

        Whale {wallet} {bought|sold} ${value} of {token} ({address}) on
        {chain} at {timestamp}. Smart-money: {flag}. Tx: {hash}
    """
    flag = "yes" if payload.get("smart_money") else "no"
    value = float(payload.get("value_usd") or 0.0)
    return (
        f"Whale {payload.get('wallet', 'unknown')} {payload.get('side', 'bought')} "
        f"${value:,.0f} of {payload.get('token', '')} ({payload.get('address', '')}) "
        f"on {payload.get('chain', 'unknown')} at {payload.get('ts', '')}. "
        f"Smart-money: {flag}. Tx: {payload.get('tx_hash', '')}"
    )


# ── Qdrant helpers ─────────────────────────────────────────────────────


def point_id(tx_hash: str) -> str:
    """Deterministic Qdrant point UUID from the tx hash.

    Re-indexing the same event (e.g. multiple matching subscriptions) is an
    idempotent upsert of the same point.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"whale:{tx_hash or 'unknown'}"))


async def ensure_collection(client: httpx.AsyncClient) -> bool:
    """Create the whale_transfers collection once per process if missing.

    Fail-open: any probe/creation failure returns False (the caller drops
    the event with a warning) — never raises into the poll loop.
    """
    global _COLLECTION_ENSURED
    if _COLLECTION_ENSURED:
        return True
    try:
        r = await client.get(f"/collections/{COLLECTION}")
        if r.status_code == 404:
            created = await client.put(
                f"/collections/{COLLECTION}",
                json={
                    "vectors": {
                        "size": VECTOR_SIZE,
                        "distance": DISTANCE,
                    }
                },
            )
            if created.status_code not in (200, 201):
                log.warning("whale_collection_create_failed status=%d body=%s", created.status_code, created.text[:200])
                return False
            log.info("whale_collection_created name=%s size=%d", COLLECTION, VECTOR_SIZE)
        elif r.status_code != 200:
            log.warning("whale_collection_probe_failed status=%d", r.status_code)
            return False
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        log.warning("whale_collection_unreachable error=%s", exc)
        return False
    _COLLECTION_ENSURED = True
    return True


# ── Live ingestion ─────────────────────────────────────────────────────


async def index_whale_event(
    event: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    vector: list[float] | None = None,
) -> dict[str, Any]:
    """Embed + upsert one whale_move event into ``whale_transfers``.

    Fail-open: any failure (Qdrant down, embedder down, malformed event)
    logs a warning and returns ``{"indexed": False}`` — never raises.

    ``client`` and ``vector`` are test seams; production callers omit them.
    """
    try:
        payload = extract_whale_payload(event)
        doc = build_whale_doc(payload)
        if vector is None:
            vector = await get_embedding(doc)
        if len(vector) != VECTOR_SIZE:
            log.warning(
                "whale_index_dim_mismatch got=%d want=%d",
                len(vector),
                VECTOR_SIZE,
            )
            return {"indexed": False, "error": f"vector dim {len(vector)} != {VECTOR_SIZE}"}

        pid = point_id(payload["tx_hash"])
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(base_url=_QDRANT_URL, timeout=10.0)
        try:
            if not await ensure_collection(client):
                return {"indexed": False, "error": "collection unavailable"}
            body = {
                "points": [
                    {
                        "id": pid,
                        "vector": vector,
                        "payload": {
                            "content": doc,
                            "metadata": payload,
                            "source": "whale_move",
                            "chain": payload["chain"],
                            "collection": COLLECTION,
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{COLLECTION}/points", json=body)
            if r.status_code not in (200, 201):
                log.warning(
                    "whale_index_upsert_failed status=%d body=%s",
                    r.status_code,
                    r.text[:200],
                )
                return {"indexed": False, "error": f"qdrant status {r.status_code}"}
        finally:
            if owns_client:
                await client.aclose()
        return {"indexed": True, "collection": COLLECTION, "point_id": pid, "doc": doc}
    except Exception as exc:  # fail-open contract (see module docstring)
        log.warning("whale_index_failed error=%s", exc)
        return {"indexed": False, "error": str(exc)}


# ── Bounded backfill from ClickHouse ───────────────────────────────────


async def backfill_whale_transfers(limit: int = MAX_BACKFILL) -> dict[str, Any]:
    """Seed ``whale_transfers`` from ClickHouse ``wallet_memory.token_transfers``.

    Bounded to ``MAX_BACKFILL`` (500) events: pulls the most recent whale
    moves (USD value above the $10k threshold) and indexes each one.
    Fail-open: ClickHouse unavailable → ``{"backfilled": 0}``.
    """
    if limit < 0:
        limit = 0
    limit = min(int(limit), MAX_BACKFILL)
    if limit == 0:
        return {"backfilled": 0, "limit": 0, "errors": 0}

    ch = get_clickhouse()
    if ch is None:
        log.warning("whale_backfill_clickhouse_unavailable")
        return {"backfilled": 0, "limit": limit, "errors": 0}

    # ``limit`` is clamped to a bounded int above; binding it as a param
    # keeps the query fully parameterized (no string interpolation).
    rows = ch.execute(
        """
        SELECT
            chain_id,
            block_timestamp,
            transaction_hash,
            from_address,
            to_address,
            token_address,
            value_decimal,
            transfer_type
        FROM wallet_memory.token_transfers
        WHERE value_decimal > %(min_val)s
        ORDER BY block_timestamp DESC
        LIMIT %(limit)s
        """,
        {"min_val": MIN_WHALE_VALUE_USD, "limit": limit},
    )

    backfilled = 0
    errors = 0
    for row in rows:
        try:
            result = await index_whale_event(
                {
                    "chain": row[0],
                    "block_timestamp": str(row[1]) if row[1] else "",
                    "transaction_hash": row[2],
                    "from_address": row[3],
                    "to_address": row[4],
                    "token_address": row[5],
                    "value_decimal": float(row[6]) if row[6] else 0.0,
                    "transfer_type": row[7],
                }
            )
            if result.get("indexed"):
                backfilled += 1
            else:
                errors += 1
        except Exception as exc:  # one bad row must not kill the batch
            log.warning("whale_backfill_row_failed error=%s", exc)
            errors += 1

    log.info(
        "whale_backfill_done limit=%d backfilled=%d errors=%d",
        limit,
        backfilled,
        errors,
    )
    return {"backfilled": backfilled, "limit": limit, "errors": errors}


__all__ = [
    "COLLECTION",
    "DISTANCE",
    "MAX_BACKFILL",
    "MIN_WHALE_VALUE_USD",
    "VECTOR_SIZE",
    "backfill_whale_transfers",
    "build_whale_doc",
    "ensure_collection",
    "extract_whale_payload",
    "index_whale_event",
    "point_id",
]
