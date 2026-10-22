"""Channel role classification and trust scoring (deterministic, data-driven)."""
from __future__ import annotations

WHALE_KW = ("whale", "🐳", "smart money", "wallet alert")
WARN_KW = (
    "scam", "rug", "rugpull", "honeypot", "avoid", "warn", "phishing",
    "blacklist", "drainer", "fake token", "stolen", "exploit",
)
SHILL_KW = ("buy now", " ape", "100x", "moonshot", "gem ", "send it", "next 100x", "lfg")
ALERT_KW = ("scam", "rug", "security", "alert", "sniff", "dethective", "honeypot", "phishing")


def _text(ch: dict) -> str:
    return f"{ch.get('title') or ''} {(ch.get('description') or '')}".lower()


def _ratio(text: str, kws: tuple[str, ...]) -> float:
    low = text.lower()
    return 1.0 if any(k in low for k in kws) else 0.0


def assign_class(ch: dict, st: dict) -> str:
    """Map collected label + observed behaviour to a lineup class."""
    lab = ch.get("label") or "unknown"
    body = " ".join(m.get("t") or "" for m in (ch.get("msgs") or []))
    meta = _text(ch)
    if ch.get("restricted"):
        return "dead"
    if _ratio(meta, WHALE_KW) and st["alert_msgs"]:
        return "whale_alert"
    warn_ratio = st["warn_msgs"] / st["n_msg"] if st["n_msg"] else 0.0
    if warn_ratio >= 0.4 or (warn_ratio >= 0.05 and _ratio(meta, ALERT_KW)):
        return "scam_alert"
    if lab == "news":
        return "news_aggregator" if st["link_ratio"] >= 0.6 else "news_primary"
    if lab == "alpha/calls":
        return "shill_watch" if st["shill_frac"] >= 0.25 or st["ca_per_20"] >= 2 else "research"
    if lab == "scam-shiller":
        warn = sum(1 for k in WARN_KW if k in body.lower())
        if warn >= 3 and st["ca_per_20"] < 1.5:
            return "scam_alert"
        return "shill_watch"
    if lab in ("trading/perp", "education"):
        return "research"
    if lab == "memes":
        return "memes"
    if lab == "own":
        return "community"
    if lab == "noise":
        return "dead"
    if st["stale"] or not st["n_msg"]:
        return "dead"
    if st["ca_per_20"] >= 2 or st["shill_frac"] >= 0.3:
        return "shill_watch"
    if st["link_ratio"] >= 0.6 and st["members"] >= 5000:
        return "news_aggregator"
    return "community"


def score_trust(ch: dict, cls: str, st: dict) -> float:
    """0-1 trust: verified/news add, shill+referral+dead subtract."""
    t = 0.5
    t += 0.15 if ch.get("verified") else 0.0
    t += 0.12 if cls in ("news_primary", "research") else 0.0
    t += 0.08 if cls == "scam_alert" else 0.0
    t += 0.10 if cls == "community" and (ch.get("label") == "own") else 0.0
    t -= 0.30 if cls == "shill_watch" else 0.0
    t -= 0.35 if cls == "dead" else 0.0
    t -= min(0.20, st["ref_frac"] * 0.25)
    t -= min(0.15, st["ca_per_20"] * 0.03)
    t -= 0.10 if (ch.get("scam") or ch.get("fake")) else 0.0
    t -= 0.25 if ch.get("restricted") else 0.0
    if st["n_msg"] == 0:
        t -= 0.20
    return round(max(0.0, min(1.0, t)), 2)
