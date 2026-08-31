"""Scam Alerts Router — active scam detections for the site main page.

Surfaces recent (default 24h) high-risk scam detections WITH the shill
evidence behind them: WHO is shilling the token (KOL handles), WHY it is a
scam (scanner risk flags + coordination evidence), and the screenshot/OCR
proof captured by the bot pipeline across X + Telegram.

Data sources (reused — never duplicated):
  * ``app.domains.shill.evidence.read_shill_evidence`` — the bot's
    ``shill_evidence`` SQLite table (read-only bridge) — screenshots + OCR.
  * ``app.routers.scanner_data_router._read_cache`` — the unified scanner's
    Redis cache (``sentinelscan:{chain}:{address}``) — risk_flags/risk_score
    when the token was scanned recently.

Honesty contract: scam reasons come ONLY from real scanner risk flags or
from the coordination evidence itself (N accounts, M posts). Nothing is
fabricated; when there is no evidence the endpoint returns an empty list.
Every failure degrades to a structured empty 200 — never a 500.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.domains.shill.evidence import read_shill_evidence

logger = get_logger("scam_alerts_router")

router = APIRouter(prefix="/api/v1/scam-alerts", tags=["social-intel"])

#: OCR excerpt length carried per KOL evidence entry (bounded payload).
_OCR_EXCERPT_CHARS = 240


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-ish timestamp; None when missing/unparseable."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Legacy rows may carry naive timestamps — treat them as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _looks_like_address(token: str) -> bool:
    """Heuristic: is this token string a contract address (not a $TICKER)?"""
    t = (token or "").strip()
    if t.startswith("0x") and len(t) >= 40:
        return True
    # Solana-style base58 addresses are 32-44 chars with mixed case/digits.
    return 32 <= len(t) <= 44 and any(c.islower() for c in t) and any(c.isdigit() for c in t)


async def _scanner_risk(chain: str, token: str) -> dict[str, Any] | None:
    """Cached scanner risk for the token, or None (unscanned / cache down)."""
    if not _looks_like_address(token):
        return None
    try:
        from app.routers.scanner_data_router import _read_cache

        cached = await _read_cache(chain or "solana", token)
        if not isinstance(cached, dict):
            return None
        risk_score = cached.get("risk_score")
        if risk_score is None:
            safety = cached.get("safety_score")
            risk_score = round(100 - float(safety), 2) if safety is not None else None
        return {
            "risk_score": risk_score,
            "risk_level": cached.get("risk_level"),
            "risk_flags": [str(f) for f in cached.get("risk_flags", []) if f],
            "scanned_at": cached.get("scanned_at", ""),
        }
    except Exception as exc:  # fail-open by contract
        logger.debug("scam_alerts:scanner_risk_failed err=%s", str(exc)[:120])
        return None


@router.get(
    "/active",
    summary="Active Scam Alerts (recent detections + shill evidence)",
    dependencies=[Depends(get_rate_limit_dependency("scam_a1", rate=2.0, burst=5))],
)
async def active_alerts(
    hours: int = Query(default=24, ge=1, le=168, description="Look-back window"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Recent scam detections with shilling KOLs, scam reasons and evidence.

    Groups the last *hours* of ``shill_evidence`` rows by (token, chain),
    enriches each group with cached scanner risk flags (honeypot, mint
    authority, liquidity pulled, …) when the token was scanned, and ranks by
    risk score. Never raises — downstream failure → empty alert list.
    """
    try:
        evidence = read_shill_evidence(limit=500)
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_alerts:evidence_read_failed err=%s", str(exc)[:120])
        evidence = []

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    grouped: dict[str, dict[str, Any]] = {}
    for row in evidence or []:
        ts = _parse_ts(row.get("captured_at"))
        # Rows with an unparseable/missing timestamp are real evidence of
        # unknown age — keep them (honest inclusion) rather than dropping.
        if ts is not None and ts < cutoff:
            continue
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        chain = str(row.get("chain") or "").strip()
        key = f"{chain}:{token}"
        g = grouped.setdefault(
            key,
            {"token": token, "chain": chain, "accounts": set(), "mentions": 0, "evidence": [], "last_seen": None},
        )
        account = str(row.get("account") or "").strip()
        if account:
            g["accounts"].add(account)
        g["mentions"] += 1
        g["evidence"].append(row)
        if ts is not None and (g["last_seen"] is None or ts > g["last_seen"]):
            g["last_seen"] = ts

    alerts: list[dict[str, Any]] = []
    for g in grouped.values():
        accounts = sorted(a for a in g["accounts"] if a)
        scanner = await _scanner_risk(g["chain"], g["token"])

        # Scam reasons — scanner risk flags when available, plus the
        # coordination evidence itself (real counts, never fabricated).
        reasons = list(scanner["risk_flags"]) if scanner else []
        coordination = (
            f"coordinated shill: {len(accounts)} accounts pushed it ({g['mentions']} posts across X/Telegram)"
        )
        reasons.append(coordination)

        risk_score = (
            scanner["risk_score"]
            if scanner and scanner.get("risk_score") is not None
            else min(100.0, len(accounts) * 10.0 + g["mentions"])
        )
        risk_level = scanner.get("risk_level") if scanner else None
        if not risk_level:
            risk_level = "high" if risk_score >= 70 else "medium" if risk_score >= 40 else "low"

        # KOL evidence entries (handles + screenshot + OCR excerpt).
        kols: list[dict[str, Any]] = []
        screenshots: list[str] = []
        ocr_excerpt = ""
        for row in g["evidence"]:
            handle = str(row.get("account") or "").strip().lstrip("@")
            image_url = str(row.get("image_url") or "")
            ocr = str(row.get("ocr_text") or "").strip()
            if handle and not any(k["handle"] == handle for k in kols):
                kols.append(
                    {
                        "handle": handle,
                        "x_url": row.get("x_url") or None,
                        "posts": sum(1 for e in g["evidence"] if str(e.get("account") or "").lstrip("@") == handle),
                    }
                )
            if image_url and image_url not in screenshots:
                screenshots.append(image_url)
            if ocr and len(ocr_excerpt) < _OCR_EXCERPT_CHARS:
                ocr_excerpt = (ocr_excerpt + ("\n" if ocr_excerpt else "") + ocr)[:_OCR_EXCERPT_CHARS]

        alerts.append(
            {
                "token": g["token"],
                "chain": g["chain"] or None,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "scam_reasons": reasons,
                "shilling_kols": kols[:10],
                "accounts_count": len(accounts),
                "mentions_count": g["mentions"],
                "screenshot_urls": screenshots[:3],
                "ocr_excerpt": ocr_excerpt or None,
                "last_seen": g["last_seen"].isoformat() if g["last_seen"] else None,
                "scanner_scanned_at": (scanner or {}).get("scanned_at") or None,
                # Drill-down links (frontend routes).
                "links": {
                    "kol_profile": f"/kol/{accounts[0].lstrip('@')}" if accounts else None,
                    "campaign": "/wall-of-shame",
                    "token": "/scanner",
                },
            }
        )

    alerts.sort(key=lambda a: (a["risk_score"], a["mentions_count"]), reverse=True)
    return {
        "alerts": alerts[: int(limit)],
        "total": len(alerts),
        "window_hours": hours,
        "source": "shill_evidence+scanner_cache",
    }


__all__ = ["router"]
