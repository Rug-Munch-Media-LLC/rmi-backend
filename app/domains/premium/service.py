"""Glue between the unified scanner and the premium derivations.

One place decides what "the facts for this token" means, so the export
endpoint, the watch cycle and the tests all derive the same graph from the
same payload instead of each assembling it slightly differently.

The scan is requested at the cheapest tier that still reports holders and
authorities; premium modules are read when a caller's scan already ran them,
never re-purchased here.
"""

from __future__ import annotations

from typing import Any

from app.domains.premium import payload_facts
from app.domains.premium.bundle_graph import build_bundle_graph
from app.domains.premium.cohorts import cohort_rollup
from app.domains.premium.snapshot import snapshot_from_scan


def labels_for(wallets: set[str]) -> dict[str, Any]:
    """KOL identities among these wallets, from the bot's identity store.

    ``pnl_sol`` is whatever the store knows — currently ``None`` because the
    bot DB does not track per-KOL PnL. It is passed through as ``None`` on
    purpose: a zero would read as "broke even".
    """
    try:
        from app.databus.kol_bot_store import load_scorecard

        entries = load_scorecard(limit=500).get("scorecard") or []
    except Exception:  # the KOL store is optional; no labels is a valid answer
        return {}
    return {
        str(entry["wallet"]): {
            "handle": entry.get("handle"),
            "x_url": f"https://x.com/{entry['handle']}" if entry.get("handle") else None,
            "pnl_sol": entry.get("profit"),
        }
        for entry in entries
        if entry.get("wallet") in wallets
    }


def derive(scan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(snapshot, bundle_graph, cohort_rollup)`` for one scan payload."""
    snapshot = snapshot_from_scan(scan)
    holders = payload_facts.holders(scan)
    buyers = payload_facts.first_buyers(scan)
    wallets = {row["wallet"] for row in [*holders, *buyers]}
    graph = build_bundle_graph(
        token=snapshot["token"],
        chain=snapshot["chain"],
        wallets=sorted(wallets),
        edges=payload_facts.edges(scan),
        holdings=payload_facts.holdings(scan),
        total_supply=scan.get("total_supply"),
    )
    return snapshot, graph, cohort_rollup(holders, buyers, labels_for(wallets))


async def scan_payload(token: str, chain: str) -> dict[str, Any]:
    """Run the scanner and return it in the dict shape the exporters expect."""
    from app.features import get_token_scanner
    from app.routers.unified_scanner_router import _format_scan_response

    result = await get_token_scanner().scan(address=token, chain=chain, tier="free", scan_mode="basic")
    return _format_scan_response(result, scan_mode="basic")
