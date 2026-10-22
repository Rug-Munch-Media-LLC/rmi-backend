"""Telegram public-preview news ingestion (no account session, no auth).

Entry points
------------
``python -m app.databus.tg_news run [--channels a,b] [--limit 200]``
    Fetch + persist; prints the per-channel run summary as JSON.
``python -m app.databus.tg_news report``
    Print stored state (last_id, subscribers, profile) without any network.

The DataBus feed consumes the JSONL files via
:func:`app.databus.tg_news.databus.telegram_news_articles`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.databus.tg_news.databus import telegram_news_articles
from app.databus.tg_news.pipeline import ingest_all
from app.databus.tg_news.state import load_state, news_dir


def _report(root: str | None) -> dict[str, Any]:
    directory = news_dir(root)
    rows = []
    for path in sorted(directory.glob("*.state.json")) if directory.exists() else []:
        handle = path.name.replace(".state.json", "")
        stored = load_state(handle, root)
        rows.append(
            {
                "channel": handle,
                "last_id": stored.get("last_id"),
                "items_total": stored.get("items_total", 0),
                "subscribers": stored.get("subscribers"),
                "kind": (stored.get("profile") or {}).get("kind"),
                "trust": (stored.get("profile") or {}).get("trust_score"),
                "forward_share": (stored.get("profile") or {}).get("forward_share"),
                "cadence_per_hour": (stored.get("profile") or {}).get("cadence_per_hour"),
                "last_run": stored.get("last_run"),
            }
        )
    articles = telegram_news_articles(limit=5, root=root)
    return {"dir": str(Path(directory)), "channels": rows, "sample_articles": len(articles)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tg_news", description=__doc__)
    parser.add_argument("command", nargs="?", choices=("run", "report"), default="run")
    parser.add_argument("--channels", default="", help="comma-separated handles (overrides seeds)")
    parser.add_argument("--limit", type=int, default=200, help="max posts per channel (backfill cap)")
    parser.add_argument("--root", default=None, help="storage root (default data/telegram_news)")
    parser.add_argument("--delay", type=float, default=0.7, help="seconds between page requests")
    args = parser.parse_args(argv)

    if args.command == "report":
        print(json.dumps(_report(args.root), indent=1, ensure_ascii=False))
        return 0
    channels = [name.strip() for name in args.channels.split(",") if name.strip()] or None
    result = asyncio.run(ingest_all(channels, limit=args.limit, delay=args.delay, root=args.root))
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result["channels_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
