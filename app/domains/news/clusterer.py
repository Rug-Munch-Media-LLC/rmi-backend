"""T03 - News story clustering (G04 FIX).

Per MINIMAX_M3_TASKS.md T03. MinHash + DBSCAN dedupes raw RSS items into
single "stories" so AI agents don't see the same CoinDesk/The Block story
counted 2-3x in their signal.

Algorithm:
  1. MinHash signature (128 permutations) on shingled title+body (first 500 chars)
  2. DBSCAN clusters within 30-minute windows, Jaccard threshold 0.6, eps=0.15
  3. Each cluster = one story with all source URLs, sentiment avg, item count
  4. Persist clusters to Postgres `news_clusters` table; raw items unchanged

Endpoints:
  GET /api/v1/news?clustered=true    returns stories (clusters), not raw items
  GET /api/v1/news                   raw items (legacy)

This module is pure logic - no I/O at import time. Router/background job
call `cluster_items(items) -> list[StoryCluster]`.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Tokenization ────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)

def _shingles(text: str, k: int = 3) -> set[str]:
    """k-shingle set of lowercased alphanumeric tokens. For Jaccard."""
    if not text:
        return set()
    toks = _TOKEN_RE.findall(text.lower())
    if len(toks) < k:
        return set(toks)
    return {" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1)}

# ── MinHash ─────────────────────────────────────────────────────────
_NUM_PERM = 128
_MAX_HASH = (1 << 32) - 1

def _minhash_signature(shingles: set[str], seed: int = 42) -> list[int]:
    """128-permutation MinHash signature of a shingle set.

    Uses SHA-256 seeded permutations - fast, deterministic, no numpy.
    """
    if not shingles:
        return [_MAX_HASH] * _NUM_PERM
    sig: list[int] = []
    for i in range(_NUM_PERM):
        m = _MAX_HASH
        for s in shingles:
            h = int.from_bytes(
                hashlib.sha256(f"{i}:{seed}:{s}".encode()).digest()[:4],
                "big",
            )
            if h < m:
                m = h
        sig.append(m)
    return sig

def _jaccard_minhash(a: list[int], b: list[int]) -> float:
    """Estimate Jaccard similarity from two MinHash signatures."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=False) if x == y) / len(a)

# ── DBSCAN (pure-python, no sklearn dep) ────────────────────────────
def _dbscan(
    signatures: list[list[int]],
    eps: float = 0.4,
    min_samples: int = 2,
) -> list[int]:
    """Density-based clustering. Returns cluster id per item (-1 = noise).

    Similarity is Jaccard (estimated via MinHash). Neighbours are pairs
    with Jaccard distance <= eps (i.e. similarity >= 1 - eps).
    Default eps=0.4 means similarity >= 0.6 (per T03 spec).
    """
    n = len(signatures)
    labels = [-1] * n
    cluster_id = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        neighbors = [
            j
            for j in range(n)
            if i != j and (1.0 - _jaccard_minhash(signatures[i], signatures[j])) <= eps
        ]
        if len(neighbors) < min_samples - 1:
            # not enough neighbours - mark as noise (may become border later)
            continue
        labels[i] = cluster_id
        seed_set = list(neighbors)
        k = 0
        while k < len(seed_set):
            q = seed_set[k]
            if labels[q] == -1:
                labels[q] = cluster_id
                q_neighbors = [
                    j
                    for j in range(n)
                    if j != q and (1.0 - _jaccard_minhash(signatures[q], signatures[j])) <= eps
                ]
                if len(q_neighbors) >= min_samples - 1:
                    seed_set.extend(q_neighbors)
            elif labels[q] is None or labels[q] == -1:
                labels[q] = cluster_id
            k += 1
        cluster_id += 1
    return labels

# ── Domain types ────────────────────────────────────────────────────
@dataclass
class NewsItem:
    """Minimal news item for clustering. Adapts from DB rows or dicts."""

    id: str
    title: str
    body: str = ""
    source: str = ""
    url: str = ""
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sentiment: float = 0.0

    @classmethod
    def from_row(cls, row: dict) -> NewsItem:
        published = row.get("published_at") or row.get("created_at")
        if isinstance(published, str):
            try:
                published = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                published = datetime.now(UTC)
        elif not isinstance(published, datetime):
            published = datetime.now(UTC)
        return cls(
            id=str(row.get("id", row.get("news_id", ""))),
            title=row.get("title", "") or "",
            body=(row.get("body") or row.get("summary") or "")[:500],
            source=row.get("source", "") or "",
            url=row.get("url", "") or "",
            published_at=published,
            sentiment=float(row.get("sentiment", 0.0) or 0.0),
        )

@dataclass
class StoryCluster:
    """One deduplicated story spanning 1+ source items."""

    cluster_id: str
    representative_title: str
    source_urls: list[str]
    sources: list[str]
    first_seen: datetime
    last_updated: datetime
    item_count: int
    sentiment_avg: float
    item_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "representative_title": self.representative_title,
            "source_urls": self.source_urls,
            "sources": self.sources,
            "first_seen": self.first_seen.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "item_count": self.item_count,
            "sentiment_avg": round(self.sentiment_avg, 3),
            "item_ids": self.item_ids,
        }

# ── Main entry point ────────────────────────────────────────────────
def cluster_items(
    items: list[NewsItem],
    window_minutes: int = 30,
    eps: float = 0.4,
    min_samples: int = 2,
) -> list[StoryCluster]:
    """Cluster news items into stories.

    Items are first grouped by 30-minute time windows, then DBSCAN runs
    on MinHash signatures within each window. Single-item clusters are
    kept (they're "noise" in DBSCAN terms but valid singleton stories).

    `eps` is the Jaccard DISTANCE threshold (1 - similarity). Per the
    task spec, two items cluster together when Jaccard similarity >= 0.6,
    so distance <= 0.4, so eps=0.4. Tighten for stricter clusters.
    """
    t0 = time.time()
    if not items:
        return []

    # Group by time window
    windows: dict[datetime, list[NewsItem]] = {}
    for it in sorted(items, key=lambda x: x.published_at):
        bucket = it.published_at.replace(
            minute=(it.published_at.minute // window_minutes) * window_minutes,
            second=0,
            microsecond=0,
        )
        windows.setdefault(bucket, []).append(it)

    stories: list[StoryCluster] = []
    for _bucket, group in windows.items():
        if len(group) == 1:
            # singleton - still a story
            it = group[0]
            stories.append(
                StoryCluster(
                    cluster_id=hashlib.sha1(
                        f"single:{it.id}:{it.published_at.isoformat()}".encode(),
                        usedforsecurity=False,
                    ).hexdigest()[:16],
                    representative_title=it.title,
                    source_urls=[it.url] if it.url else [],
                    sources=[it.source] if it.source else [],
                    first_seen=it.published_at,
                    last_updated=it.published_at,
                    item_count=1,
                    sentiment_avg=it.sentiment,
                    item_ids=[it.id],
                )
            )
            continue

        sigs = [_minhash_signature(_shingles(f"{it.title} {it.body}")) for it in group]
        labels = _dbscan(sigs, eps=eps, min_samples=min_samples)
        # Singletons (label == -1) still become stories
        clusters: dict[int, list[int]] = {}
        for idx, lbl in enumerate(labels):
            clusters.setdefault(lbl if lbl != -1 else idx, []).append(idx)
        for _cid, indices in clusters.items():
            members = [group[i] for i in indices]
            # Pick representative = longest title (usually the most descriptive)
            rep = max(members, key=lambda x: len(x.title))
            sentiments = [m.sentiment for m in members if m.sentiment is not None]
            avg_sent = sum(sentiments) / len(sentiments) if sentiments else 0.0
            cluster_id = hashlib.sha1(
                ":".join(sorted(m.id for m in members)).encode(),
                usedforsecurity=False,
            ).hexdigest()[:16]
            stories.append(
                StoryCluster(
                    cluster_id=cluster_id,
                    representative_title=rep.title,
                    source_urls=[m.url for m in members if m.url],
                    sources=sorted({m.source for m in members if m.source}),
                    first_seen=min(m.published_at for m in members),
                    last_updated=max(m.published_at for m in members),
                    item_count=len(members),
                    sentiment_avg=avg_sent,
                    item_ids=[m.id for m in members],
                )
            )

    logger.info(
        "news_clustered items=%d stories=%d windows=%d elapsed_ms=%.1f",
        len(items),
        len(stories),
        len(windows),
        (time.time() - t0) * 1000,
    )
    return stories

# ── DB persistence (optional, lazy import) ──────────────────────────
_PG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS news_clusters (
  cluster_id           TEXT PRIMARY KEY,
  representative_title TEXT NOT NULL,
  first_seen           TIMESTAMPTZ NOT NULL,
  last_updated         TIMESTAMPTZ NOT NULL,
  item_count           INTEGER NOT NULL DEFAULT 0,
  sentiment_avg        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  source_urls          JSONB NOT NULL DEFAULT '[]'::jsonb,
  sources              JSONB NOT NULL DEFAULT '[]'::jsonb,
  item_ids             JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS news_clusters_last_updated_idx
  ON news_clusters (last_updated DESC);
"""

async def ensure_schema() -> bool:
    """Create news_clusters table if missing. Returns True on success."""
    try:
        import asyncpg

        from app.core.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            await conn.execute(_PG_SCHEMA_SQL)
        finally:
            await conn.close()
        logger.info("news_clusters_schema_ready")
        return True
    except (KeyError, ValueError, TypeError) as exc :
        logger.debug("ensure_schema_error", exc_info=True)
        logger.warning("news_clusters_schema_failed err=%s", exc)
        return False

async def persist_clusters(stories: list[StoryCluster]) -> int:
    """Upsert stories to Postgres. Returns rows affected."""
    if not stories:
        return 0
    try:
        import json

        import asyncpg

        from app.core.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            rows = [
                (
                    s.cluster_id,
                    s.representative_title,
                    s.first_seen,
                    s.last_updated,
                    s.item_count,
                    s.sentiment_avg,
                    json.dumps(s.source_urls),
                    json.dumps(s.sources),
                    json.dumps(s.item_ids),
                )
                for s in stories
            ]
            await conn.executemany(
                """
                INSERT INTO news_clusters
                  (cluster_id, representative_title, first_seen, last_updated,
                   item_count, sentiment_avg, source_urls, sources, item_ids)
                VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb)
                ON CONFLICT (cluster_id) DO UPDATE SET
                  representative_title = EXCLUDED.representative_title,
                  first_seen = EXCLUDED.first_seen,
                  last_updated = EXCLUDED.last_updated,
                  item_count = EXCLUDED.item_count,
                  sentiment_avg = EXCLUDED.sentiment_avg,
                  source_urls = EXCLUDED.source_urls,
                  sources = EXCLUDED.sources,
                  item_ids = EXCLUDED.item_ids
                """,
                rows,
            )
            return len(rows)
        finally:
            await conn.close()
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc :
        logger.debug("unknown_error", exc_info=True)
        logger.warning("news_clusters_persist_failed err=%s", exc)
        return 0

async def load_recent_clusters(limit: int = 50) -> list[dict]:
    """Load recent clusters from Postgres."""
    try:
        import json

        import asyncpg

        from app.core.db_pool import PG_URL

        conn = await asyncpg.connect(PG_URL)
        try:
            rows = await conn.fetch(
                "SELECT * FROM news_clusters ORDER BY last_updated DESC LIMIT $1",
                limit,
            )
            return [
                {
                    "cluster_id": r["cluster_id"],
                    "representative_title": r["representative_title"],
                    "first_seen": r["first_seen"].isoformat(),
                    "last_updated": r["last_updated"].isoformat(),
                    "item_count": r["item_count"],
                    "sentiment_avg": r["sentiment_avg"],
                    "source_urls": json.loads(r["source_urls"]),
                    "sources": json.loads(r["sources"]),
                    "item_ids": json.loads(r["item_ids"]),
                }
                for r in rows
            ]
        finally:
            await conn.close()
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc :
        logger.debug("load_recent_clusters_error", exc_info=True)
        logger.warning("news_clusters_load_failed err=%s", exc)
        return []

__all__ = [
    "NewsItem",
    "StoryCluster",
    "cluster_items",
    "ensure_schema",
    "load_recent_clusters",
    "persist_clusters",
]
