#!/usr/bin/env python3
"""Probe unified crypto news sources and classify them as VERIFIED / DEAD / STALE.

Reads ``app/databus/unified_news_sources.json`` (255 sources: rss / reddit / nitter),
fetches each feed politely (bounded concurrency, short timeout), parses it with
feedparser, and requires at least one valid item (title + link + recent pub date)
to mark the source VERIFIED.

Writes:
- ``app/databus/news_sources_status.json``  — per-source status machine-readable map
- ``docs/news-sources-test-report.md``      — human-readable report

No sources are modified or removed; this only classifies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import httpx

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = REPO_ROOT / "app" / "databus" / "unified_news_sources.json"
STATUS_FILE = REPO_ROOT / "app" / "databus" / "news_sources_status.json"
REPORT_FILE = REPO_ROOT / "docs" / "news-sources-test-report.md"

DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_AGE_DAYS = 30

# A browser UA, deliberately: the bot-identifying UA got 403/429 from
# Cloudflare-protected feeds and mis-flagged live sources as DEAD
# (the Substack block of 2026-08-13 was entirely this — all 9 feeds
# answered real RSS to a browser UA).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

VERIFIED = "VERIFIED"
DEAD = "DEAD"
STALE = "STALE"


def _entry_datetime(entry: dict[str, Any]) -> datetime | None:
    """Return the entry's published/updated time as tz-aware UTC, if parseable."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if not parsed:
            continue
        try:
            # feedparser returns a time.struct_time (naive UTC).
            return datetime(*parsed[:6], tzinfo=UTC)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _fetch_feed(url: str, timeout_seconds: float) -> tuple[int, str]:
    """Fetch a feed with a browser TLS fingerprint when available.

    httpx's TLS signature is blocked (403) by Cloudflare-protected feeds that
    answer curl/urllib fine — the Substack block of 2026-08-13 was entirely
    this. curl_cffi impersonates a real Chrome handshake, so blocked feeds
    classify correctly. Falls back to httpx when curl_cffi is absent.
    """
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore[import-not-found]

        r = cffi_requests.get(url, impersonate="chrome", timeout=timeout_seconds, allow_redirects=True)
        return int(r.status_code), r.text
    except ImportError:
        pass
    except Exception as exc:  # curl_cffi fetch failure -> httpx fallback
        log.debug("curl_cffi fallback for %s: %s", url, exc)
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout_seconds, follow_redirects=True)
    return r.status_code, r.text


def _classify_feed(
    url: str,
    max_age_days: int,
    timeout_seconds: float,
) -> tuple[str, datetime | None, int, str | None]:
    """Fetch + parse one feed URL; returns (status, last_good, item_count, error)."""
    status_code = 0
    text = ""
    for attempt in range(2):
        try:
            status_code, text = _fetch_feed(url, timeout_seconds)
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            if attempt == 0:
                time.sleep(1.5)
                continue
            return DEAD, None, 0, f"fetch error: {exc.__class__.__name__}: {exc}"
        if status_code in (429, 403, 202) and attempt == 0:
            # Throttle/WAF answers are NOT death — retry once, gently.
            time.sleep(4.0)
            continue
        break

    if status_code != 200:
        return DEAD, None, 0, f"HTTP {status_code}"

    feed = feedparser.parse(text)
    entries: list[dict[str, Any]] = list(feed.entries)
    if not entries:
        return DEAD, None, 0, "parse error: no entries in feed"

    valid_items: list[tuple[datetime | None, str]] = []
    for entry in entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        valid_items.append((_entry_datetime(entry), title))

    if not valid_items:
        return DEAD, None, 0, "no items with both title and link"

    dates = [dt for dt, _ in valid_items if dt is not None]
    if not dates:
        return STALE, None, len(valid_items), "items present but no parseable pub dates"

    last_good = max(dates)
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    if last_good >= cutoff:
        return VERIFIED, last_good, len(valid_items), None

    days_old = (datetime.now(UTC) - last_good).days
    return (
        STALE,
        last_good,
        len(valid_items),
        f"newest item {days_old}d old (window: {max_age_days}d)",
    )


async def probe_source(
    source: dict[str, Any],
    semaphore: asyncio.Semaphore,
    max_age_days: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Probe a single source under the shared concurrency semaphore."""
    source_id: str = source["id"]
    url: str = source["url"]
    async with semaphore:
        status, last_good, item_count, error = await asyncio.to_thread(
            _classify_feed, url, max_age_days, timeout_seconds
        )
    return {
        "id": source_id,
        "name": source.get("name", source_id),
        "type": source.get("type", "rss"),
        "url": url,
        "status": status,
        "last_good": last_good.isoformat() if last_good else None,
        "item_count": item_count,
        "error": error,
    }


async def probe_all(
    sources: list[dict[str, Any]],
    concurrency: int,
    max_age_days: int,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Probe all sources with bounded concurrency, preserving input order."""
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(probe_source(s, semaphore, max_age_days, timeout_seconds) for s in sources))
    return list(results)


def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {VERIFIED: 0, DEAD: 0, STALE: 0}
    for result in results:
        counts[result["status"]] += 1
    return counts


def write_status_json(results: list[dict[str, Any]], max_age_days: int) -> None:
    """Write per-source status map: source id -> status + last-good + error."""
    payload: dict[str, Any] = {
        "generated": datetime.now(UTC).isoformat(),
        "source_file": "app/databus/unified_news_sources.json",
        "max_age_days": max_age_days,
        "counts": _summary(results),
        "sources": {
            result["id"]: {
                "status": result["status"],
                "type": result["type"],
                "last_good": result["last_good"],
                "item_count": result["item_count"],
                "error": result["error"],
            }
            for result in results
        },
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2) + "\n")


def write_report(results: list[dict[str, Any]], max_age_days: int) -> None:
    """Write the human-readable markdown report."""
    counts = _summary(results)
    lines: list[str] = [
        "# Crypto News Sources — Test Report",
        "",
        f"- **Generated:** {datetime.now(UTC).isoformat()}",
        "- **Source file:** `app/databus/unified_news_sources.json`",
        f"- **Total sources probed:** {len(results)}",
        f"- **Freshness window:** {max_age_days} days",
        "",
        "## Summary",
        "",
        f"- **VERIFIED** (returns fresh items): **{counts[VERIFIED]}**",
        f"- **DEAD** (connection/parse error): **{counts[DEAD]}**",
        f"- **STALE** (returns items, but old): **{counts[STALE]}**",
        "",
        "Status map: `app/databus/news_sources_status.json`",
        "",
    ]

    by_status: dict[str, list[dict[str, Any]]] = {VERIFIED: [], DEAD: [], STALE: []}
    for result in results:
        by_status[result["status"]].append(result)

    lines.append("## VERIFIED Sources")
    lines.append("")
    lines.append(
        f"{counts[VERIFIED]} sources returned fresh items. IDs: "
        + ", ".join(sorted(r["id"] for r in by_status[VERIFIED]))
    )
    lines.append("")

    for status in (DEAD, STALE):
        lines.append(f"## {status} Sources ({counts[status]})")
        lines.append("")
        if not by_status[status]:
            lines.append("_None._")
            lines.append("")
            continue
        lines.append("| id | type | item_count | last_good | error |")
        lines.append("|----|------|-----------:|-----------|-------|")
        for result in sorted(by_status[status], key=lambda r: r["id"]):
            error = (result["error"] or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {result['id']} | {result['type']} | {result['item_count']} "
                f"| {result['last_good'] or '-'} | {error} |"
            )
        lines.append("")

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines))


def load_sources() -> list[dict[str, Any]]:
    data = json.loads(SOURCES_FILE.read_text())
    sources: list[dict[str, Any]] = data.get("sources", [])
    missing_url = [s["id"] for s in sources if not s.get("url")]
    if missing_url:
        raise SystemExit(f"Sources missing 'url': {missing_url}")
    return sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"max concurrent requests (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-request timeout seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"freshness window in days (default: {DEFAULT_MAX_AGE_DAYS})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = load_sources()
    results = asyncio.run(probe_all(sources, args.concurrency, args.max_age_days, args.timeout))
    counts = _summary(results)
    write_status_json(results, args.max_age_days)
    write_report(results, args.max_age_days)
    print(
        f"probed {len(results)} sources "
        f"(concurrency={args.concurrency}, timeout={args.timeout}s, "
        f"max_age={args.max_age_days}d): "
        f"{counts[VERIFIED]} verified, {counts[DEAD]} dead, {counts[STALE]} stale"
    )


if __name__ == "__main__":
    main()
