"""Ranked lineup: news/research, shill monitoring, scam-alert sources."""
from __future__ import annotations

import math

NEWS = {"news_primary", "news_aggregator", "research"}


def _reach(subs: int, top: int) -> float:
    return math.log10(max(subs, 1)) / math.log10(max(top, 10))


def top_news_research(rows: list[dict], n: int = 10) -> list[dict]:
    pool = [r for r in rows if r["class"] in NEWS and not r["restricted"]]
    top_subs = max((r["subs"] for r in pool), default=10)
    for r in pool:
        act = r["_stats"]["n_msg"] / 20
        r["_score"] = round(0.60 * r["trust"] + 0.25 * act + 0.15 * _reach(r["subs"], top_subs), 3)
    out = sorted(pool, key=lambda r: (-r["_score"], -r["subs"]))[:n]
    return [{"rank": i, "handle": r["handle"], "title": r["title"], "class": r["class"],
             "subs": r["subs"], "trust": r["trust"], "last_post": r["last_post"],
             "score": r["_score"]} for i, r in enumerate(out, 1)]


def top_shill(rows: list[dict], offenders: list[dict], n: int = 10) -> list[dict]:
    off = {o["channel"]: o for o in offenders}
    pool = [r for r in rows if r["class"] == "shill_watch" and not r["restricted"]]
    for r in pool:
        o = off.get(r["handle"], {})
        r["_score"] = round(
            2.0 * o.get("co_push_events", 0) + 0.5 * o.get("affinity_weight", 0)
            + r["ca_per_20_msgs"] + 0.5 * _reach(r["subs"], 100000), 3)
    out = sorted(pool, key=lambda r: (-r["_score"], -r["subs"]))[:n]
    return [{"rank": i, "handle": r["handle"], "title": r["title"], "subs": r["subs"],
             "trust": r["trust"], "ca_per_20_msgs": r["ca_per_20_msgs"],
             "co_push_events": off.get(r["handle"], {}).get("co_push_events", 0),
             "score": r["_score"]} for i, r in enumerate(out, 1)]


def top_scam_alerts(rows: list[dict], n: int = 5) -> list[dict]:
    pool = [r for r in rows if r["class"] == "scam_alert" and not r["restricted"]]
    for r in pool:
        st = r["_stats"]
        r["_warn"] = round(st["warn_msgs"] / st["n_msg"], 2) if st["n_msg"] else 0.0
    out = sorted(pool, key=lambda r: (-r["trust"], -r["_warn"]))[:n]
    return [{"rank": i, "handle": r["handle"], "title": r["title"], "subs": r["subs"],
             "trust": r["trust"], "warn_ratio": r["_warn"],
             "last_post": r["last_post"]} for i, r in enumerate(out, 1)]
