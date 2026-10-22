"""CLI: backfill articles for RSS sources the normal probe cannot reach.

Usage:
  python3 backfill.py list
  python3 backfill.py run [--limit 20] [--max-sources 10] [--only A,B] [--include-dead]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

import db
import discover
import extract
import fetch_chain
import ingest

_FAILING_SQL = """
SELECT source, url, status, http_status, COALESCE(last_error, '')
FROM rss_feed_status
WHERE status = 'error' OR (%s AND status = 'dead_permanent')
ORDER BY source
"""


def _rows(include_dead: bool) -> list[tuple[str, str, str, int, str]]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(_FAILING_SQL, (include_dead,))
        return cur.fetchall()


def _article_urls(feed_body: str, feed_url: str, limit: int) -> tuple[list[str], str]:
    """Feed entries when the body parses as a feed, else listing-page links."""
    entries = discover.feed_entries(feed_body)
    if entries:
        return [link for link, _title in entries[:limit]], "feed"
    return discover.listing_links(feed_body, feed_url, limit=limit), "listing"


def _backfill_source(source: str, feed_url: str, limit: int) -> dict[str, object]:
    result: dict[str, object] = {
        "source": source,
        "feed_url": feed_url,
        "feed_route": "blocked",
        "discovered": 0,
        "tried": 0,
        "ingested": 0,
        "duplicates": 0,
        "rag": 0,
        "blocked": "",
    }
    got = fetch_chain.fetch(feed_url)
    if not got.body:
        result["blocked"] = got.error
        return result
    urls, mode = _article_urls(got.body, feed_url, limit)
    result["feed_route"] = f"{got.route}/{mode}"
    result["discovered"] = len(urls)
    for url in urls:
        result["tried"] = int(result["tried"]) + 1
        page = fetch_chain.fetch(url)
        if not page.body:
            continue
        article = extract.extract(url, page.body)
        if article is None and page.route == "direct":
            page = fetch_chain.fetch(url, start=1)  # JS shell: escalate past direct
            article = extract.extract(url, page.body) if page.body else None
        if article is None:
            continue
        stored = ingest.store(article, source, page.route)
        if stored["inserted"]:
            result["ingested"] = int(result["ingested"]) + 1
            if str(stored["rag"]).startswith("rag_200"):
                result["rag"] = int(result["rag"]) + 1
        else:
            result["duplicates"] = int(result["duplicates"]) + 1
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "run"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-sources", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--include-dead", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    rows = _rows(args.include_dead)
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",") if s.strip()}
        rows = [r for r in rows if r[0].lower() in wanted]
    if args.max_sources:
        rows = rows[: args.max_sources]

    if args.command == "list":
        print(f"{len(rows)} failing sources")
        for source, url, status, http_status, err in rows:
            print(f"{source}\t{status}\t{http_status}\t{url}\t{err[:70]}")
        return 0

    report: list[dict[str, object]] = []
    for source, url, _status, _http, _err in rows:
        entry = _backfill_source(source, url, args.limit)
        report.append(entry)
        print(
            f"{source}: route={entry['feed_route']} found={entry['discovered']} "
            f"new={entry['ingested']} dup={entry['duplicates']} rag={entry['rag']} "
            f"{entry['blocked']}",
            flush=True,
        )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sources_attempted": len(report),
        "sources_recovered": sum(1 for r in report if int(r["ingested"]) > 0),
        "articles_ingested": sum(int(r["ingested"]) for r in report),
        "rag_documents": sum(int(r["rag"]) for r in report),
        "sources": report,
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    print(json.dumps({k: payload[k] for k in payload if k != "sources"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
