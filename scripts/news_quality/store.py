"""Postgres half of the news-quality backfill: schema, reads, idempotent writes.

Column contract (disjoint from the ratings pipeline):

``body_source``           publisher | truncated | google_shell
``body_quality_score``    0-100 from :func:`app.domains.news.quality.score_body`
``body_quality_verdict``  ok | thin | stub
``truncated``             body is not the full publisher article
``canonical_url``         publisher URL once the Google shell is resolved
``resolved_at``           when resolution was attempted (NULL = pending)

``quality_score`` / ``quality_verdict`` belong to the ACCEPT/REJECT/NEEDS_SOURCE
ratings pipeline and are NEVER written here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse

import psycopg

from app.domains.news.quality import BodyQuality, score_body

DDL = """
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS body_quality_verdict TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS body_quality_score SMALLINT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS body_source TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS truncated BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS canonical_url TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_news_body_source ON news_items(body_source);
CREATE INDEX IF NOT EXISTS idx_news_body_quality_verdict
  ON news_items(body_quality_verdict) WHERE body_quality_verdict IS NOT NULL;
"""


def _pg_params() -> dict[str, object]:
    """Connection kwargs: ``NF_PG_*`` overrides, else the app's ``DATABASE_URL``.

    The URL carries the credentials and the *container* host
    (``rmi-postgres``); host-side runs reach the same DB on loopback, so the
    host is overridable. No credential is ever written in source.
    """
    raw = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(raw) if raw else None
    password = parsed.password if parsed and parsed.password else ""
    return {
        "host": os.environ.get("NF_PG_HOST", "127.0.0.1"),
        "port": int(os.environ.get("NF_PG_PORT") or (parsed.port if parsed else 5432) or 5432),
        "dbname": os.environ.get("NF_PG_DB") or (parsed.path.lstrip("/") if parsed else "rmi"),
        "user": os.environ.get("NF_PG_USER") or (parsed.username if parsed else "rmi"),
        "password": os.environ.get("NF_PG_PASSWORD") or unquote(password),
    }


def connect() -> psycopg.Connection:
    """Open an autocommit psycopg connection from the deploy env."""
    return psycopg.connect(**_pg_params(), autocommit=True)


def ensure_schema(conn: psycopg.Connection) -> None:
    """Create the body-quality columns/indexes when a deploy has not run them."""
    conn.execute(DDL)


def distribution(conn: psycopg.Connection) -> dict[str, int]:
    """Body-quality verdict counts, for the before/after report."""
    rows = conn.execute(
        "SELECT COALESCE(body_quality_verdict,'(null)'), count(*) FROM news_items GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return dict(rows)


def rescore(conn: psycopg.Connection) -> int:
    """Recompute body quality for every row; returns rows actually changed."""
    rows = conn.execute("SELECT news_id, url, title, body_markdown FROM news_items").fetchall()
    changed = 0
    for news_id, url, title, body in rows:
        q = score_body(body, url, title)
        cur = conn.execute(
            "UPDATE news_items SET body_source=%s, body_quality_score=%s, "
            "body_quality_verdict=%s, truncated=%s "
            "WHERE news_id=%s AND (body_source IS DISTINCT FROM %s "
            "OR body_quality_score IS DISTINCT FROM %s "
            "OR body_quality_verdict IS DISTINCT FROM %s "
            "OR truncated IS DISTINCT FROM %s)",
            (q.body_source, q.score, q.verdict, q.truncated, news_id, q.body_source, q.score, q.verdict, q.truncated),
        )
        changed += cur.rowcount
    return changed


def pending_google(conn: psycopg.Connection, limit: int) -> list[tuple[str, str, str, str, str]]:
    """Google-shell rows not yet attempted, newest first."""
    return conn.execute(
        "SELECT news_id, url, title, source, COALESCE(body_markdown,'') FROM news_items "
        "WHERE url LIKE '%%news.google.com%%' AND resolved_at IS NULL "
        "ORDER BY published_at DESC LIMIT %s",
        (limit,),
    ).fetchall()


def mark_resolved(conn: psycopg.Connection, news_id: str, canonical: str, body: str, quality: BodyQuality) -> None:
    """Store the recovered publisher body + its quality (never touches ratings)."""
    conn.execute(
        "UPDATE news_items SET canonical_url=%s, body_markdown=%s, body_source=%s, "
        "body_quality_score=%s, body_quality_verdict=%s, truncated=%s, resolved_at=%s "
        "WHERE news_id=%s",
        (
            canonical,
            body,
            quality.body_source,
            quality.score,
            quality.verdict,
            quality.truncated,
            datetime.now(UTC),
            news_id,
        ),
    )


def mark_shell(conn: psycopg.Connection, news_id: str, quality: BodyQuality) -> None:
    """Resolution failed: keep the body, record the shell verdict, stamp the attempt."""
    conn.execute(
        "UPDATE news_items SET body_source=%s, body_quality_score=%s, "
        "body_quality_verdict=%s, truncated=%s, resolved_at=%s WHERE news_id=%s",
        (quality.body_source, quality.score, quality.verdict, quality.truncated, datetime.now(UTC), news_id),
    )
