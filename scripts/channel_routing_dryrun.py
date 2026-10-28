#!/usr/bin/env python3
"""Dry-run: show where five representative items land, and WHY.

Offline proof for the channel purpose registry — no Telegram call, no send.
For each sample it prints the item's kind, the channels whose profile lists that
kind (``route_content``), and the quoted purpose fragment that justifies it.

    python scripts/channel_routing_dryrun.py            # table
    python scripts/channel_routing_dryrun.py --denied   # + what the tier router would lose
"""

from __future__ import annotations

import sys

from app.core.channel_profiles import kind_for_source, load_profiles
from app.core.channel_routing import LOGICAL_PROFILE_CHANNELS, filter_plan_by_profile, route_content

#: (label, content source, tier-router plan the dispatcher would build today)
SAMPLES: tuple[tuple[str, str, list[tuple[str, str, str]]], ...] = (
    (
        "Scanner finding: SNIFF token, risk 92/100 (medium)",
        "scanner.finding",
        [("rmicryptonews", "scanner", "medium"), ("rmiscans", "scanner", "medium")],
    ),
    (
        "Rug pulled: liquidity removed, $480k (critical)",
        "rugbrain.exploit",
        [
            ("rmialerts", "rug_pull", "critical"),
            ("rmicryptonews", "rug_pull", "critical"),
            ("main_channel", "rug_pull", "critical"),
        ],
    ),
    (
        "Whale moved 12.4M USDC into a fresh wallet (high)",
        "rugbrain.whale",
        [("rmialerts", "whale", "high"), ("rmialpha", "whale", "high")],
    ),
    (
        "Telegram-scraped news: SEC approves tokenized fund (low)",
        "telegram_scrape.news",
        [("rmicryptonews", "bulletin", "low"), ("main_channel", "bulletin", "low")],
    ),
    (
        "Portfolio / market recap: majors down 3.1% on the week",
        "market.daily_wrap",
        [("rmicryptonews", "market", "low"), ("main_channel", "market", "low")],
    ),
)


def _purpose_fragment(role: str, purpose: str) -> str:
    first = purpose.split(". ")[0].rstrip(".")
    return f"{role}: {first[:88]}"


def main(argv: list[str]) -> int:
    profiles = load_profiles()
    show_denied = "--denied" in argv
    print(f"{'item':62} {'kind':14} {'routed to':34} why")
    print("-" * 132)
    for label, source, plan in SAMPLES:
        kind = kind_for_source(source)
        channels = route_content(kind or "", profiles)
        why = " | ".join(_purpose_fragment(c, profiles[c].purpose) for c in channels)
        print(f"{label[:62]:62} {str(kind):14} {', '.join(channels)[:34]:34} {why[:60]}")
        if show_denied:
            _kept, denied = filter_plan_by_profile(
                [
                    {"channel": ch, "alerts": [{"category": cat, "severity": sev}]}
                    for ch, cat, sev in plan
                ],
                profiles,
            )
            for row in denied:
                print(
                    f"    denied on {row['channel']:<16} category={row['category']:<14} reason={row['reason']}"
                )
    print()
    print(
        "logical channel → profile role:",
        ", ".join(f"{k}→{v}" for k, v in sorted(LOGICAL_PROFILE_CHANNELS.items())),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
