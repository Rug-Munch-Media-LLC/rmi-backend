#!/usr/bin/env python3
"""Sync Ghost posts into ``news_items`` (idempotent).

Usage (Talos):  python3 scripts/ghost_feed_sync.py [--limit 15]

Reads ``GHOST_CONTENT_API_KEY`` / ``GHOST_CONTENT_API_URL`` from the
environment (or the backend .env when run with ``--env-file``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _load_env(path: str | None) -> None:
    if not path:
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        import os

        os.environ.setdefault(key.strip(), value.strip())


def _pool_dsn() -> str:
    import os

    dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or ""
    if not dsn:
        user = os.getenv("POSTGRES_USER", "rmi")
        password = os.getenv("POSTGRES_PASSWORD", "")
        host = os.getenv("POSTGRES_HOST", "127.0.0.1")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "rmi")
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


async def _run(limit: int) -> int:
    import asyncpg

    from app.domains.news.ghost_sync import sync_ghost_posts

    pool = await asyncpg.create_pool(_pool_dsn(), min_size=1, max_size=2)
    try:
        counts = await sync_ghost_posts(pool, limit=limit)
    finally:
        await pool.close()
    print(counts)
    return 0 if counts["upserted"] else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--env-file", default=None)
    args = ap.parse_args()
    _load_env(args.env_file)
    return asyncio.run(_run(args.limit))


if __name__ == "__main__":
    sys.exit(main())
