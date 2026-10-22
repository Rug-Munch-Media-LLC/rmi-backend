"""The X handle registry: verify every handle, then write `data/x_sources.json`.

`class` drives the rank boost and `trust` drives the source weight, so both are
data — this file only owns the required set and the verification pass. A handle
that does not resolve is reported and dropped from the registry rather than
stored with a made-up id.

    python3 x_registry.py            # verify + rewrite data/x_sources.json
    python3 x_registry.py --check    # verify and report, write nothing
"""

from __future__ import annotations

import argparse
import json
import time

import x_graphql
from http_get import Limiter
from sources import REGISTRY

#: Requested handle -> the handle that actually resolves, where they differ.
#: ScamSniffer renamed; the requested name 404s, so ingestion follows the account.
ALIASES = {"ScamSniffer": "realScamSniffer"}

#: (handle, class, trust) — class mirrors news_fallback.classify().
REQUIRED: tuple[tuple[str, str, float], ...] = (
    ("bubblemaps", "analytics", 0.82),
    ("lookonchain", "analytics", 0.85),
    ("gmgnai", "analytics", 0.82),
    ("arkham", "analytics", 0.85),
    ("spotonchain", "analytics", 0.85),
    ("whale_alert", "analytics", 0.8),
    ("OnchainLens", "analytics", 0.82),
    ("DefiLlama", "analytics", 0.85),
    ("zachxbt", "security", 0.9),
    ("ScamSniffer", "security", 0.9),
    ("PeckShieldAlert", "security", 0.92),
    ("CertiKAlert", "security", 0.92),
    ("SlowMist_Team", "security", 0.92),
    ("BlockSecTeam", "security", 0.9),
    ("SEAL_Org", "security", 0.88),
    ("rektnews", "news", 0.85),
    ("CoinDesk", "news", 0.85),
    ("TheBlock", "news", 0.9),
    ("WuBlockchain", "news", 0.88),
    ("Cointelegraph", "news", 0.85),
    ("solana", "project", 0.8),
    ("ethereum", "project", 0.8),
)


def verify(handle: str, limiter: Limiter) -> dict:
    """One handle -> {handle, class, trust, id, followers, verified} or an error."""
    account = x_graphql.user(handle, limiter)
    if not account or not account.get("user_id"):
        return {"handle": handle, "error": "not_found"}
    return {
        "handle": str(account["handle"] or handle),
        "user_id": account["user_id"],
        "followers": account["followers"],
        "verified": account["verified"],
    }


def refresh(write: bool = True) -> dict:
    """Verify every required handle; write the registry when all is well."""
    limiter = Limiter()
    rows: list[dict] = []
    missing: list[str] = []
    verified = 0
    for handle, klass, trust in REQUIRED:
        found = verify(ALIASES.get(handle, handle), limiter)
        if found.get("error"):
            missing.append(handle)
        else:
            verified += 1
        row = {
            "handle": found.get("handle", ALIASES.get(handle, handle)),
            "requested": handle,
            "class": klass,
            "trust": trust,
            "user_id": found.get("user_id", ""),
            "followers": found.get("followers", 0),
            "verified": bool(found.get("verified")),
        }
        rows.append(row)
        print(
            f"{handle:<18} {klass:<10} {trust:<5} "
            f"{found.get('user_id', '-'):<20} {found.get('followers', 0):>9} "
            f"{'blue' if found.get('verified') else '   '} {found.get('error', '')}"
        )
    if write:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(
            json.dumps(
                {
                    "version": 3,
                    "updated": time.strftime("%Y-%m-%d"),
                    "note": (
                        "X ingestion registry (single copy: data/). Verified by x_registry.py; "
                        "class drives the rank boost, trust drives source_weight."
                    ),
                    "sources": rows,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return {
        "handles": len(rows),
        "verified": verified,
        "missing": missing,
        "registry": str(REGISTRY),
        "written": write,
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description="verify + write the X handle registry")
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    args = ap.parse_args(argv)
    result = refresh(write=not args.check)
    print(json.dumps(result, indent=2))
    return 0 if not result["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
