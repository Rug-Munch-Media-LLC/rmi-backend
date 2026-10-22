"""Run the Telegram sweep's JSONL into ``news_items`` and the ``news_v2_8b`` lane.

One wiring point, idempotent by construction: the insert is ``ON CONFLICT DO
NOTHING`` on ``tg:<channel>:<message_id>`` and an existing post is *adopted*
(weights filled, no duplicate row); only rows whose ``rag_embedding_id IS NULL``
are sent, so a second run adds zero rows and zero documents.

Run: ``python -m app.databus.tg_news.persist`` (NF_PG_* + ``RMI_RAG_API_KEY``).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger
from app.databus.tg_news import bridge
from app.databus.tg_news.state import news_dir
from app.databus.tg_news.verified_channels import IMPOSTOR

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger("tg_news.persist")

RAG_INGEST = os.getenv("TG_RAG_INGEST", "http://127.0.0.1:8012/api/v1/ingest")
BATCH = 10

_INSERT = """INSERT INTO news_items (news_id, url, title, summary, body_markdown, source,
  published_at, ingested_at, source_tier, source_trust, source_weight, content_hash,
  body_source, truncated, category, source_type)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
RETURNING news_id"""


def _connect() -> Any:
    import psycopg  # local import: module stays importable without a driver

    return psycopg.connect(
        host=os.getenv("NF_PG_HOST", "127.0.0.1"),
        port=int(os.getenv("NF_PG_PORT", "5432")),
        dbname=os.getenv("NF_PG_DB", "rmi"),
        user=os.getenv("NF_PG_USER", "rmi"),
        password=os.getenv("NF_PG_PASSWORD", ""),
    )


def _posts(path: Any) -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


_ADOPT = """UPDATE news_items SET source_type = 'telegram', source_trust = %s,
  source_weight = %s, body_source = COALESCE(NULLIF(body_source, ''), %s)
 WHERE content_hash = %s AND (source_type IS NULL OR source_weight IS NULL)"""


def _insert(rows: Sequence[tuple[Any, ...]]) -> tuple[list[str], int]:
    """Insert rows, adopting a pre-existing row for the same post.

    ``news_id`` is ``tg:<channel>:<message_id>`` and ``content_hash`` is unique,
    so a post written by an earlier lane is *adopted* (weights filled in) rather
    than duplicated. Returns (news_ids in the batch, adopted count).
    """
    adopted = new = 0
    with _connect() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(_INSERT, row)
                if cur.fetchone() is not None:
                    new += 1
                else:
                    cur.execute(_ADOPT, (row[9], row[10], row[12], row[11]))
                    adopted += cur.rowcount
        conn.commit()
    return [str(row[0]) for row in rows], new, adopted


def _pending(news_ids: Sequence[str]) -> list[dict[str, Any]]:
    """RAG payloads for rows of this batch that still lack an embedding."""
    if not news_ids:
        return []
    docs = []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT news_id, url, title, body_markdown, source, source_trust, published_at
                   FROM news_items WHERE news_id = ANY(%s) AND rag_embedding_id IS NULL""",
            (list(news_ids),),
        )
        for news_id, url, title, body, source, trust, published in cur.fetchall():
            docs.append(
                bridge.document(
                    news_id,
                    url,
                    title,
                    body or "",
                    str(source).replace("Telegram @", ""),
                    float(trust),
                    published.isoformat(),
                )
            )
    return docs


def _send(docs: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Send in batches; returns (documents confirmed ingested, error strings)."""
    if not docs:
        return [], []
    if not key:
        return [], ["RMI_RAG_API_KEY unset"]
    sent, errors = [], []
    for index in range(0, len(docs), BATCH):
        chunk = docs[index : index + BATCH]
        try:
            resp = httpx.post(RAG_INGEST, json={"documents": chunk}, headers={"X-API-Key": key}, timeout=120.0)
            if int(resp.json().get("ingested") or 0):
                sent.extend(chunk)
            else:
                errors.append(f"status={resp.status_code} ingested=0 {resp.text[:120]}")
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:160])
    return sent, errors[:3]


def _mark_embedded(docs: list[dict[str, Any]]) -> int:
    """Store uuid5(doc_id) on the row so a re-run never re-sends it."""
    if not docs:
        return 0
    marked = 0
    with _connect() as conn:
        with conn.cursor() as cur:
            for doc in docs:
                cur.execute(
                    "UPDATE news_items SET rag_embedding_id = %s WHERE url = %s AND rag_embedding_id IS NULL",
                    (str(uuid.uuid5(uuid.NAMESPACE_DNS, doc["doc_id"])), doc["metadata"]["url"]),
                )
                marked += cur.rowcount
        conn.commit()
    return marked


def run(root: str | None = None, *, limit: int = 4000) -> dict[str, Any]:
    """Bridge every swept channel's JSONL into ``news_items`` + ``news_v2_8b``."""
    directory = news_dir(root)
    now = datetime.now(UTC)
    key = os.getenv("RMI_RAG_API_KEY", "")
    inserted, adopted, per_channel, news_ids = 0, 0, {}, []
    for path in sorted(directory.glob("*.jsonl")) if directory.exists() else []:
        handle = path.stem
        if handle.lower() in IMPOSTOR:
            logger.info("persist skips impostor channel %s", handle)
            continue
        rows = bridge.rows(_posts(path)[-limit:], handle, now)
        if not rows:
            continue
        fresh, added, filled = _insert(rows)
        inserted += added
        adopted += filled
        news_ids.extend(fresh)
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM news_items WHERE news_id = ANY(%s)", (fresh,))
            per_channel[handle] = int(cur.fetchone()[0])
    docs = _pending(news_ids)
    sent, errors = _send(docs, key)
    return {
        "ran_at": now.isoformat(),
        "channels": len(per_channel),
        "rows_new": inserted,
        "rows_adopted": adopted,
        "per_channel_total": per_channel,
        "rag_candidates": len(docs),
        "rag_ingested": len(sent),
        "rag_embedded_marked": _mark_embedded(sent),
        "rag_errors": errors,
        "rag_key_present": bool(key),
    }


def main() -> int:
    print(json.dumps(run(), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
