"""Build the channel lineup + shill graph artifacts from collected TG data."""
from __future__ import annotations

import itertools
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clusters
import lineup
import rank
import scanner_check
import shillgraph

SOURCES = ["/tmp/tg_final.json", "/tmp/tg_labeled.json", "/tmp/tg_scout/final.json"]
DATA = "/srv/work/repos/rmi-backend/data"
KEEP = ("handle", "title", "subs", "class", "trust", "last_post", "link_ratio",
        "ca_per_20_msgs", "referral_markers", "label_raw", "restricted", "type", "id")


def load() -> tuple[list[dict], str, list[str]]:
    missing: list[str] = []
    for p in SOURCES:
        if not os.path.exists(p):
            missing.append(p)
            continue
        try:
            return json.load(open(p)), p, missing
        except ValueError:
            missing.append(p + " (invalid json)")
    raise SystemExit("no usable source: " + ",".join(missing))


def relaxed_pairs(edges: list[dict], days: int = 7) -> list[dict]:
    """>=1 shared token within `days` (fallback for the strict >=3 rule)."""
    per: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for e in edges:
        per[e["channel"]][e["token"]].append(e["ts"])
    out = []
    for a, b in itertools.combinations(sorted(per), 2):
        shared = [t for t in set(per[a]) & set(per[b])
                  if any(abs((date.fromisoformat(x) - date.fromisoformat(y)).days) <= days
                         for x in per[a][t] for y in per[b][t])]
        if shared:
            out.append({"a": a, "b": b, "shared": sorted(shared), "weight": len(shared)})
    return sorted(out, key=lambda r: -r["weight"])


def main() -> None:
    chans, src, missing = load()
    rows = lineup.build_lineup(chans)
    by_id = {c["id"]: c for c in chans}
    edges = shillgraph.build_edges(rows, by_id)
    cops = shillgraph.co_push(edges)
    cp_pairs = shillgraph.copush_pairs(cops)
    strict = shillgraph.affinity(edges)
    relaxed = relaxed_pairs(edges)
    aff_for_rings = relaxed if not strict else strict
    fm = shillgraph.first_movers(edges)
    off = shillgraph.repeat_offenders(edges, cops, aff_for_rings)
    rings = clusters.build_clusters(cp_pairs, aff_for_rings, edges)
    scan = scanner_check.check_co_pushed(cops, n=5, fallback=edges)

    os.makedirs(DATA, exist_ok=True)
    meta = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": src, "missing_sources": missing,
        "channels": len(rows), "public_preview_only": True,
        "limits": [
            "<=20 messages per channel were collected; ~100 requested was unavailable",
            "message dates are day-granularity: a 24h window means |delta| <= 1 day",
            "strict affinity (>=3 shared tokens/7d) yields no pairs in this sample; "
            "clusters use the >=1 shared/7d fallback and are confidence-labelled",
        ],
    }
    records = [{k: r[k] for k in KEEP} for r in rows]
    json.dump(records, open(os.path.join(DATA, "channel_lineup.json"), "w"), indent=2)

    graph = {
        "meta": meta,
        "counts_by_role": dict(Counter(r["class"] for r in records)),
        "excluded_restricted": [{"handle": r["handle"], "title": r["title"]}
                                for r in records if r["restricted"]],
        "edges": edges,
        "edge_count": len(edges),
        "co_push": cops,
        "affinity": {"strict_rule": ">=3 shared tokens within 7d", "strict_pairs": strict,
                     "fallback_rule": ">=1 shared token within 7d", "fallback_pairs": relaxed},
        "copush_pairs": cp_pairs,
        "clusters": rings,
        "first_movers": fm,
        "repeat_offenders": off,
        "scanner_crosscheck": scan,
    }
    json.dump(graph, open(os.path.join(DATA, "shill_graph.json"), "w"), indent=2)
    json.dump({
        "meta": meta,
        "top_news_research": rank.top_news_research(rows, 10),
        "top_shill_monitor": rank.top_shill(rows, off, 10),
        "top_scam_alerts": rank.top_scam_alerts(rows, 5),
    }, open(os.path.join(DATA, "channel_rankings.json"), "w"), indent=2)

    print(json.dumps({
        "channels": len(records), "roles": dict(Counter(r["class"] for r in records)),
        "edges": len(edges), "co_push": len(cops), "strict_pairs": len(strict),
        "fallback_pairs": len(relaxed), "copush_pairs": len(cp_pairs),
        "rings": len(rings["rings"]), "offenders": len(off),
        "scanner_healthy": scan["scanner_healthy"],
        "scanned": len(scan["checked"]), "usable_scans": sum(1 for c in scan["checked"] if c["usable"]),
        "queued": len(scan["queued"]),
    }, indent=2))


if __name__ == "__main__":
    main()
