"""Normalize GMGN token-security facts into our own security block."""

from __future__ import annotations

from typing import Any

from app.domains.gmgn_intel.coerce import gap, num, text, whole

SECURITY_ENDPOINT = "GET /v1/token/security"

_NUMERIC = (
    "rug_ratio",
    "top_10_holder_rate",
    "suspected_insider_hold_rate",
    "sniper_count",
    "bot_degen_rate",
    "burn_ratio",
    "dev_token_burn_ratio",
)


def _flags(sec: dict[str, Any]) -> list[str]:
    """Human-readable risk flags derived from the raw facts."""
    flags: list[str] = []
    if sec.get("is_honeypot"):
        flags.append("honeypot")
    if sec.get("can_not_sell"):
        flags.append("cannot_sell")
    if (num(sec.get("top_10_holder_rate")) or 0) > 0.5:
        flags.append("top10_concentration")
    if (num(sec.get("rug_ratio")) or 0) > 0.5:
        flags.append("high_rug_ratio")
    buy_tax = num(sec.get("buy_tax")) or 0
    sell_tax = num(sec.get("sell_tax")) or 0
    if max(buy_tax, sell_tax) > 10:
        flags.append("high_tax")
    if sec.get("is_blacklist") or sec.get("blacklist"):
        flags.append("blacklist")
    return flags


def normalize_security(raw: dict[str, Any] | None, observed_at: str) -> dict[str, Any]:
    """Security facts -> our block; nulls carry a reason, never a fake zero."""
    sec: dict[str, Any] = {
        "is_honeypot": bool((raw or {}).get("is_honeypot") or (raw or {}).get("honeypot") or False),
        "can_not_sell": bool((raw or {}).get("can_not_sell") or False),
        "is_open_source": (raw or {}).get("is_open_source", (raw or {}).get("open_source")),
        "renounced_mint": (raw or {}).get("renounced_mint"),
        "burn_status": text((raw or {}).get("burn_status")),
        "buy_tax": num((raw or {}).get("buy_tax")),
        "sell_tax": num((raw or {}).get("sell_tax")),
        "sniper_count": whole((raw or {}).get("sniper_count")),
        "source": SECURITY_ENDPOINT,
        "observed_at": observed_at,
        "missing": [],
        "flags": [],
    }
    for field in _NUMERIC:
        sec[field] = num((raw or {}).get(field))
    if raw is None:
        sec["missing"].append(gap("security", "endpoint returned no data"))
        return sec
    for field in ("rug_ratio", "top_10_holder_rate", "sniper_count"):
        if sec.get(field) is None:
            sec["missing"].append(gap(field, "absent in security response"))
    sec["flags"] = _flags(raw)
    return sec
