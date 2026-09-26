"""Row builders + address predicates shared by the source ingest modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["LabelFields", "ScamFields", "label_row", "scam_row", "valid_address"]


@dataclass(frozen=True, slots=True)
class LabelFields:
    """Inputs required to build one wallet label row."""

    address: str
    chain_id: str
    name: str
    category: str
    subtype: str
    source: str


@dataclass(frozen=True, slots=True)
class ScamFields:
    """Inputs required to build one scam-address row."""

    address: str
    chain_id: str
    source: str
    threat_type: str
    confidence: float
    evidence: str


def valid_address(addr: str) -> bool:
    """True when an address is non-empty and long enough to be an address."""
    return bool(addr) and len(addr) >= 10


def label_row(fields: LabelFields) -> dict[str, Any]:
    """Build a wallet_labels row."""
    return {
        "address": fields.address.lower(),
        "chain_id": fields.chain_id,
        "label_name": fields.name,
        "label_category": fields.category,
        "label_subtype": fields.subtype,
        "source": fields.source,
        "is_sanctioned": False,
    }


def scam_row(fields: ScamFields) -> dict[str, Any]:
    """Build a scam_addresses row."""
    return {
        "address": fields.address.lower(),
        "chain_id": fields.chain_id,
        "source": fields.source,
        "threat_type": fields.threat_type,
        "confidence": fields.confidence,
        "evidence": fields.evidence,
    }
