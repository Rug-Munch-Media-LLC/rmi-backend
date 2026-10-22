"""Re-verify the Telegram source table against the public preview.

The failure mode this guards is silence: a 302 preview yields an empty JSONL with
no error, and a 200 can belong to a squatter holding the name ("Certik Alert
(Meme)", 48 subs). So the probe records *status without following redirects* plus
what the page says about itself, and ``--check`` fails when a channel we claim to
sweep is no longer open.

Usage (Talos, repo venv)::

    python scripts/tg_news_probe.py            # table for every listed handle
    python scripts/tg_news_probe.py --check    # exit 1 if a swept desk is closed
    python scripts/tg_news_probe.py --handle zachxbt
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.databus.tg_news.verified_channels import IMPOSTOR, PREVIEW_OFF, SWEPT

POSTS = re.compile(r"tgme_widget_message_wrap")
TITLE = re.compile(r"tgme_channel_info_header_title[^>]*>(.*?)</div>", re.S)
SUBS = re.compile(r"counter_value[^>]*>(.*?)</span>", re.S)
STAMP = re.compile(r'tgme_widget_message_date[^>]*>\s*<time datetime="([^"]+)"', re.S)
UA = {"User-Agent": "Mozilla/5.0 (compatible; rmi-news-probe/1.0)", "Accept-Language": "en"}


async def probe(client: httpx.AsyncClient, handle: str) -> dict[str, Any]:
    """One preview probe: status, post count, title, subscribers, newest post."""
    try:
        response = await client.get(f"https://t.me/s/{handle}")
    except httpx.HTTPError as exc:
        return {"handle": handle, "error": type(exc).__name__, "posts": 0}
    row: dict[str, Any] = {"handle": handle, "status": response.status_code, "posts": 0}
    if response.status_code == 200:
        title = TITLE.search(response.text)
        subs = SUBS.search(response.text)
        stamps = STAMP.findall(response.text)
        row.update(
            {
                "posts": len(POSTS.findall(response.text)),
                "title": re.sub("<[^>]+>", "", title.group(1)).strip() if title else None,
                "subs": subs.group(1).strip() if subs else None,
                "newest": max(stamps) if stamps else None,
            }
        )
    return row


async def run(handles: list[str], concurrency: int = 8) -> list[dict[str, Any]]:
    """Probe ``handles`` in small parallel waves (the preview rate-limits bursts)."""
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0, headers=UA) as client:
        for start in range(0, len(handles), concurrency):
            wave = handles[start : start + concurrency]
            rows.extend(await asyncio.gather(*(probe(client, handle) for handle in wave)))
            await asyncio.sleep(0.3)
    return rows


def failures(rows: list[dict[str, Any]]) -> list[str]:
    """Handles we claim to sweep that no longer answer an open preview."""
    open_handles = {row["handle"].lower() for row in rows if row.get("status") == 200 and row.get("posts")}
    return [channel.username for channel in SWEPT if channel.username.lower() not in open_handles]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", action="append", default=[], help="probe only these handles")
    parser.add_argument("--check", action="store_true", help="exit 1 when a swept desk is closed")
    args = parser.parse_args(argv)
    handles = args.handle or sorted(
        {channel.username for channel in SWEPT} | set(PREVIEW_OFF) | set(IMPOSTOR), key=str.lower
    )
    rows = asyncio.run(run(handles))
    for row in sorted(rows, key=lambda item: item.get("posts") or 0, reverse=True):
        print(
            f"{row['handle']:<24} st={row.get('status')} posts={row.get('posts', 0):>3} "
            f"subs={row.get('subs') or '-':>8} newest={row.get('newest') or '-'} title={(row.get('title') or '')[:44]}"
        )
    if not args.check:
        return 0
    broken = failures(rows)
    for handle in broken:
        print(f"CLOSED-BUT-SWEPT {handle}", file=sys.stderr)
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
