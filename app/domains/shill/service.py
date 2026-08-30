"""Shill-campaign service layer — the single entry point the router calls.

Wires the shill domain pieces together: detection, the ApiPass profile probe,
the evidence store, and the reused Wall-of-Shame feed. Every method is
fail-open — it returns empty structures / ``None`` on any downstream failure
and never raises, so a dead ApiPass key, missing bot DB, Qdrant outage or
missing wall-of-shame file degrades to an empty 200 rather than a 500.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.databus import kol_bot_store as kbs
from app.domains.shill import apipass_x
from app.domains.shill.detect import ShillCampaign, detect_shill_campaigns
from app.domains.shill.evidence import read_shill_evidence, search_evidence, write_evidence

logger = get_logger("shill_service")


# ── Campaign detection ──────────────────────────────────────────────────────


async def detect_campaigns_from_articles(
    articles: list[Any],
    *,
    window_hours: int | None = None,
    min_accounts: int | None = None,
    min_mentions: int | None = None,
) -> list[ShillCampaign]:
    """Detect coordinated shill campaigns from a list of article dicts.

    Pure detection (no I/O). The caller (bot / an ingestion job) feeds
    normalized articles; the router returns the campaigns. Fail-open: any
    error → ``[]``.
    """
    try:
        return detect_shill_campaigns(
            articles or [],
            window_hours=window_hours,
            min_accounts=min_accounts,
            min_mentions=min_mentions,
        )
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_service:detect_failed err=%s", str(exc)[:120])
        return []


# ── Campaign list (evidence-backed) ────────────────────────────────────────


def list_campaigns(
    *,
    token: str = "",
    chain: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Aggregate shill evidence rows into a campaign list.

    There is no dedicated campaign table, so campaigns are derived from the
    evidence store: group ``shill_evidence`` rows by ``(token, chain)`` and
    count distinct ``account`` pushers + total mentions. This reuses the
    evidence the bot/backend have already persisted — one source of truth.
    Fail-open: empty evidence → ``{"campaigns": [], "total": 0}``.
    """
    evidence = read_shill_evidence(token=token, chain=chain, limit=max(1, int(limit) * 5))
    grouped: dict[str, dict[str, Any]] = {}
    for row in evidence or []:
        key = f"{row.get('chain') or 'ticker'}:{row.get('token') or 'unknown'}"
        g = grouped.setdefault(
            key,
            {"token": row.get("token"), "chain": row.get("chain"), "accounts": set(), "mentions": 0, "evidence": []},
        )
        g["accounts"].add(str(row.get("account") or ""))
        g["mentions"] += 1
        g["evidence"].append(row)
    campaigns = []
    for g in grouped.values():
        accounts = sorted(a for a in g["accounts"] if a)
        campaigns.append(
            {
                "key": f"{g['chain']}:{g['token']}",
                "token": g["token"],
                "chain": g["chain"],
                "accounts": accounts,
                "accounts_count": len(accounts),
                "mentions_count": g["mentions"],
                "score": len(accounts) * 10.0 + g["mentions"],
                "evidence": g["evidence"][:10],
            }
        )
    campaigns.sort(key=lambda c: c["score"], reverse=True)
    return {"campaigns": campaigns[: int(limit)], "total": len(campaigns), "source": "shill_evidence"}


# ── Handle probe (ApiPass) ─────────────────────────────────────────────────


async def probe_handle(handle: str, *, force: bool = False) -> dict[str, Any] | None:
    """Probe one X handle via ApiPass (``fetch_x_profile``).

    Returns the rich profile (or the unavailable node) or ``None`` when no key
    is configured / rate-limited / API error — the caller degrades.
    """
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    try:
        return await apipass_x.fetch_x_profile(handle, force=force)
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_service:probe_failed handle=%s err=%s", handle, str(exc)[:120])
        return None


# ── Evidence read ──────────────────────────────────────────────────────────


def get_evidence(
    *,
    token: str = "",
    chain: str = "",
    account: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Return shill evidence rows. Fail-open ``{"evidence": [], "total": 0}``."""
    evidence = read_shill_evidence(token=token, chain=chain, account=account, limit=limit)
    return {"evidence": evidence, "total": len(evidence), "source": "shill_evidence"}


async def search_shill_evidence(query: str, *, limit: int = 10) -> dict[str, Any]:
    """Semantic search over the shill evidence Qdrant collection."""
    hits = await search_evidence(query, limit=limit)
    return {"hits": hits, "total": len(hits), "source": "qdrant_shill_evidence"}


async def add_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Persist one shill evidence record (Qdrant mirror + bot DB, fail-open).

    Returns ``{"saved": bool}`` — True when the canonical write landed.
    """
    saved = await write_evidence(record or {})
    return {"saved": bool(saved), "source": "shill_evidence"}


# ── Wall of Shame / shill feed (reused) ────────────────────────────────────


def wall_of_shame_feed(severity: str = "") -> dict[str, Any]:
    """Wall-of-Shame feed — reused from the existing KOL intel store.

    Reads the curated Wall of Shame the bot's kol_ingest pipeline writes
    (``app.databus.kol_bot_store``). This deliberately reuses (not duplicates)
    the existing ``/api/v1/kol/wall-of-shame`` data source. Fail-open: missing
    file → ``{"wall_of_shame": [], "total": 0}``.
    """
    try:
        sev = severity.strip() if isinstance(severity, str) else ""
        return kbs.load_wall_of_shame(severity=sev)
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_service:wall_of_shame_failed err=%s", str(exc)[:120])
        return {"wall_of_shame": [], "total": 0, "severity": severity.strip() or None, "source": "wall_of_shame_json"}


__all__ = [
    "add_evidence",
    "detect_campaigns_from_articles",
    "get_evidence",
    "list_campaigns",
    "probe_handle",
    "search_shill_evidence",
    "wall_of_shame_feed",
]
