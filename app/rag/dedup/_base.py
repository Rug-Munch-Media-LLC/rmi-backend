"""Core dedup primitives: hashing, normalization, DedupResult."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


def content_fingerprint(text: str) -> str:
    """Stable fingerprint: boilerplate stripped, first 1000 chars, whitespace normalized."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()[:1000]
    return hashlib.sha256(cleaned.encode()).hexdigest()


def content_hash(text: str) -> str:
    return content_fingerprint(text)


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def near_duplicate(a: str, b: str, threshold: float = 0.9) -> bool:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= threshold


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_valid_evm_address(address: str) -> bool:
    return bool(re.match(r"^0x[0-9a-fA-F]{40}$", address or ""))


def to_checksum_address(address: str) -> str:
    # Simplified: real impl uses EIP-55. Kept here for backward compat.
    return (address or "").lower()


def normalize_address(address: str, chain: str = "evm") -> str:
    if chain == "evm":
        return to_checksum_address(address)
    return (address or "").strip()


@dataclass
class DedupResult:
    is_duplicate: bool = False
    group_id: str | None = None
    level: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
