"""KOL wallet labeling — cross-ref attributed wallets with shill evidence.

The 945-wallet KOL KB (``verified_kol_wallets.json``) maps KOL handles to
wallets but carries no behavioral labels. This module improves labeling by
cross-referencing each wallet against the shill-evidence store
(``app.domains.shill.evidence.read_shill_evidence`` — the rows the bot's
X/Telegram shill capture writes):

  * ``shill recipient``   — the wallet ADDRESS appears in the OCR/text of a
    shill-evidence row for a token the KOL shilled (the KOL pushed token X
    and this wallet is named in the captured evidence for it).
  * ``deployer of $T``    — the wallet address IS the token address in a
    shill-evidence row (the KOL's wallet deployed the shilled token).
  * ``verified KOL wallet`` / ``attributed wallet`` — the KB baseline label
    from the entry's ``verified``/``confidence`` fields when no shill
    cross-reference applies.

Each label carries a confidence (``high`` for direct address matches,
``medium`` for KB-verified, ``low`` for heuristic attribution) and the list
of linked shill campaigns (tokens + evidence URLs). Pure file/DB reads,
fail-open everywhere — never raises.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.shill import evidence as shill_evidence

logger = get_logger("kol_wallet_labels")

#: Max shill-evidence rows consulted per handle (bounded read).
_EVIDENCE_LIMIT = 200


def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _norm_addr(addr: Any) -> str:
    return str(addr or "").strip().lower()


def _shill_rows_for(handle: str) -> list[dict[str, Any]]:
    """Shill-evidence rows for a handle (fail-open ``[]``)."""
    try:
        return shill_evidence.read_shill_evidence(account=handle, limit=_EVIDENCE_LIMIT)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_wallet_labels:evidence_read_failed err=%s", str(exc)[:120])
        return []


def _campaigns_for(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Distinct shill campaigns (token + evidence url) from evidence rows."""
    campaigns: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        token = str(row.get("token") or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        campaigns.append(
            {
                "token": token,
                "chain": str(row.get("chain") or ""),
                "x_url": str(row.get("x_url") or row.get("url") or ""),
                "captured_at": str(row.get("captured_at") or ""),
            }
        )
    return campaigns


def label_wallets(handle: str) -> list[dict[str, Any]]:
    """Labeled wallets for a KOL handle.

    Returns one record per attributed wallet:
    ``{address, chain, label, confidence, shill_campaigns, verified, source}``.
    Unknown handles / empty KB → ``[]``. Never raises.
    """
    from app.databus import kol_wallets as kw

    key = _norm_handle(handle)
    if not key:
        return []
    try:
        entries = [
            e for e in kw.load_verified_kol_wallets() if _norm_handle(e.get("handle")) == key and e.get("address")
        ]
    except Exception as exc:  # KB unreadable → no wallets to label
        logger.debug("kol_wallet_labels:kb_read_failed err=%s", str(exc)[:120])
        return []
    if not entries:
        return []

    rows = _shill_rows_for(key)
    campaigns = _campaigns_for(rows)
    labeled: list[dict[str, Any]] = []
    for entry in entries:
        address = str(entry.get("address") or "").strip()
        addr_l = _norm_addr(address)
        label = "verified KOL wallet" if entry.get("verified") else "attributed wallet"
        confidence = "medium" if entry.get("verified") else "low"
        linked: list[dict[str, str]] = []
        for row in rows:
            token = str(row.get("token") or "").strip()
            if not token:
                continue
            haystack = f"{row.get('text') or ''} {row.get('ocr_text') or ''}".lower()
            if addr_l and addr_l in haystack:
                # Direct address match in captured shill evidence.
                label = "shill recipient"
                confidence = "high"
                if token and token not in {c["token"] for c in linked}:
                    linked.append(
                        {
                            "token": token,
                            "x_url": str(row.get("x_url") or row.get("url") or ""),
                            "captured_at": str(row.get("captured_at") or ""),
                        }
                    )
            elif addr_l and addr_l == _norm_addr(token):
                # The wallet IS the shilled token's contract address.
                label = f"deployer of {token[:16]}"
                confidence = "high"
                if token not in {c["token"] for c in linked}:
                    linked.append(
                        {
                            "token": token,
                            "x_url": str(row.get("x_url") or row.get("url") or ""),
                            "captured_at": str(row.get("captured_at") or ""),
                        }
                    )
        labeled.append(
            {
                "address": address,
                "chain": entry.get("chain"),
                "label": label,
                "confidence": confidence,
                "shill_campaigns": linked or campaigns,
                "verified": bool(entry.get("verified", False)),
                "source": entry.get("source"),
                "evidence_url": entry.get("evidence_url"),
            }
        )
    return labeled


__all__ = ["label_wallets"]
