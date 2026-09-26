#!/usr/bin/env python3
"""Wallet label inventory report (ClickHouse counts, printed to stdout)."""

from __future__ import annotations

from typing import Any

from .ch_client import ch_select

__all__ = ["show_report"]

_GROUP_QUERIES: tuple[tuple[str, str, int], ...] = (
    (
        "\n── By Category ──",
        "SELECT label_category, count() as cnt FROM wallet_labels GROUP BY label_category ORDER BY cnt DESC LIMIT 20",
        25,
    ),
    (
        "\n── By Source (top 15) ──",
        "SELECT source, count() as cnt FROM wallet_labels GROUP BY source ORDER BY cnt DESC LIMIT 15",
        40,
    ),
    ("\n── By Chain ──", "SELECT chain_id, count() as cnt FROM wallet_labels GROUP BY chain_id ORDER BY cnt DESC", 15),
    (
        "\n── By Threat Type ──",
        "SELECT threat_type, count() as cnt FROM scam_addresses GROUP BY threat_type ORDER BY cnt DESC LIMIT 10",
        25,
    ),
    (
        "\n── By Source ──",
        "SELECT source, count() as cnt FROM scam_addresses GROUP BY source ORDER BY cnt DESC LIMIT 10",
        40,
    ),
)

_TOTALS: tuple[tuple[str, str], ...] = (
    ("\n📊 wallet_labels total", "SELECT count() FROM wallet_labels"),
    ("\n📊 scam_addresses total", "SELECT count() FROM scam_addresses"),
    (
        "\n📈 scam_addresses added in last 7 days",
        "SELECT count() FROM scam_addresses WHERE first_seen_at >= now() - INTERVAL 7 DAY",
    ),
    (
        "\n📈 wallet_labels added in last 7 days",
        "SELECT count() FROM wallet_labels WHERE loaded_at >= now() - INTERVAL 7 DAY",
    ),
)


def _scalar(ch: Any, query: str) -> int:
    """Run a single-value count query, 0 when nothing came back."""
    result = ch_select(ch, query)
    return result[0][0] if result else 0


def _print_group(ch: Any, title: str, query: str, width: int) -> None:
    """Print one grouped-count section."""
    print(title)
    for row in ch_select(ch, query) or []:
        print(f"  {row[0]!s:<{width}d} {row[1]:>10,}")


async def show_report(ch: Any) -> None:
    """Display current wallet label statistics."""
    if not ch:
        print("ClickHouse unavailable - cannot show report")
        return

    print("\n" + "=" * 70)
    print("WALLET LABEL INVENTORY REPORT")
    print("=" * 70)

    try:
        for title, query in _TOTALS:
            print(f"{title}: {_scalar(ch, query):,}")
        for title, query, width in _GROUP_QUERIES:
            _print_group(ch, title, query, width)
    except Exception as e:
        print(f"Report generation failed: {e}")

    print("=" * 70)
