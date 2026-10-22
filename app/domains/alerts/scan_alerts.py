"""Derive persistable alert rows from a rugmunch-scanner scan result.

The scanner returns one entry per detector in ``signals``; each carries a
``signal`` boolean (did it fire), ``risk_level`` and the evidence behind it.
A clean token fires nothing and produces no alert — that is deliberate, so the
alerts table stays a record of things that actually happened rather than a
mirror of every scan.

Content has to be postable to a channel with no further lookups, so each row
carries what happened, the token and chain, the size of the thing that made it
matter (liquidity / price / volume), and why it matters in plain English.
"""

from __future__ import annotations

from typing import Any

from app.domains.alerts.postgres_store import alert_id_for

#: Detector name → the phrase a channel post leads with.
_LABELS = {
    "honeypot": "Honeypot behaviour detected",
    "deployer_history": "Deployer linked to prior rugpulls",
    "mint_authority": "Mint authority still live — supply can be inflated",
    "freeze_authority": "Freeze authority still live — holders can be locked out",
    "liquidity_drain": "Liquidity draining",
    "token_velocity": "Abnormal token velocity",
    "wallet_cluster": "Clustered wallets holding supply",
}

_SEVERITY = {
    "LOW": "info",
    "MEDIUM": "warning",
    "HIGH": "critical",
    "CRITICAL": "critical",
}


def _short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address


def _market_facts(enrichment: dict[str, Any]) -> str:
    """Liquidity / price / 24h volume, in the order a trader reads them."""
    market = enrichment.get("market") or {}
    if not market.get("ok"):
        return "market data unavailable"
    parts = []
    if market.get("liquidity_usd") is not None:
        parts.append(f"liquidity ${market['liquidity_usd']:,.0f}")
    if market.get("price_usd") is not None:
        parts.append(f"price ${market['price_usd']:.3g}")
    volume = (market.get("volume") or {}).get("h24")
    if volume:
        parts.append(f"24h volume ${volume:,.0f}")
    if market.get("dex"):
        parts.append(f"top pool {market['dex']}")
    return ", ".join(parts)


def _authority_facts(enrichment: dict[str, Any]) -> list[str]:
    rugcheck = enrichment.get("rugcheck") or {}
    if not rugcheck.get("ok"):
        return []
    facts = []
    for field, phrase in (
        ("mint_authority", "mint authority"),
        ("freeze_authority", "freeze authority"),
    ):
        value = rugcheck.get(field)
        if value:
            facts.append(f"{phrase} {_short(str(value))}")
    fee = (rugcheck.get("transfer_fee") or {}).get("pct")
    if fee:
        facts.append(f"transfer fee {fee}%")
    return facts


def alerts_from_scan(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return alert rows for every detector that actually fired."""
    token = str(result.get("token") or "")
    chain = str(result.get("chain") or "")
    if not token or not chain:
        return []

    enrichment = result.get("enrichment") or {}
    coverage = result.get("coverage") or {}
    degraded = coverage.get("degraded_subsystems") or []
    market = _market_facts(enrichment)
    authorities = _authority_facts(enrichment)

    rows: list[dict[str, Any]] = []
    for signal in result.get("signals") or []:
        if not signal.get("signal"):
            continue
        detector = str(signal.get("detector") or "unknown")
        level = str(signal.get("risk_level") or "LOW").upper()
        score = signal.get("risk_score")
        label = _LABELS.get(detector, detector.replace("_", " ").capitalize())

        detail = f"{label} on {chain} {_short(token)}"
        if score is not None:
            detail += f" (risk {score}/100, {level})"
        why = [market, *authorities]
        fired = [str(e) for e in (signal.get("evidence") or [])][:3]
        why.extend(fired)
        description = "; ".join(p for p in why if p) + "."

        rows.append(
            {
                "alert_id": alert_id_for(chain, token, detector),
                "token_id": token,
                "wallet_id": None,
                "chain": chain,
                "alert_type": detector,
                "severity": _SEVERITY.get(level, "info"),
                "title": detail,
                "description": description,
                "evidence": {
                    "detector": detector,
                    "risk_score": score,
                    "risk_level": level,
                    "confidence": signal.get("confidence"),
                    "confidence_tier": signal.get("confidence_tier"),
                    "fired_evidence": fired,
                    "market": enrichment.get("market") or {},
                    "rugcheck": {
                        k: (enrichment.get("rugcheck") or {}).get(k)
                        for k in ("mint_authority", "freeze_authority", "transfer_fee")
                    },
                    "degraded_subsystems": degraded,
                    "scanner_warnings": (signal.get("warnings") or []),
                    "scanned_at": result.get("scanned_at"),
                },
            }
        )
    return rows
