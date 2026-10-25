"""Pure hashing + text-similarity helpers (no I/O, no Redis).

``url_hash``/``doc_id_hash`` key the Redis structures; ``normalize_text``,
``tokenize`` and ``near_duplicate`` back the optional soft-dedup candidates.
Boilerplate stripping lives in :mod:`app.utils.dedup_content` so fingerprinting
and hashing agree on what "real content" means.
"""

from __future__ import annotations

import hashlib
import re

from app.utils.dedup_content import strip_boilerplate


def url_hash(url: str) -> str:
    """Stable short hash of a URL for dedup keys (matches crawler doc-id style)."""
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:32]


def doc_id_hash(doc_id: str) -> str:
    """Stable short hash of an external doc-id for exact doc-id dedup.

    Mirrors ``url_hash`` so doc-ids and URLs share one key space; use it for
    crawler-returned ``doc_id`` values that are stable across re-ingestion.
    """
    return hashlib.sha256((doc_id or "").encode("utf-8", errors="ignore")).hexdigest()[:32]


def normalize_text(text: str) -> str:
    """Canonical text form: boilerplate stripped, whitespace collapsed, lower.

    Unlike ``content_fingerprint`` (which truncates to the first 1000 chars),
    ``normalize_text`` keeps the FULL body — use it for exact/near-identical
    block detection where truncation would be lossy.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", strip_boilerplate(text)).strip().lower()


def content_hash(text: str) -> str:
    """SHA-256 of the fully normalized text — identical blocks collapse."""
    return hashlib.sha256(normalize_text(text).encode("utf-8", errors="ignore")).hexdigest()


def tokenize(text: str) -> set[str]:
    """Normalized word tokens for similarity scoring (lowercased alnum runs)."""
    return set(re.findall(r"[a-z0-9]+", normalize_text(text)))


def near_duplicate(a: str, b: str, threshold: float = 0.9) -> bool:
    """True when token-set Jaccard similarity ``|A∩B| / |A union B| >= threshold``.

    Empty/entity-free inputs return False (cannot decide → fail-open, matching
    the module's other checks). No network, no Redis — safe inline.
    """
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold
