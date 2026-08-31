#!/usr/bin/env python3
"""Populate the Qdrant ``social_sentiment`` collection from bot KOL data.

Cheap, non-Pro path: derives per-KOL and per-call sentiment docs from the
bot's ``bot_users.db`` (kol_scores / kol_calls / kol_identities) via
``app.databus.kol_bot_store.derive_kol_sentiment_docs``, embeds them with
the local Qwen3-4B embedder and upserts into Qdrant. Idempotent — prior
``kind in (kol_sentiment, call_sentiment)`` points are deleted first.

Usage:
    python scripts/populate_social_sentiment.py [--dry-run] [--limit 2000]

Env:
    QDRANT_URL   (default http://localhost:6334)
    EMBED_URL    (default http://localhost:8004/v1/embeddings)
    EMBED_MODEL  (default qwen3-embedding-4b)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6334").rstrip("/")
EMBED_URL = os.getenv("EMBED_URL", "http://localhost:8004/v1/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding-4b")
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "rmi-social-sentiment")
REPLACE_KINDS = ["kol_sentiment", "call_sentiment"]


def _post(url: str, payload: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def embed_texts(texts: list[str], batch: int = 16) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        data = _post(EMBED_URL, {"input": chunk, "model": EMBED_MODEL})["data"]
        vectors.extend(d["embedding"] for d in sorted(data, key=lambda d: d["index"]))
        print(f"  embedded {min(i + batch, len(texts))}/{len(texts)}", flush=True)
    return vectors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=2000)
    args = ap.parse_args()

    from app.databus.kol_bot_store import derive_kol_sentiment_docs

    docs = derive_kol_sentiment_docs(limit=args.limit)
    print(f"derived {len(docs)} sentiment docs from bot_users.db")
    if not docs:
        print("nothing to do (bot DB unreachable or empty)")
        return 1
    if args.dry_run:
        print(json.dumps(docs[0], indent=2)[:800])
        return 0

    # Delete prior derived points so re-runs never duplicate.
    del_result = _post(
        f"{QDRANT_URL}/collections/social_sentiment/points/delete",
        {"filter": {"must": [{"key": "kind", "match": {"any": REPLACE_KINDS}}]}},
    )
    print(f"deleted prior derived points: {del_result.get('status')}")

    vectors = embed_texts([d["text"] for d in docs])
    points = [
        {
            "id": str(uuid.uuid5(NAMESPACE, d["id"])),
            "vector": vec,
            "payload": {**d["metadata"], "doc_id": d["id"], "text": d["text"][:1200]},
        }
        for d, vec in zip(docs, vectors)
    ]
    upserted = 0
    for i in range(0, len(points), 100):
        chunk = points[i : i + 100]
        res = _post(
            f"{QDRANT_URL}/collections/social_sentiment/points?wait=true",
            {"points": chunk},
        )
        if res.get("status") == "ok":
            upserted += len(chunk)
        print(f"  upserted {upserted}/{len(points)}", flush=True)

    count = _post(f"{QDRANT_URL}/collections/social_sentiment/points/count", {"exact": True})
    print(f"DONE: {upserted} upserted, collection now has {count['result']['count']} points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
