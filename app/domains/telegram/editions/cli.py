"""Dry-run renderer — prints the exact payloads, sends nothing.

    python -m app.domains.telegram.editions --pretty
    python -m app.domains.telegram.editions --decisions decisions.json --pretty

Two halves of one contract:

* the three editions, rendered from a lead story (``--lead-file`` or the
  built-in fixture) exactly as :func:`publish_edition` would render them;
* the tiered routing plan for Lane C decisions, with caps, promotion and
  dedupe applied, showing which lane each story lands in and why anything
  was skipped.

No sender is constructed, so there is no path from this module to a network
call — that is the point of a dry run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.domains.telegram.editions.editions import EDITIONS, edition_lanes, edition_title, render_edition, schedule
from app.domains.telegram.editions.lanes_report import lane_inventory
from app.domains.telegram.editions.ledger import InMemoryLedger, day_key
from app.domains.telegram.editions.render import story_payload
from app.domains.telegram.editions.routing import plan_delivery

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

FIXTURE_LEAD: dict[str, Any] = {
    "slug": "daily-rug-2026-10-29-mixer-drain",
    "title": "Mixer drain leaves 4,100 wallets exposed",
    "teaser": (
        "A settlement contract lost its admin key overnight and the attacker is "
        "already laundering through three bridges. On-chain traces tie the deployer "
        "to a launchpad cohort flagged last month, which makes the next 24 hours the "
        "window that matters."
    ),
    "url": "https://rugmunch.io/story/daily-rug-2026-10-29-mixer-drain",
    "image_url": "https://rugmunch.io/og/daily-rug-2026-10-29-mixer-drain.png",
    "tags": ["exploit", "mixer", "bridge", "risk"],
}

FIXTURE_DECISIONS: list[dict[str, Any]] = [
    {
        "slug": "daily-rug-2026-10-29-mixer-drain",
        "title": "Mixer drain leaves 4,100 wallets exposed",
        "teaser": "Settlement contract drained; three bridges used to launder.",
        "url": "https://rugmunch.io/story/daily-rug-2026-10-29-mixer-drain",
        "image_url": "https://rugmunch.io/og/daily-rug-2026-10-29-mixer-drain.png",
        "tags": ["exploit", "mixer"],
        "lanes": ["news", "alerts"],
        "big_story": True,
        "score": 0.91,
    },
    {
        "slug": "whale-accumulation-sol-2026-10-29",
        "title": "Three wallets add 1.2M SOL in 48 hours",
        "teaser": "Cluster behaviour matches the Q3 accumulation pattern.",
        "tags": ["whales", "solana"],
        "lanes": ["whales", "alpha"],
        "score": 0.64,
    },
    {
        "slug": "launchpad-fee-change",
        "title": "Launchpad cuts creator fees to zero",
        "teaser": "Announced in the operator channel, unconfirmed by the contract.",
        "lanes": ["site", "news"],
        "score": 0.31,
    },
]


def render_editions(lead: Mapping[str, Any] | None = None, *, env: Mapping[str, str] | None = None, date: str | None = None) -> list[dict[str, Any]]:
    """Every edition's payload, as delivered (no send, no ledger write)."""
    source = lead or FIXTURE_LEAD
    return [
        {
            "key": edition.key,
            "title": edition_title(edition, env),
            "at_utc": f"{edition.hour:02d}:{edition.minute:02d}",
            "lanes": list(edition_lanes(env)),
            "payload": render_edition(edition, source, env=env, date=date).as_dict(),
        }
        for edition in EDITIONS
    ]


def render_routing(
    decisions: Sequence[Mapping[str, Any]],
    *,
    env: Mapping[str, str] | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """The routing plan for Lane C decisions: posts rendered, skips explained."""
    day = date or day_key()
    plan = plan_delivery(decisions, day=day, ledger=InMemoryLedger(), env=env)
    return {
        "day": day,
        "posts": [
            {
                "lane": post.lane,
                "slug": post.decision.slug,
                "key": post.key,
                "big_story": post.decision.big_story,
                "payload": story_payload(lead=post.decision.raw or post.decision.as_dict(), site_url="https://rugmunch.io", lane=post.lane).as_dict(),
            }
            for post in plan.posts
        ],
        "skipped": [
            {"slug": row.slug, "lane": row.lane, "reason": row.reason} for row in plan.skipped
        ],
    }


def build_preview(
    decisions: Sequence[Mapping[str, Any]] | None = None,
    *,
    lead: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """Everything a dry run shows, in one value-free document."""
    return {
        "lanes": [dict(row) for row in lane_inventory(env)],
        "schedule": [dict(row) for row in schedule()],
        "editions": render_editions(lead, env=env, date=date),
        "routing": render_routing(decisions or FIXTURE_DECISIONS, env=env, date=date),
    }


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.domains.telegram.editions")
    parser.add_argument("--decisions", help="JSON file with Lane C decisions (array)")
    parser.add_argument("--lead", help="JSON file with one edition lead story")
    parser.add_argument("--date", help="override the UTC day (YYYY-MM-DD)")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON")
    args = parser.parse_args(argv)

    decisions = _load_json(args.decisions) if args.decisions else None
    lead = _load_json(args.lead) if args.lead else None
    preview = build_preview(decisions, lead=lead, date=args.date)
    print(json.dumps(preview, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
