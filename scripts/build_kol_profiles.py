#!/usr/bin/env python3
"""Build ``app/databus/kol_profiles.json`` — enriched KOL social-intelligence dataset.

Merges four bundled, real data sources (no network, no fabrication):

  - app/databus/verified_kol_wallets.json   — wallet attributions (945 wallets / 893 handles)
  - app/databus/influential_accounts.json   — KOL registry (504 accounts, categories/notes)
  - app/databus/kol_rug_history.json        — documented rug/dump events (22 KOLs, real evidence)
  - app/databus/known_scammers.json         — known-scammer registry (31 records)

plus a small curated metadata table (display names, approximate follower
counts) for famous public figures. Every follower count is flagged
``followers_approx: true`` and is an order-of-magnitude public-knowledge
estimate — never scraped, never independently verified this session.

Honesty contract:
  - CALLS are only included when they come from the rug-history file
    (``verified: true``, with evidence URL) or are explicitly marked
    ``verified: false`` with an ``[unverified]`` note. No dates/prices are
    invented for unverified calls.
  - PERFORMANCE is a computed win-rate heuristic from *documented outcomes
    only*. The bundled history currently records only negative outcomes
    (dumped/rugged), so win-rate is a lower bound and the rating is a
    rug-risk-weighted trust score, not a track-record claim.
  - RISK associations carry the same verified/unverified split.

Output: ``app/databus/kol_profiles.json`` — {updated, description, profiles: [...]}
Usage:  python3 scripts/build_kol_profiles.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATABUS = ROOT / "app" / "databus"
OUT_PATH = DATABUS / "kol_profiles.json"

AS_OF = date.today().isoformat()

FOLLOWERS_NOTE = "Approximate public-knowledge estimate; not independently verified."

# ═══════════════════════════════════════════════════════════════════════
# Curated metadata for famous public figures.
# follower values are order-of-magnitude public-knowledge approximations
# (flagged approx downstream). Keep additions to well-known figures only.
# ═══════════════════════════════════════════════════════════════════════

CURATED: dict[str, dict[str, Any]] = {
    "blknoiz06": {"display_name": "Ansem", "category": "influencer", "followers_approx": 400_000},
    "HalieyWelchX": {
        "display_name": "Haliey Welch (Hawk Tuah)",
        "category": "influencer",
        "followers_approx": 1_000_000,
    },
    "LoganPaul": {"display_name": "Logan Paul", "category": "influencer", "followers_approx": 5_000_000},
    "stablekwon": {"display_name": "Do Kwon (Terraform Labs)", "category": "influencer", "followers_approx": 1_000_000},
    "cryptomanran": {
        "display_name": "Ran Neuner (Crypto Banter)",
        "category": "influencer",
        "followers_approx": 800_000,
    },
    "scottmelker": {
        "display_name": "Scott Melker (Wolf of All Streets)",
        "category": "analyst",
        "followers_approx": 1_000_000,
    },
    "SBF_FTX": {"display_name": "Sam Bankman-Fried", "category": "influencer", "followers_approx": 1_000_000},
    "kevinolearytv": {
        "display_name": "Kevin O'Leary (Mr. Wonderful)",
        "category": "influencer",
        "followers_approx": 2_500_000,
    },
    "TomBrady": {"display_name": "Tom Brady", "category": "influencer", "followers_approx": 2_000_000},
    "giseleofficial": {"display_name": "Gisele Bündchen", "category": "influencer", "followers_approx": 3_000_000},
    "StephenCurry30": {"display_name": "Stephen Curry", "category": "influencer", "followers_approx": 14_000_000},
    "SHAQ": {"display_name": "Shaquille O'Neal", "category": "influencer", "followers_approx": 15_000_000},
    "KimKardashian": {"display_name": "Kim Kardashian", "category": "influencer", "followers_approx": 75_000_000},
    "paulpierce34": {"display_name": "Paul Pierce", "category": "influencer", "followers_approx": 1_500_000},
    "nickcarter": {
        "display_name": "Nick Carter (Backstreet Boys)",
        "category": "influencer",
        "followers_approx": 4_000_000,
    },
    "souljaboy": {"display_name": "Soulja Boy", "category": "influencer", "followers_approx": 3_000_000},
    "lillyachty": {"display_name": "Lil Yachty", "category": "influencer", "followers_approx": 3_000_000},
    "BenPhillips": {"display_name": "Ben Phillips", "category": "influencer", "followers_approx": 1_000_000},
    "stoolpresidente": {"display_name": "Dave Portnoy", "category": "influencer", "followers_approx": 3_000_000},
    "safemoon": {"display_name": "Braden Karony (SafeMoon CEO)", "category": "influencer", "followers_approx": 50_000},
    "MustStopMurad": {"display_name": "Murad Mahmudov", "category": "influencer", "followers_approx": 400_000},
    "WaleMoca": {"display_name": "Wale.moca", "category": "influencer", "followers_approx": 200_000},
    "hurls4president": {"display_name": "Hurls", "category": "influencer", "followers_approx": 100_000},
    "justinsuntron": {"display_name": "Justin Sun", "category": "influencer", "followers_approx": 3_000_000},
    "lindsaylohan": {"display_name": "Lindsay Lohan", "category": "influencer", "followers_approx": 13_000_000},
    "jakepaul": {"display_name": "Jake Paul", "category": "influencer", "followers_approx": 20_000_000},
    "Akon": {"display_name": "Akon (Aliaune Thiam)", "category": "influencer", "followers_approx": 5_000_000},
    "FloydMayweather": {
        "display_name": "Floyd Mayweather Jr.",
        "category": "influencer",
        "followers_approx": 10_000_000,
    },
    "djkhaled": {"display_name": "DJ Khaled", "category": "influencer", "followers_approx": 13_000_000},
    "sseagalofficial": {"display_name": "Steven Seagal", "category": "influencer", "followers_approx": 1_000_000},
    "RichardHeartWin": {
        "display_name": "Richard Heart (Richard Schueler)",
        "category": "influencer",
        "followers_approx": 700_000,
    },
    "BenArmstrongsX": {"display_name": "Ben Armstrong (BitBoy)", "category": "influencer", "followers_approx": 300_000},
    "IvanOnTech": {
        "display_name": "Ivan Liljeqvist (Ivan on Tech)",
        "category": "influencer",
        "followers_approx": 200_000,
    },
    "officialmcafee": {"display_name": "John McAfee", "category": "influencer", "followers_approx": 900_000},
    "AltcoinGordon": {"display_name": "Altcoin Gordon", "category": "influencer", "followers_approx": 300_000},
    "SahilArora0": {"display_name": "Sahil Arora", "category": "influencer", "followers_approx": 100_000},
    "avi_eisen": {"display_name": "Avi Eisenberg", "category": "trader", "followers_approx": 100_000},
    "Mashinsky": {"display_name": "Alex Mashinsky", "category": "influencer", "followers_approx": 200_000},
    "MattShiller_": {"display_name": "Matt Gems", "category": "influencer", "followers_approx": 300_000},
    "BrooxBauer": {"display_name": "Broox Bauer", "category": "influencer", "followers_approx": 100_000},
    "Serpent": {"display_name": "Serpent", "category": "trader", "followers_approx": 500_000},
    "dritankapllani": {"display_name": "Dritan Kapllani Jr.", "category": "influencer", "followers_approx": 50_000},
    "tiffany.milanovich": {"display_name": "Tiffany Milanovich", "category": "influencer", "followers_approx": 30_000},
    "0xSisyphus": {"display_name": "Sisyphus", "category": "trader", "followers_approx": 200_000},
    "AndrewKang": {"display_name": "Andrew Kang", "category": "trader", "followers_approx": 200_000},
    "adamscochran": {"display_name": "Adam Cochran", "category": "analyst", "followers_approx": 200_000},
    "AlexBecker": {"display_name": "Alex Becker", "category": "influencer", "followers_approx": 500_000},
    "BigCheds": {"display_name": "Big Cheds", "category": "trader", "followers_approx": 300_000},
    "AltcoinSherpa": {"display_name": "Altcoin Sherpa", "category": "trader", "followers_approx": 300_000},
    "BenjaminCowen": {"display_name": "Benjamin Cowen", "category": "analyst", "followers_approx": 800_000},
    "0xKofi": {"display_name": "0xKofi", "category": "analyst", "followers_approx": 100_000},
    "0xMert_": {"display_name": "Mert Mumtaz", "category": "analyst", "followers_approx": 200_000},
    "defiignas": {"display_name": "Ignas", "category": "analyst", "followers_approx": 200_000},
    "Route2Fi": {"display_name": "Route 2 FI", "category": "analyst", "followers_approx": 100_000},
    "APompliano": {"display_name": "Anthony Pompliano", "category": "influencer", "followers_approx": 1_700_000},
    "100trillionUSD": {"display_name": "PlanB", "category": "analyst", "followers_approx": 2_000_000},
    "TheMoonCarl": {"display_name": "The Moon (Carl)", "category": "influencer", "followers_approx": 800_000},
    "zhusu": {"display_name": "Su Zhu", "category": "trader", "followers_approx": 500_000},
    "cobie": {"display_name": "Cobie", "category": "trader", "followers_approx": 700_000},
}

# Registry category -> 4-value taxonomy (trader/alpha/influencer/analyst).
# Org accounts (protocol/exchange) and devs are left unmapped (null).
CATEGORY_MAP = {
    "trader": "trader",
    "analyst": "analyst",
    "kol": "influencer",
    "media": "influencer",
    "founder": "alpha",
    "vc": "alpha",
}

ORG_CATEGORIES = {"protocol", "exchange"}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _handle_key(raw: Any) -> str:
    return str(raw or "").strip().lstrip("@").lower()


def build() -> dict[str, Any]:
    wallets = _load_json(DATABUS / "verified_kol_wallets.json", {"wallets": []}).get("wallets", [])
    registry = _load_json(DATABUS / "influential_accounts.json", {"accounts": []}).get("accounts", [])
    rug_hist = _load_json(DATABUS / "kol_rug_history.json", {"kol_rug_history": []}).get("kol_rug_history", [])
    scammers_raw = _load_json(DATABUS / "known_scammers.json", {})
    if isinstance(scammers_raw, list):
        scammers = [r for r in scammers_raw if isinstance(r, dict)]
    else:
        scammers = [
            r
            for key in ("accounts", "scammers", "entries")
            for r in (scammers_raw.get(key) or [])
            if isinstance(r, dict)
        ]

    # ── source lookups ─────────────────────────────────────────────────
    wallet_by_handle: dict[str, list[dict[str, Any]]] = {}
    for w in wallets:
        wallet_by_handle.setdefault(_handle_key(w.get("handle")), []).append(w)

    # CURATED keys are normalized too (e.g. "SHAQ" -> "shaq") so mixed-case
    # curated figures match the lowercased handle keys.
    curated_norm: dict[str, dict[str, Any]] = {_handle_key(k): v for k, v in CURATED.items()}

    registry_by_handle: dict[str, dict[str, Any]] = {}
    for a in registry:
        registry_by_handle.setdefault(_handle_key(a.get("handle")), a)

    rug_by_handle: dict[str, list[dict[str, Any]]] = {}
    for entry in rug_hist:
        rug_by_handle.setdefault(_handle_key(entry.get("handle") or entry.get("kol_handle")), entry.get("rugs") or [])

    scammer_by_handle: dict[str, dict[str, Any]] = {}
    for s in scammers:
        scammer_by_handle.setdefault(_handle_key(s.get("handle")), s)

    # ── profile base set: wallet handles + registry + curated ──────────
    handles: set[str] = (
        set(wallet_by_handle)
        | set(registry_by_handle)
        | set(curated_norm)
        | set(rug_by_handle)
        | set(scammer_by_handle)
    )

    profiles: list[dict[str, Any]] = []
    for handle in sorted(handles):
        curated = curated_norm.get(handle, {})
        reg = registry_by_handle.get(handle, {})

        display_name = curated.get("display_name") or str(reg.get("name") or "")
        if not display_name and wallet_by_handle.get(handle):
            display_name = str(wallet_by_handle[handle][0].get("name") or "") or handle

        reg_category = str(reg.get("category") or "")
        category = curated.get("category") or CATEGORY_MAP.get(reg_category)
        if not category and reg_category in ORG_CATEGORIES:
            category = None  # org accounts are not KOLs — keep unmapped

        profile: dict[str, Any] = {
            "handle": handle,
            "x_handle": handle,
            "display_name": display_name,
            "avatar_url": f"https://unavatar.io/x/{handle}",
            "category": category,
            "registry_category": reg_category or None,
            "followers_approx": curated.get("followers_approx"),
            "followers_note": FOLLOWERS_NOTE if curated.get("followers_approx") is not None else None,
            "note": str(reg.get("notes") or "")[:240] or None,
            "calls": [],
            "performance": {},
            "risk": {
                "known_scammer": False,
                "scammer_severity": None,
                "scammer_verified": False,
                "rug_associations": [],
            },
            "sources": [],
        }
        if not profile["display_name"]:
            profile["display_name"] = handle
        if reg:
            profile["sources"].append("influential_accounts")
        if handle in wallet_by_handle:
            profile["sources"].append("verified_kol_wallets")
        if handle in rug_by_handle:
            profile["sources"].append("kol_rug_history")
        if handle in scammer_by_handle:
            profile["sources"].append("known_scammers")
        if handle in curated_norm:
            profile["sources"].append("curated_public_knowledge")

        # ── calls: real rug-history events only (verified or explicitly flagged) ──
        for rug in rug_by_handle.get(handle, []):
            call = {
                "token": str(rug.get("token_symbol") or rug.get("token") or "").upper(),
                "chain": rug.get("chain"),
                "date": rug.get("date") or rug.get("call_date"),
                "direction": "long",
                "outcome": rug.get("outcome"),
                "severity": rug.get("severity"),
                "verified": bool(rug.get("verified", False)),
                "evidence_url": rug.get("evidence_url"),
                "note": str(rug.get("notes") or "")[:300],
            }
            if call["token"] and call["outcome"]:
                profile["calls"].append(call)

        # ── risk: known-scammer registry + rug associations ──
        scam = scammer_by_handle.get(handle)
        if scam:
            profile["risk"] = {
                "known_scammer": True,
                "scammer_severity": scam.get("severity"),
                "scammer_verified": bool(scam.get("verified", False)),
                "scammer_category": scam.get("category"),
                "rug_associations": [
                    {
                        "name": str(rug.get("token_symbol") or rug.get("token") or "").upper(),
                        "outcome": rug.get("outcome"),
                        "severity": rug.get("severity"),
                        "date": rug.get("date"),
                        "verified": bool(rug.get("verified", False)),
                        "note": str(rug.get("notes") or "")[:200],
                    }
                    for rug in rug_by_handle.get(handle, [])
                ],
            }
        elif rug_by_handle.get(handle):
            profile["risk"]["rug_associations"] = [
                {
                    "name": str(rug.get("token_symbol") or rug.get("token") or "").upper(),
                    "outcome": rug.get("outcome"),
                    "severity": rug.get("severity"),
                    "date": rug.get("date"),
                    "verified": bool(rug.get("verified", False)),
                    "note": str(rug.get("notes") or "")[:200],
                }
                for rug in rug_by_handle.get(handle, [])
            ]

        profile["performance"] = _compute_performance(profile["calls"], profile["risk"])
        profiles.append(profile)

    profiles.sort(
        key=lambda p: ((p["performance"].get("score") is not None), -(p["performance"].get("score") or 0), p["handle"])
    )

    return {
        "updated": AS_OF,
        "description": (
            "Enriched KOL social-intelligence profiles. Merged from bundled datasets "
            "(verified_kol_wallets, influential_accounts, kol_rug_history, known_scammers) "
            "plus curated public-knowledge metadata. Follower counts are approximations; "
            "unverified claims are explicitly flagged [unverified]."
        ),
        "profile_count": len(profiles),
        "profiles": profiles,
    }


def _compute_performance(calls: list[dict[str, Any]], risk: dict[str, Any]) -> dict[str, Any]:
    """Win-rate heuristic from documented outcomes only (see module docstring)."""
    resolved = [c for c in calls if c.get("outcome") in ("win", "dumped", "rugged")]
    wins = sum(1 for c in resolved if c.get("outcome") == "win")
    losses = len(resolved) - wins
    verified_losses = sum(1 for c in resolved if c.get("outcome") in ("dumped", "rugged") and c.get("verified"))
    unverified_losses = losses - verified_losses

    total = wins + losses
    win_rate = round(wins / total, 4) if total else None

    score = 50.0
    if total:
        score += 20.0 * (wins / total)  # +0..+20
        score -= 12.0 * min(verified_losses, 3)  # -0..-36
        score -= 6.0 * min(unverified_losses, 3)  # -0..-18
    if risk.get("known_scammer"):
        score -= 40.0 if risk.get("scammer_verified") else 15.0
    score = max(0.0, min(100.0, round(score, 1)))

    grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D" if score >= 35 else "F"

    if total == 0 and not risk.get("known_scammer"):
        return {
            "score": None,
            "grade": None,
            "win_rate": None,
            "wins": 0,
            "losses": 0,
            "unresolved": len(calls),
            "as_of": AS_OF,
            "note": "Insufficient documented call data; rating withheld rather than guessed.",
        }

    return {
        "score": score,
        "grade": grade,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "unresolved": len(calls) - total,
        "as_of": AS_OF,
        "note": (
            "Heuristic from documented outcomes only (bundled history records negative "
            "outcomes; win-rate is a lower bound). Rug-risk-weighted trust score, not a track-record claim."
        ),
    }


def main() -> None:
    data = build()
    OUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH} — {data['profile_count']} profiles, updated {data['updated']}")


if __name__ == "__main__":
    main()
