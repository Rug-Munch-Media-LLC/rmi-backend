"""Pure cluster/funding summarisers for page context (no I/O)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.api.v1.assistant.sources_rugmaps import _addr, _cluster_fields, _num, _pct

_FUNDING_KEYS = ("funding_source", "funded_by", "first_funder", "funder")


def _funding(node: dict[str, Any], label: dict[str, Any]) -> str:
    for source in (node, label):
        for key in _FUNDING_KEYS:
            if source.get(key):
                return str(source[key])
    return ""


def _behavior(members: list[dict[str, Any]]) -> str:
    buys = sum(_num(m, ("buys", "buy_count")) for m in members)
    sells = sum(_num(m, ("sells", "sell_count")) for m in members)
    if buys == 0 and sells == 0:
        return "unknown"
    if sells > 0 and buys / max(sells, 1) < 0.3:
        return "wash_trade"
    if buys > 0 and sells == 0:
        return "hold" if len(members) > 1 else "sniper"
    return "active"


def _group(holders: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for holder in holders:
        addr = _addr(holder)
        label = labels.get(addr.lower(), {})
        key = _cluster_fields(holder) or _cluster_fields(label) or _funding(holder, label)
        gid = f"cluster:{key}" if key else f"solo:{addr}"
        groups.setdefault(gid, []).append(holder)
    return list(groups.values())


def _cluster_row(members: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    addrs = [_addr(m) for m in members]
    sources: dict[str, int] = defaultdict(int)
    for member in members:
        source = _funding(member, labels.get(_addr(member).lower(), {}))
        if source:
            sources[source] += 1
    return {
        "cluster_id": addrs[0] if addrs else "",
        "size": len(members),
        "holders": addrs[:20],
        "total_holder_pct": round(sum(_pct(m) for m in members), 6),
        "behavior": _behavior(members),
        "funding_sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])[:3]),
    }


def summarize_clusters(
    holders: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Group holders into clusters; returns (rows, largest_cluster)."""
    if not holders:
        return [], None
    rows = [_cluster_row(members, labels) for members in _group(holders, labels)]
    rows.sort(key=lambda row: row["total_holder_pct"], reverse=True)
    return rows, rows[0]


def funding_summary(holders: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    pcts: dict[str, float] = defaultdict(float)
    for holder in holders:
        source = _funding(holder, labels.get(_addr(holder).lower(), {})) or "unknown"
        counts[source] += 1
        pcts[source] += _pct(holder)
    top = [
        {"source": key, "holders": count, "total_holder_pct": round(pcts[key], 6)}
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    ]
    return {"distinct_sources": len(counts), "top_sources": top, "holders_analyzed": len(holders)}


def holder_summary(holders: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pcts = sorted((_pct(h) for h in holders), reverse=True)
    return {
        "holders_analyzed": len(holders),
        "top10_holder_pct": round(sum(pcts[:10]), 6),
        "labeled_holders": len(labels),
        "basis": "rugmaps.get_token_holders",
    }


def select_cluster(rows: list[dict[str, Any]], cluster: str | None) -> dict[str, Any] | None:
    """Pick the requested cluster, else the largest one."""
    if not rows:
        return None
    if not cluster:
        return rows[0]
    return next((row for row in rows if row["cluster_id"] == cluster), None)
