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
        return {
            "handle": key,
            "score": score,
            "rug_history": rug_history,
            "wallets": wallets,
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
        return ks.get_kol_leaderboard(limit)
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
