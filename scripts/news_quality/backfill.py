"""Backfill news body quality once, then stay idempotent.

Two phases, both driven by the ONE scorer in ``app.domains.news.quality``:

A. re-score every row (``body_source`` / ``body_quality_score`` /
   ``body_quality_verdict`` / ``truncated``)
B. resolve ``news.google.com`` interstitials to the publisher, fetch the real
   body, re-score. On failure the body is left untouched and the row is marked
   ``google_shell`` with ``resolved_at`` set, so a second run changes nothing.

Usage::

    python3 backfill.py --limit 40 --report /tmp/news-quality.json
    python3 backfill.py --rescore-only

Column safety: the ratings pipeline's ``quality_verdict`` (ACCEPT/REJECT/
NEEDS_SOURCE) and ``quality_score`` are never written by this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import fetch
import store

from app.domains.news.quality import score_body
from app.domains.news.resolve import Resolver


def _resolve_rows(conn: object, limit: int) -> list[dict[str, object]]:
    """Phase B: resolve pending Google rows and rewrite the ones we recovered."""
    rows = store.pending_google(conn, limit)
    resolver = Resolver()
    report: list[dict[str, object]] = []
    for news_id, url, title, source, body_before in rows:
        canonical = resolver.canonical(url)
        body = ""
        if canonical:
            body = fetch.body_from_html(fetch.publisher_html(canonical), canonical)
        ok = bool(canonical) and len(body) >= fetch.MIN_PUBLISHER_BODY
        if ok:
            quality = score_body(body, canonical, title)
            store.mark_resolved(conn, news_id, canonical, body, quality)
        else:
            quality = score_body(body_before, url, title)
            store.mark_shell(conn, news_id, quality)
        report.append(
            {
                "news_id": news_id,
                "source": source,
                "url": url,
                "canonical_url": canonical,
                "resolved": ok,
                "body_chars": len(body),
                "body_before_chars": len(body_before),
                "quality": asdict(quality),
            }
        )
    return report


def _payload(
    resolved: list[dict[str, object]],
    before: dict[str, int],
    after: dict[str, int],
    rescored: int,
) -> dict[str, object]:
    """Build the JSON report (counts by source + up to 3 resolved samples)."""
    attempted: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    for item in resolved:
        attempted[str(item["source"])] += 1
        if item["resolved"]:
            wins[str(item["source"])] += 1
    samples = [i for i in resolved if i["resolved"]][:3]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "before": before,
        "after": after,
        "rows_changed": rescored,
        "google_rows_attempted": len(resolved),
        "google_rows_resolved": wins.total(),
        "resolution_by_source": {src: f"{wins[src]}/{attempted[src]}" for src in sorted(attempted)},
        "samples": samples,
        "unresolved": [i["news_id"] for i in resolved if not i["resolved"]][:10],
    }


def main(argv: list[str]) -> int:
    """Run both phases and return a process status."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=700, help="max Google rows to resolve")
    parser.add_argument("--rescore-only", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    conn = store.connect()
    try:
        store.ensure_schema(conn)
        before = store.distribution(conn)
        rescored = store.rescore(conn)
        resolved = [] if args.rescore_only else _resolve_rows(conn, args.limit)
        if resolved:
            rescored += store.rescore(conn)
        after = store.distribution(conn)
    finally:
        conn.close()

    payload = _payload(resolved, before, after, rescored)
    printable = {k: v for k, v in payload.items() if k != "samples"}
    print(json.dumps(printable, indent=2))
    for sample in payload["samples"]:
        print(
            f"sample {sample['news_id']} -> {sample['canonical_url']} "
            f"({sample['body_chars']} chars, verdict={sample['quality']['verdict']})"
        )
    if args.report:
        Path(args.report).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
