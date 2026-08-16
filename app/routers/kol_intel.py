"""KOL Social-Intelligence API Router (read-only).

Thin router wrapper over the existing databus KOL modules — no re-implemented
logic, just composition + graceful degradation:

  GET  /api/v1/kol/scammers         — Known scammers (filter by severity)
  GET  /api/v1/kol/kol/{handle}     — KOL profile: score + rug history + wallets
  GET  /api/v1/kol/leaderboard      — KOL trust-score leaderboard
  GET  /api/v1/kol/wallets/{handle} — Attributed wallets for a KOL
  GET  /api/v1/kol/cross-chain/{handle} — Cross-chain wallet links
  GET  /api/v1/kol/shill-signals    — Detected shill signals (count + alerts)
  GET  /api/v1/kol/scammer-calls    — Captured scammer-call posts (store-backed)
  GET  /api/v1/kol/profiles         — Enriched KOL profiles (avatar/category/calls/rating)
  GET  /api/v1/kol/profiles/{handle} — Full enriched profile + wallets + calls
  GET  /api/v1/kol/health           — Service health

Every handler is wrapped so an upstream databus failure degrades to a
structured ``{"error": ..., "detail": ...}`` response (404 for unknown
handles, 200 with empty collections when the data source is empty).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.core.logging import get_logger
from app.databus import kol_scoring as ks, kol_wallet_linker as kwl, kol_wallets as kw, shill_detector as sd
from app.domains.shill_alert.call_store import CallStore

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/kol", tags=["social-intel"])

_KNOWN_SCAMMERS_PATH = Path(__file__).resolve().parent.parent / "databus" / "known_scammers.json"
_KOL_PROFILES_PATH = Path(__file__).resolve().parent.parent / "databus" / "kol_profiles.json"

# Lazy file-backed store: the constructor does no I/O, so this is safe at
# import time. Reads degrade to empty when the file is missing/corrupt.
_CALL_STORE = CallStore()


# ═══════════════════════════════════════════════════════════════════════
# Response models
# ═══════════════════════════════════════════════════════════════════════


class HealthResponse(BaseModel):
    status: str
    scammers: int
    wallets: int


class ErrorResponse(BaseModel):
    error: str
    detail: str


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers (pure, file-backed — no network)
# ═══════════════════════════════════════════════════════════════════════


def _load_scammer_records() -> list[dict[str, Any]]:
    """Load full known-scammer records from ``known_scammers.json``.

    Supports ``{"accounts": [...]}``, ``{"handles": [...]}`` or a bare list.
    Falls back to the registry helper (bare handle strings) when the file is
    unreadable. Never raises.
    """
    try:
        raw = json.loads(_KNOWN_SCAMMERS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("kol_intel_known_scammers_unreadable: %s", str(e)[:120])
        return [{"handle": h} for h in sorted(sd.load_known_scammers())]

    if isinstance(raw, list):
        records = [r for r in raw if isinstance(r, dict) and r.get("handle")]
        if records:
            return records
        return [{"handle": str(h)} for h in raw if isinstance(h, str) and str(h).strip()]

    if isinstance(raw, dict):
        for key in ("accounts", "scammers", "entries", "handles"):
            items = raw.get(key) or []
            if not isinstance(items, list):
                continue
            records = [r for r in items if isinstance(r, dict) and r.get("handle")]
            if records:
                return records
            handles = [str(h) for h in items if isinstance(h, str) and str(h).strip()]
            if handles:
                return [{"handle": h} for h in handles]
    return []


def _account_handles() -> set[str]:
    """Handles in the KOL registry (``influential_accounts.json``)."""
    try:
        return {str(a.get("handle", "")).strip().lstrip("@").lower() for a in kw.load_accounts() if a.get("handle")}
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_accounts_unreadable: %s", str(e)[:120])
        return set()


def _wallets_for(key: str) -> list[dict[str, Any]]:
    """Attributed wallets for a handle from ``verified_kol_wallets.json``."""
    wallets: list[dict[str, Any]] = []
    for entry in kw.load_verified_kol_wallets():
        entry_handle = str(entry.get("handle", "")).strip().lstrip("@").lower()
        if entry_handle != key:
            continue
        wallets.append(
            {
                "handle": entry.get("handle"),
                "name": entry.get("name"),
                "chain": entry.get("chain"),
                "address": entry.get("address"),
                "confidence": entry.get("confidence"),
                "source": entry.get("source"),
                "evidence_url": entry.get("evidence_url"),
                "verified": bool(entry.get("verified", False)),
            }
        )
    return wallets


def _scammer_handles() -> set[str]:
    """Handles flagged in ``known_scammers.json`` (normalized, lowercased)."""
    return {str(r.get("handle", "")).strip().lstrip("@").lower() for r in _load_scammer_records() if r.get("handle")}


def _load_kol_profiles() -> dict[str, dict[str, Any]]:
    """Load enriched KOL profiles from ``kol_profiles.json`` (handle -> profile).

    Built by ``scripts/build_kol_profiles.py`` from the bundled databus
    datasets + curated public-knowledge metadata. Degrades to an empty dict
    when the file is missing/corrupt — never raises.
    """
    try:
        raw = json.loads(_KOL_PROFILES_PATH.read_text(encoding="utf-8"))
        profiles = raw.get("profiles", []) if isinstance(raw, dict) else []
        if not isinstance(profiles, list):
            return {}
        return {
            str(p.get("handle", "")).strip().lstrip("@").lower(): p
            for p in profiles
            if isinstance(p, dict) and p.get("handle")
        }
    except (OSError, ValueError) as e:
        logger.warning("kol_intel_profiles_unreadable: %s", str(e)[:120])
        return {}


def _profile_summary(profile: dict[str, Any], wallet_count: int) -> dict[str, Any]:
    """Compact public shape for list views (never includes evidence internals)."""
    perf = profile.get("performance") or {}
    risk = profile.get("risk") or {}
    return {
        "handle": profile.get("handle"),
        "display_name": profile.get("display_name") or profile.get("handle"),
        "avatar_url": profile.get("avatar_url"),
        "category": profile.get("category"),
        "followers_approx": profile.get("followers_approx"),
        "followers_note": profile.get("followers_note"),
        "rating": perf.get("score"),
        "rating_grade": perf.get("grade"),
        "win_rate": perf.get("win_rate"),
        "calls_count": len(profile.get("calls") or []),
        "known_scammer": bool(risk.get("known_scammer")),
        "scammer_verified": bool(risk.get("scammer_verified")),
        "wallet_count": wallet_count,
    }


def _wallet_counts() -> dict[str, int]:
    """Number of attributed wallets per handle (from verified_kol_wallets.json)."""
    counts: dict[str, int] = {}
    for entry in kw.load_verified_kol_wallets():
        key = str(entry.get("handle", "")).strip().lstrip("@").lower()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _is_known_kol(key: str, rug_history: dict[str, Any], wallets: list[dict[str, Any]]) -> bool:
    """A handle is known if it has attributed wallets, documented rug
    history, an entry in the KOL registry, or is flagged as a known
    scammer (a flagged scammer is still a known, trackable KOL)."""
    return bool(wallets) or bool(rug_history.get("found")) or key in _account_handles() or key in _scammer_handles()


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


# ═══════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════


@router.get("/health", response_model=HealthResponse, summary="KOL Intel Health")
def health_check() -> HealthResponse:
    """Counts of the bundled KOL data files (never raises)."""
    try:
        scammers = len(_load_scammer_records())
        wallets = len(kw.load_verified_kol_wallets())
        return HealthResponse(status="ok", scammers=scammers, wallets=wallets)
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_health_failed: %s", str(e)[:120])
        return HealthResponse(status="degraded", scammers=0, wallets=0)


@router.get("/scammers", summary="List Known Scammers")
def list_scammers(
    severity: str = Query(default="", description="Filter by severity (e.g. high|medium|low)"),
) -> dict[str, Any]:
    """List known scammers from ``known_scammers.json``, optionally filtered."""
    try:
        records = _load_scammer_records()
        if severity.strip():
            sev = severity.strip().lower()
            records = [r for r in records if str(r.get("severity") or "").strip().lower() == sev]
        return {
            "scammers": records,
            "total": len(records),
            "severity": severity.strip() or None,
            "source": "known_scammers",
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_scammers_failed: %s", str(e)[:120])
        return {"scammers": [], "total": 0, "severity": severity.strip() or None, "source": "known_scammers"}


@router.get("/kol/{handle}", response_model=None, summary="KOL Profile (score + rug history + wallets)")
def kol_profile(handle: str) -> dict[str, Any] | JSONResponse:
    """Compose trust score, documented rug history and attributed wallets."""
    key = handle.strip().lstrip("@").lower()
    if not key:
        return _error(400, "invalid_handle", "handle must not be empty")
    try:
        rug_history = ks.get_kol_rug_history(handle)
        wallets = _wallets_for(key)
        if not _is_known_kol(key, rug_history, wallets):
            return _error(404, "not_found", f"KOL {handle!r} not found")
        score = ks.score_kol(handle, persist=False)
        profile = _load_kol_profiles().get(key)
        return {
            "handle": key,
            "score": score,
            "rug_history": rug_history,
            "wallets": wallets,
            "profile": _profile_summary(profile, len(wallets)) if profile else None,
            "found": True,
            "source": "kol_intel",
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_profile_failed: %s", str(e)[:120])
        return _error(500, "internal_error", f"KOL profile lookup failed: {e}")


@router.get("/leaderboard", summary="KOL Trust-Score Leaderboard")
def leaderboard(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """Rank tracked KOLs by trust score (descending)."""
    try:
        result = ks.get_kol_leaderboard(limit)
        # Merge enriched profile metadata (avatar/category/followers/wallet count)
        # into each entry — additive, never overwrites the score record.
        profiles = _load_kol_profiles()
        wallet_counts = _wallet_counts()
        for entry in result.get("leaderboard", []):
            key = str(entry.get("handle", "")).strip().lstrip("@").lower()
            profile = profiles.get(key)
            if profile:
                summary = _profile_summary(profile, wallet_counts.get(key, 0))
                for field in (
                    "display_name",
                    "avatar_url",
                    "category",
                    "followers_approx",
                    "rating",
                    "rating_grade",
                    "win_rate",
                    "calls_count",
                    "known_scammer",
                    "scammer_verified",
                    "wallet_count",
                ):
                    if field not in entry:
                        entry[field] = summary.get(field)
            else:
                entry.setdefault("wallet_count", wallet_counts.get(key, 0))
        return result
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_leaderboard_failed: %s", str(e)[:120])
        return {"leaderboard": [], "total_tracked": 0, "source": "kol_scoring"}


@router.get("/wallets/{handle}", summary="Attributed Wallets for a KOL")
def wallets(handle: str) -> dict[str, Any]:
    """Attributed wallets for a handle (200 with empty list when unknown)."""
    key = handle.strip().lstrip("@").lower()
    try:
        entries = _wallets_for(key)
        return {"handle": key, "wallets": entries, "total": len(entries), "source": "verified_kol_wallets"}
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_wallets_failed: %s", str(e)[:120])
        return {"handle": key, "wallets": [], "total": 0, "source": "verified_kol_wallets"}


@router.get("/cross-chain/{handle}", summary="Cross-Chain Wallet Links for a KOL")
def cross_chain(handle: str) -> dict[str, Any]:
    """Cross-chain wallet links for a handle (200 with empty list when none)."""
    key = handle.strip().lstrip("@").lower()
    try:
        links = [link.to_dict() for link in kwl.link_all_cross_chain() if link.kol_id == key]
        return {"handle": key, "links": links, "total": len(links), "source": "kol_wallet_linker"}
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_cross_chain_failed: %s", str(e)[:120])
        return {"handle": key, "links": [], "total": 0, "source": "kol_wallet_linker"}


@router.get("/shill-signals", summary="Detected Shill Signals")
def shill_signals() -> dict[str, Any]:
    """Run the shill detector over the (currently empty) live signal set.

    The read-only router has no post/tx ingestion source yet, so this
    returns a safe zero-result summary until the pipeline seeds the
    detector inputs. Never raises.
    """
    try:
        result = sd.analyze_shill_signals(
            {},
            {},
            {},
            known_scammers=sd.load_known_scammers(),
        )
        return {
            "count": result.get("total_alerts", 0),
            "alerts": result.get("alerts", []),
            "patterns": result.get("patterns", {}),
            "severity": result.get("severity", {}),
            "source": result.get("source", "shill_detector"),
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_shill_signals_failed: %s", str(e)[:120])
        return {"count": 0, "alerts": [], "patterns": {}, "severity": {}, "source": "shill_detector"}


@router.get("/scammer-calls", summary="Captured Scammer-Call Posts")
def scammer_calls(
    limit: int = Query(default=50, ge=1, le=500),
    severity: str = Query(default="", description="Filter by severity (e.g. critical|high|medium|low)"),
) -> dict[str, Any]:
    """Return captured scammer-call posts from the append-only call store.

    Reads ``app/databus/scammer_calls.json`` (JSON Lines, newest first)
    through ``CallStore`` and returns the ``{calls, total}`` shape the
    frontend expects. Empty/corrupt store degrades to
    ``{"calls": [], "total": 0}``. Never raises.
    """
    try:
        return _CALL_STORE.to_dict(limit=limit, severity=severity.strip() or None)
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_scammer_calls_failed: %s", str(e)[:120])
        return {"calls": [], "total": 0}


@router.get("/profiles", summary="Enriched KOL Profiles (avatar/category/rating/calls)")
def kol_profiles(
    limit: int = Query(default=50, ge=1, le=500),
    category: str = Query(default="", description="Filter by category (trader|alpha|influencer|analyst)"),
) -> dict[str, Any]:
    """List enriched KOL profiles, ranked: rated first (score desc), then
    unrated by follower count desc. Each entry carries avatar, category,
    approximate followers, wallet count, calls count and the computed
    rug-risk-weighted rating (null when insufficient data). Never raises.
    """
    try:
        profiles = _load_kol_profiles()
        wallet_counts = _wallet_counts()
        if category.strip():
            cat = category.strip().lower()
            profiles = {k: p for k, p in profiles.items() if str(p.get("category") or "").lower() == cat}

        def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, float, float, str]:
            _, p = item
            perf = p.get("performance") or {}
            score = perf.get("score")
            return (
                0 if score is not None else 1,
                -(score or 0),
                -(p.get("followers_approx") or 0),
                str(p.get("handle") or ""),
            )

        ranked = sorted(profiles.items(), key=sort_key)[:limit]
        entries = [_profile_summary(p, wallet_counts.get(k, 0)) for k, p in ranked]
        rated = sum(1 for p in profiles.values() if (p.get("performance") or {}).get("score") is not None)
        return {
            "profiles": entries,
            "total": len(profiles),
            "rated": rated,
            "limit": limit,
            "category": category.strip().lower() or None,
            "source": "kol_profiles",
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_profiles_failed: %s", str(e)[:120])
        return {
            "profiles": [],
            "total": 0,
            "rated": 0,
            "limit": limit,
            "category": category.strip().lower() or None,
            "source": "kol_profiles",
        }


@router.get(
    "/profiles/{handle}", response_model=None, summary="Full Enriched KOL Profile (calls + performance + wallets)"
)
def kol_profile_detail(handle: str) -> dict[str, Any] | JSONResponse:
    """Full enriched profile for a handle: metadata, historical calls,
    computed performance, risk associations and attributed wallets.
    Unknown handles return 404; missing profile data degrades to a minimal
    wallet-derived profile (200) when the handle is in the wallet list.
    """
    key = handle.strip().lstrip("@").lower()
    if not key:
        return _error(400, "invalid_handle", "handle must not be empty")
    try:
        profiles = _load_kol_profiles()
        profile = profiles.get(key)
        if profile is None:
            # Fall back to wallet-only shape (handle known from wallet list)
            wallets = _wallets_for(key)
            if not wallets:
                return _error(404, "not_found", f"KOL {handle!r} not found")
            return {
                "handle": key,
                "profile": {
                    "handle": key,
                    "x_handle": key,
                    "display_name": key,
                    "avatar_url": f"https://unavatar.io/x/{key}",
                    "category": None,
                    "followers_approx": None,
                    "calls": [],
                    "performance": {
                        "score": None,
                        "grade": None,
                        "win_rate": None,
                        "wins": 0,
                        "losses": 0,
                        "note": "No enriched profile entry; wallet attribution only.",
                    },
                    "risk": {"known_scammer": False, "rug_associations": []},
                    "sources": ["verified_kol_wallets"],
                },
                "wallets": wallets,
                "wallet_count": len(wallets),
                "found": True,
                "source": "kol_profiles",
            }
        wallets = _wallets_for(key)
        return {
            "handle": key,
            "profile": profile,
            "wallets": wallets,
            "wallet_count": len(wallets),
            "found": True,
            "source": "kol_profiles",
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("kol_intel_profile_detail_failed: %s", str(e)[:120])
        return _error(500, "internal_error", f"KOL profile lookup failed: {e}")
