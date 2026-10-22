"""Build the channel lineup: per-channel stats, class and trust."""
from __future__ import annotations

import re
from datetime import date, timedelta

import classify
import extract

STALE_DAYS = 14
WARN_RE = re.compile(
    r"\b(scam|rugpull|rug pull|rugged|honeypot|phishing|drainer|blacklist|beware|warning|avoid this|fake token)\b",
    re.I,
)


def channel_stats(ch: dict, max_date: date) -> dict:
    """Derive activity, link, CA and referral statistics for one channel."""
    msgs = ch.get("msgs") or []
    ents = extract.extract(msgs)
    body = " ".join(m.get("t") or "" for m in msgs).lower()
    n = len(msgs)
    dates = [m.get("d") for m in msgs if m.get("d")]
    last = max(dates) if dates else None
    last_d = date.fromisoformat(last) if last else None
    stale = last_d is None or (max_date - last_d) > timedelta(days=STALE_DAYS)
    return {
        "n_msg": n,
        "members": ch.get("members") or 0,
        "last_post": last,
        "stale": stale,
        "link_ratio": round(len(ents["links"]) / n, 3) if n else 0.0,
        "ref_frac": round(len(ents["refs"]) / n, 3) if n else 0.0,
        "ca_per_20": round(len(ents["cas"]) / n * 20, 2) if n else 0.0,
        "shill_frac": round((ch.get("n_shill") or 0) / n, 3) if n else 0.0,
        "alert_msgs": body.count("alert") + body.count("🚨"),
        "warn_msgs": sum(1 for m in msgs if WARN_RE.search(m.get("t") or "")),
        "referral_markers": sorted(set(ents["refs"]))[:5],
        "entities": ents,
    }


def _handle(ch: dict) -> str:
    return ch.get("username") or f"id:{ch.get('id')}"


def build_lineup(channels: list[dict]) -> list[dict]:
    """Return one lineup record per channel, ordered by subs desc."""
    all_dates = [m.get("d") for c in channels for m in (c.get("msgs") or []) if m.get("d")]
    max_date = date.fromisoformat(max(all_dates)) if all_dates else date.today()
    rows = []
    for ch in channels:
        st = channel_stats(ch, max_date)
        cls = classify.assign_class(ch, st)
        rows.append({
            "handle": _handle(ch),
            "title": ch.get("title"),
            "subs": st["members"],
            "class": cls,
            "trust": classify.score_trust(ch, cls, st),
            "last_post": st["last_post"],
            "link_ratio": st["link_ratio"],
            "ca_per_20_msgs": st["ca_per_20"],
            "referral_markers": st["referral_markers"],
            "label_raw": ch.get("label"),
            "restricted": bool(ch.get("restricted")),
            "type": ch.get("type"),
            "id": ch.get("id"),
            "_stats": st,
        })
    rows.sort(key=lambda r: (-r["subs"], r["handle"]))
    return rows
