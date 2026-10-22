"""Push X posts into the news_v2_8b RAG collection via the existing ingest API.

The doc id is a uuid5 of the post's dedupe key, so the same post always maps to
the same point — a re-run re-sends the same id and Qdrant upserts instead of
duplicating. A post whose news_items row already carries rag_embedding_id is
never re-sent.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "news_fallback"))

import db  # noqa: E402  (shared connection helper)

RAG_INGEST = "http://127.0.0.1:8012/api/v1/ingest"
COLLECTION = os.environ.get("X_RAG_COLLECTION", "news_v2_8b")
BATCH = 10


def doc_id(dedupe_key: str) -> str:
    """Deterministic doc id for one post."""
    return "news-x:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"x:{dedupe_key}"))


def point_id(doc: str) -> str:
    """Qdrant point id written back to news_items.rag_embedding_id."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc))


def _document(
    post_id: str,
    text: str,
    title: str,
    handle: str,
    url: str,
    trust: float,
    klass: str,
    published: str,
) -> dict:
    """One ingest document, shaped like the fallback lane's payload."""
    return {
        "doc_id": post_id,
        "content": f"{title}\n\n{text}",
        "collection": COLLECTION,
        "source": f"x:{handle}",
        "severity": "high" if klass in ("security", "regulation") else "medium",
        "metadata": {
            "title": title,
            "url": url,
            "source": handle,
            "source_class": klass,
            "source_trust": trust,
            "published": published,
            "lane": "x_ingest",
            "source_type": "x",
        },
    }


def pending(news_ids: list[str]) -> list[str]:
    """Only the rows that still lack an embedding."""
    if not news_ids:
        return []
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT news_id FROM news_items WHERE news_id = ANY(%s) AND rag_embedding_id IS NULL",
                (news_ids,),
            )
            return [str(row[0]) for row in cur.fetchall()]


def rows(news_ids: list[str]) -> list[dict]:
    """Fetch the columns the RAG document needs."""
    if not news_ids:
        return []
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT news_id, title, body_markdown, source, url,
                          source_trust, category, published_at
                   FROM news_items WHERE news_id = ANY(%s)""",
                (news_ids,),
            )
            cols = ("news_id", "title", "body", "source", "url", "trust", "klass", "published")
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def push(news_ids: list[str], dry_run: bool = False) -> dict[str, object]:
    """Embed pending rows; returns {sent, embedded, skipped, errors}."""
    key = os.environ.get("RMI_RAG_API_KEY", "")
    todo = pending(news_ids)
    if not key:
        return {"sent": 0, "embedded": 0, "skipped": len(todo), "errors": ["no_api_key"]}
    if dry_run:
        return {"sent": 0, "embedded": 0, "skipped": len(todo), "errors": ["dry_run"]}

    docs = []
    for row in rows(todo):
        doc = doc_id(str(row["news_id"]).removeprefix("x:"))
        docs.append(
            (
                str(row["news_id"]),
                doc,
                _document(
                    doc,
                    str(row["body"]),
                    str(row["title"]),
                    str(row["source"]),
                    str(row["url"]),
                    float(row["trust"]),
                    str(row["klass"]),
                    str(row["published"]),
                ),
            )
        )

    embedded = 0
    errors: list[str] = []
    headers = {"X-API-Key": key}
    with httpx.Client(timeout=90.0) as client:
        for start in range(0, len(docs), BATCH):
            chunk = docs[start : start + BATCH]
            try:
                resp = client.post(
                    RAG_INGEST, json={"documents": [d for _, _, d in chunk]}, headers=headers
                )
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(type(exc).__name__)
                continue
            if resp.status_code >= 400:
                errors.append(f"http_{resp.status_code}:{body.get('code', '')}")
                continue
            if not body.get("ingested"):
                errors.append("ingest_zero")
                continue
            for news_id, doc, _ in chunk:
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE news_items SET rag_embedding_id=%s WHERE news_id=%s",
                        (point_id(doc), news_id),
                    )
                    conn.commit()
                embedded += 1
    return {"sent": len(docs), "embedded": embedded, "skipped": 0, "errors": errors}
