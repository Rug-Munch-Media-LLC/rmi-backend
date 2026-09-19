"""Scam-pattern detection — rugbrain-backed, backend-side helper.

Relocated from ``app.rag.compat.detect_scam_patterns`` (Track 1 RAG
cutaway). The RAG engine lives in rugbrain; this module keeps the
backend's quick keyword pre-filter plus a rugbrain semantic lookup, and
preserves the legacy return shape:

    ``quick_matches`` / ``deep_matches`` / ``highest_threat`` / ``threat_severity``
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.integrations.rugbrain_client import rugbrain_search

logger = get_logger(__name__)


async def detect_scam_patterns(token_data: dict[str, Any], threshold: float = 0.65) -> dict[str, Any]:
    """Compare a token against known scam patterns via rugbrain + keyword matching."""
    quick_matches: list[dict[str, Any]] = []
    deep_matches: list[dict[str, Any]] = []

    # 1. Quick code-snippet keyword matches
    try:
        from app.crypto_embeddings import KNOWN_SCAM_PATTERNS

        code_lower = (token_data.get("contract_code") or "").lower()
        for pattern in KNOWN_SCAM_PATTERNS:
            snippets = pattern.get("code_snippets") or []
            if not snippets:
                continue
            quick_hits = sum(1 for s in snippets if s.lower() in code_lower)
            if quick_hits > 0:
                quick_matches.append(
                    {
                        "pattern": pattern["name"],
                        "severity": pattern.get("severity", "high"),
                        "quick_match_score": quick_hits / len(snippets),
                        "matched_snippets": quick_hits,
                    }
                )
    except Exception as e:
        logger.debug("detect_scam_quick_failed err=%s", str(e)[:120])

    # 2. Deep semantic matches via rugbrain search
    try:
        query = (
            f"{token_data.get('address', '')} {token_data.get('symbol', '')} {token_data.get('name', '')} scam pattern"
        ).strip()
        hits = await rugbrain_search(
            query=query, collections=["known_scams", "scam_patterns", "defi_exploits"], limit=5
        )
        for h in hits:
            score = float(h.get("rrf_score", 0) or 0)
            meta = h.get("metadata", {}) or {}
            if score >= 0.005:  # RRF scores are ~0.01-0.02, so 0.005 catches anything meaningful
                deep_matches.append(
                    {
                        "pattern": meta.get("pattern_name", meta.get("name", "")),
                        "severity": meta.get("severity", "high"),
                        "description": (h.get("content") or h.get("text") or "")[:300],
                        "similarity": round(score, 4),
                    }
                )
    except Exception as e:
        logger.debug("detect_scam_deep_failed err=%s", str(e)[:120])

    deep_matches.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return {
        "quick_matches": quick_matches,
        "deep_matches": deep_matches,
        "highest_threat": deep_matches[0]["pattern"] if deep_matches else "none",
        "threat_severity": deep_matches[0]["severity"] if deep_matches else "low",
    }
