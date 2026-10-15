#!/usr/bin/env python3
"""Repair dead RSS sources in unified_news_sources.json.

The health probe (test_news_sources.py) marks sources DEAD for reasons that
are NOT all permanent:

  * HTTP 429 — our own prober rate-limited them (concurrency 10); alive.
  * HTTP 403 — bot protection (Cloudflare); alive behind a real UA / Pry.
  * HTTP 404 / 410 — moved or killed (Medium ended RSS: 410). The site may
    still have a feed at another path.

Strategy per dead/stale source: re-probe with a browser UA and low
concurrency, trying the original URL plus a bounded set of alternate feed
paths on the same host. A candidate that yields >=1 valid item wins.

Dry-run by default; --apply rewrites the source URL in place. Nothing is
ever removed — a source that cannot be repaired keeps its original URL and
stays flagged in the status file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import feedparser
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = REPO_ROOT / "app" / "databus" / "unified_news_sources.json"
STATUS_FILE = REPO_ROOT / "app" / "databus" / "news_sources_status.json"
REPORT_FILE = REPO_ROOT / "docs" / "news-sources-repair-report.md"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_AGE_DAYS = 30

#: Alternate feed paths tried on the same host, most common first.
ALT_PATHS = (
    "/feed",
    "/rss.xml",
    "/rss",
    "/atom.xml",
    "/feed.xml",
    "/index.xml",
    "/blog/feed",
    "/blog/rss.xml",
    "/blog/index.xml",
    "/news/feed",
    "/feed/",
)


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _candidates(url: str) -> list[str]:
    """The original URL plus bounded alternate feed paths on the same host."""
    out = [url]
    origin = _origin(url)
    for path in ALT_PATHS:
        cand = origin + path
        if cand != url:
            out.append(cand)
    # trailing-slash variant of the original
    if not url.endswith("/"):
        out.append(url + "/")
    return out


def _ok(feed_bytes: bytes) -> bool:
    """At least one item with a title + link, published within MAX_AGE_DAYS."""
    parsed = feedparser.parse(feed_bytes)
    cutoff = datetime.now(UTC) - timedelta(days=MAX_AGE_DAYS)
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not (title and link):
            continue
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            dt = datetime(*published[:6], tzinfo=UTC)
            if dt >= cutoff:
                return True
        else:
            return True  # undated item still counts as alive
    return False


async def _probe_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str) -> tuple[str, str | None]:
    """Return (url, None) when alive, else (url, error string)."""
    async with sem:
        try:
            r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
            if r.status_code != 200:
                return url, f"HTTP {r.status_code}"
            if _ok(r.content):
                return url, None
            return url, "no recent items"
        except httpx.HTTPError as exc:
            return url, f"{type(exc).__name__}"


async def repair(urls: list[str], concurrency: int) -> dict[str, str | None]:
    """Probe every candidate for every url; first alive candidate wins."""
    sem = asyncio.Semaphore(concurrency)
    fixed: dict[str, str | None] = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in urls:
            winner = None
            for cand in _candidates(url):
                probed, err = await _probe_one(client, sem, cand)
                if err is None:
                    winner = probed
                    break
            fixed[url] = winner
    return fixed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="rewrite the sources file")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    data = json.loads(SOURCES_FILE.read_text())
    status = json.loads(STATUS_FILE.read_text())["sources"]
    broken = [s for s in data["sources"] if status.get(s["id"], {}).get("status") in ("DEAD", "STALE")]
    if args.limit:
        broken = broken[: args.limit]
    print(f"repairing {len(broken)} DEAD/STALE sources (concurrency={args.concurrency})")

    fixed = asyncio.run(repair([s["url"] for s in broken], args.concurrency))
    recovered = {u: w for u, w in fixed.items() if w and w != u}
    same = [u for u, w in fixed.items() if w == u]
    failed = [u for u, w in fixed.items() if not w]
    print(f"recovered at a new URL: {len(recovered)}")
    print(f"alive at the original URL: {len(same)}")
    print(f"still dead: {len(failed)}")

    if args.apply:
        by_url = {s["url"]: s for s in data["sources"]}
        for old, new in recovered.items():
            by_url[old]["url"] = new
            by_url[old]["repaired_at"] = datetime.now(UTC).isoformat()
        data["updated"] = datetime.now(UTC).date().isoformat()
        SOURCES_FILE.write_text(json.dumps(data, indent=2) + "\n")
        REPORT_FILE.write_text(
            "# News sources repair report\n\n"
            f"run: {datetime.now(UTC).isoformat()}\n\n"
            f"- recovered at a new URL: {len(recovered)}\n"
            f"- alive at the original URL: {len(same)}\n"
            f"- still dead: {len(failed)}\n\n"
            + "\n".join(f"- {old} -> {new}" for old, new in sorted(recovered.items()))
            + "\n"
        )
        print("applied to", SOURCES_FILE)
    else:
        for old, new in sorted(recovered.items())[:20]:
            print(f"  {old}\n    -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
