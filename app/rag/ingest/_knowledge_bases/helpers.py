"""Shared helpers for the knowledge base package (collection map)."""

from __future__ import annotations

from typing import Any

from app.rag.ingest._knowledge_bases.exploits import DEPLOYER_HISTORY, SCAM_INTEL
from app.rag.ingest._knowledge_bases.patterns import CONTRACT_AUDIT
from app.rag.ingest._knowledge_bases.protocols import PHISHING_DB, WALLET_LABELS

KNOWLEDGE_BASES: dict[str, list[dict[str, Any]]] = {
    "scam_intel": SCAM_INTEL,
    "deployer_history": DEPLOYER_HISTORY,
    "contract_audit": CONTRACT_AUDIT,
    "phishing_db": PHISHING_DB,
    "wallet_labels": WALLET_LABELS,
}
