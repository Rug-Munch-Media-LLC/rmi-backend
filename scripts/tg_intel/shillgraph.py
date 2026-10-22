"""Shill graph: channel -> token edges, co-push, affinity, first movers."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import extract

PUSHER_CLASSES = {"shill_watch", "memes"}
WINDOW_24H = 1   # day-granularity data: |delta| <= 1 day is a 24h window
WINDOW_7D = 7


def _aliases(messages: list[dict]) -> dict[str, str]:
    """ticker -> chain:ca for messages that name both."""
    out: dict[str, str] = {}
    for m in messages:
        t = m.get("t") or ""
        ents = extract.extract([m])
        cas = [k for k in ents["tokens"] if ":" in k]
        for tick in ents["tickers"]:
            if cas:
                out.setdefault(tick, cas[0])
    return out


def build_edges(rows: list[dict], channels: dict[int, dict]) -> list[dict]:
    """One edge per (channel, token) occurrence with date and quote."""
    edges: list[dict] = []
    for r in rows:
        if r["class"] not in PUSHER_CLASSES or r["restricted"]:
            continue
        ch = channels[r["id"]]
        alias = _aliases(ch.get("msgs") or [])
        for m in ch.get("msgs") or []:
            ts = m.get("d")
            body = (m.get("t") or "").strip()
            if not ts or not body:
                continue
            for key, rec in extract.extract([m])["tokens"].items():
                tick = rec["ticker"]
                canon = alias.get(tick or "", key) if ":" not in key else key
                edges.append({
                    "channel": r["handle"], "token": canon, "ticker": tick,
                    "ca": rec["ca"], "chain": rec["chain"], "ts": ts,
                    "quote": body[:220], "trust": r["trust"],
                })
    return edges


def _by_token(edges: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        g[e["token"]].append(e)
    return g


def _day(ts: str) -> date:
    return date.fromisoformat(ts)


def co_push(edges: list[dict]) -> list[dict]:
    """Tokens pushed by >=2 distinct channels inside a 24h window."""
    out = []
    for token, es in _by_token(edges).items():
        es = sorted(es, key=lambda e: e["ts"])
        for i, a in enumerate(es):
            group = {a["channel"]: a}
            for b in es[i + 1:]:
                if (_day(b["ts"]) - _day(a["ts"])).days > WINDOW_24H:
                    break
                group.setdefault(b["channel"], b)
            if len(group) >= 2:
                out.append({
                    "token": token, "ticker": a["ticker"], "ca": a["ca"],
                    "chain": a["chain"], "channels": sorted(group), "count": len(group),
                    "window_start": min(e["ts"] for e in group.values()),
                    "window_end": max(e["ts"] for e in group.values()),
                })
                break
    return sorted(out, key=lambda x: (-x["count"], x["window_start"]))


def affinity(edges: list[dict]) -> list[dict]:
    """Channel pairs sharing >=3 tokens where both pushed within 7 days."""
    per_ch: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for e in edges:
        per_ch[e["channel"]][e["token"]].append(e["ts"])
    pairs = []
    chans = sorted(per_ch)
    for i, a in enumerate(chans):
        for b in chans[i + 1:]:
            shared = []
            for tok in set(per_ch[a]) & set(per_ch[b]):
                if any(abs((_day(x) - _day(y)).days) <= WINDOW_7D
                       for x in per_ch[a][tok] for y in per_ch[b][tok]):
                    shared.append(tok)
            if len(shared) >= 3:
                pairs.append({"a": a, "b": b, "shared": sorted(shared),
                              "weight": len(shared)})
    return sorted(pairs, key=lambda p: -p["weight"])


def first_movers(edges: list[dict]) -> dict[str, dict]:
    """Earliest pusher per token (ties broken by higher trust, then subs)."""
    out = {}
    for token, es in _by_token(edges).items():
        best = sorted(es, key=lambda e: (e["ts"], -e["trust"]))[0]
        out[token] = {"channel": best["channel"], "ts": best["ts"],
                      "ticker": best["ticker"], "quote": best["quote"][:160]}
    return out


def repeat_offenders(edges: list[dict], cops: list[dict], pairs: list[dict]) -> list[dict]:
    """Channels recurring in co-pushes / affinity rings."""
    hits: dict[str, int] = defaultdict(int)
    for c in cops:
        for ch in c["channels"]:
            hits[ch] += 1
    aff: dict[str, int] = defaultdict(int)
    for p in pairs:
        aff[p["a"]] += p["weight"]
        aff[p["b"]] += p["weight"]
    vol: dict[str, int] = defaultdict(int)
    for e in edges:
        vol[e["channel"]] += 1
    out = []
    for ch in set(hits) | set(aff):
        if hits[ch] >= 2 or aff.get(ch, 0) >= 6:
            out.append({"channel": ch, "co_push_events": hits[ch],
                        "affinity_weight": aff.get(ch, 0), "token_mentions": vol[ch]})
    return sorted(out, key=lambda r: (-r["co_push_events"], -r["affinity_weight"]))


def copush_pairs(cops: list[dict]) -> list[dict]:
    """Channel pairs that amplified the same token inside 24h."""
    agg: dict[tuple[str, str], dict] = {}
    for c in cops:
        chs = sorted(c["channels"])
        for i, a in enumerate(chs):
            for b in chs[i + 1:]:
                rec = agg.setdefault((a, b), {"a": a, "b": b, "weight": 0, "tokens": []})
                rec["weight"] += 1
                rec["tokens"].append(c["ticker"] or c["token"])
    return sorted(agg.values(), key=lambda r: -r["weight"])
