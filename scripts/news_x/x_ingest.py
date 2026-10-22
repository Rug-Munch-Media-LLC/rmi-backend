"""CLI for the X ingestion lane.

    python3 x_ingest.py --once              # fetch, store, embed, print top 10
    python3 x_ingest.py --once --dry-run    # fetch + report, no writes
    python3 x_ingest.py --rank              # ranking table only
    python3 x_ingest.py --once --handles CertiKAlert,zachxbt

Environment: NF_PG_* (database, shared with news_fallback), RMI_RAG_API_KEY
(RAG ingest), X_INGEST_PROXY (residential egress for the syndication path),
X_NITTER_MIRRORS (opt-in mirrors), X_SOURCES_JSON (registry override).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch
import rag
import rank
import store
from model import Post
from sources import Source, load_registry


def _recent(posts: list[Post], hours: int) -> list[Post]:
    """Keep only posts inside the window, newest first."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    return sorted(
        (p for p in posts if p.created_at >= cutoff), key=lambda p: p.created_at, reverse=True
    )


def run_once(
    hours: int,
    dry_run: bool,
    only: set[str] | None,
    stages: tuple[str, ...] | None,
    min_posts: int = 0,
) -> dict:
    """Fetch every registered handle, store, embed, rank."""
    registry: list[Source] = load_registry()
    if only:
        registry = [s for s in registry if s.handle.lower() in only]
    limiter = fetch.Limiter()
    totals = {"handles": 0, "routes": {}, "fetched": 0, "inserted": 0, "duplicate": 0, "skipped": 0}
    inserted_ids: list[str] = []
    for src in registry:
        chain = fetch.fetch_handle(src.handle, limiter, stages, min_posts)
        posts = _recent(chain.posts, hours)
        totals["handles"] += 1
        totals["fetched"] += len(posts)
        if chain.route:
            totals["routes"][chain.route] = totals["routes"].get(chain.route, 0) + 1
        if dry_run:
            print(
                f"{src.handle:<20} {chain.route or '-':<22} {len(posts):>3} posts  "
                f"{';'.join(chain.errors)[:70]}"
            )
            continue
        result = store.store_many(posts, f"@{src.handle}", src.trust, src.klass)
        totals["inserted"] += int(result["inserted"])
        totals["duplicate"] += int(result["duplicate"])
        totals["skipped"] += int(result["skipped"])
        inserted_ids.extend(result["news_ids"])
        print(
            f"{src.handle:<20} {chain.route or '-':<22} {len(posts):>3} fetched "
            f"{result['inserted']:>3} new {result['duplicate']:>2} dup  "
            f"{';'.join(chain.errors)[:60]}"
        )
    if not dry_run and inserted_ids:
        totals["rag"] = rag.push([i for i in inserted_ids if store.rag_ready(i)])
    return totals


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description="X ingestion lane")
    ap.add_argument("--once", action="store_true", help="run a single ingest pass")
    ap.add_argument("--rank", action="store_true", help="print the ranking table only")
    ap.add_argument("--handles", default="", help="comma-separated handle subset")
    ap.add_argument("--hours", type=int, default=24, help="window in hours (default 24)")
    ap.add_argument("--top", type=int, default=10, help="rows in the ranking table")
    ap.add_argument(
        "--stages", default="", help="comma-separated stages (syndication,nitter,reader)"
    )
    ap.add_argument(
        "--min-posts", type=int, default=0, help="posts per handle before the readers stop"
    )
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args(argv)

    if not (args.once or args.rank):
        ap.print_help()
        return 2

    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip()) or None
    if args.once:
        started = time.monotonic()
        only = {h.strip().lstrip("@").lower() for h in args.handles.split(",") if h.strip()}
        totals = run_once(args.hours, args.dry_run, only or None, stages, args.min_posts)
        print(
            f"\nhandles={totals['handles']} fetched={totals['fetched']} "
            f"inserted={totals['inserted']} duplicate={totals['duplicate']} "
            f"skipped={totals['skipped']}"
        )
        print(f"routes={totals['routes']}")
        if "rag" in totals:
            print(f"rag={totals['rag']}")
        print(f"elapsed={time.monotonic() - started:.1f}s")

    print()
    print(rank.render(rank.top(args.top)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
