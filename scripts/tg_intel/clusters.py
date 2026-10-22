"""Cluster shill channels by shared-token / co-push affinity."""
from __future__ import annotations

from collections import defaultdict


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _graph_edges(copush_pairs: list[dict], aff_pairs: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for p in copush_pairs:
        key = tuple(sorted((p["a"], p["b"])))
        merged[key] = {"a": key[0], "b": key[1], "copush": p["weight"],
                       "shared": 0, "tokens": list(p["tokens"])}
    for p in aff_pairs:
        key = tuple(sorted((p["a"], p["b"])))
        rec = merged.setdefault(key, {"a": key[0], "b": key[1], "copush": 0,
                                      "shared": 0, "tokens": []})
        rec["shared"] = p["weight"]
        rec["tokens"] = sorted(set(rec["tokens"]) | set(p["shared"]))
    return list(merged.values())


def build_clusters(copush_pairs: list[dict], aff_pairs: list[dict], edges: list[dict]) -> dict:
    """Return rings (components) with members, top tokens and one quote each."""
    links = _graph_edges(copush_pairs, aff_pairs)
    uf = _UF()
    for l in links:
        uf.union(l["a"], l["b"])
    members: dict[str, list[str]] = defaultdict(list)
    for ch in {e["channel"] for e in edges} | {x for l in links for x in (l["a"], l["b"])}:
        members[uf.find(ch)].append(ch)

    rings = []
    for root, chans in members.items():
        if len(chans) < 2:
            continue
        chans = sorted(chans)
        tk = defaultdict(int)
        for e in edges:
            if e["channel"] in chans:
                tk[e["token"]] += 1
        shared = defaultdict(set)
        for e in edges:
            if e["channel"] in chans:
                shared[e["token"]].add(e["channel"])
        co = sorted((t for t, cs in shared.items() if len(cs) > 1),
                    key=lambda t: -tk[t])
        pair_w = sum(l["copush"] + l["shared"] for l in links
                     if l["a"] in chans and l["b"] in chans)
        ev = sorted(
            (e for e in edges if e["channel"] in chans and e["token"] in set(co or tk)),
            key=lambda e: (not e["ca"], -e["trust"], e["ts"]),
        )
        rings.append({
            "ring": None,
            "members": chans,
            "size": len(chans),
            "shared_tokens": co[:5],
            "top_tokens": [{"token": t, "mentions": n} for t, n in
                           sorted(tk.items(), key=lambda z: -z[1])[:5]],
            "link_weight": pair_w,
            "confidence": "strong" if any(l["copush"] >= 2 for l in links
                                          if l["a"] in chans and l["b"] in chans) else "weak",
            "evidence": ({"channel": ev[0]["channel"], "ts": ev[0]["ts"],
                          "token": ev[0]["ticker"] or ev[0]["token"],
                          "quote": ev[0]["quote"]} if ev else None),
        })
    rings.sort(key=lambda r: (-r["size"], -r["link_weight"]))
    for i, r in enumerate(rings, 1):
        r["ring"] = f"RING-{i:02d}"
    clustered = {m for r in rings for m in r["members"]}
    return {
        "rings": rings,
        "clustered_channels": len(clustered),
        "unclustered": sorted({e["channel"] for e in edges} - clustered),
    }
