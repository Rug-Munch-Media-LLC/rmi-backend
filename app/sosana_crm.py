"""
SOSANA CRM Investigation - Detailed case data API (BACKEND ONLY, not public).
Exposes investigation data: wallets, entities, financials, evidence, risk assessment.
"""

import json
import os

from app.core.logging import get_logger

logger = get_logger(__name__)

CRM_PATH = "/app/data/SOSANA-CRM-2024.json"


class SOSANACRM:
    """Loads and serves the SOSANA criminal investigation data."""

    def __init__(self) -> None:
        self._data = None
        self._loaded = False

    def load(self) -> bool:
        if os.path.exists(CRM_PATH):
            try:
                with open(CRM_PATH) as f:
                    self._data = json.load(f)
                self._loaded = True
                return True
            except Exception as e:
                logger.error("Failed to load SOSANA CRM: %s", e)
        return False

    @property
    def data(self) -> dict:
        if not self._loaded:
            self.load()
        return self._data or {}

    def summary(self) -> dict:
        """Case overview - safe to expose."""
        d = self.data
        return {
            "case_id": d.get("case_id"),
            "title": d.get("title"),
            "status": d.get("status"),
            "created_at": d.get("created_at"),
            "summary": d.get("summary", {}),
            "financial_analysis": d.get("financial_analysis", {}),
            "risk_assessment": d.get("risk_assessment", {}),
        }

    def entities(self, entity_type: str | None = None) -> dict:
        """Get entities: wallets, persons, organizations, tokens."""
        entities = self.data.get("entities", {})
        if entity_type:
            return {entity_type: entities.get(entity_type, [])}
        return entities

    def wallets_detail(self) -> list[dict]:
        """Detailed wallet analysis from the investigation."""
        wallets = self.data.get("entities", {}).get("wallets", [])
        # Enrich with on-chain labels
        result = []
        for w in wallets[:50]:  # Limit to 50 for API response
            result.append(
                {
                    "address": w.get("address", ""),
                    "chain": w.get("chain", "unknown"),
                    "label": w.get("label", "unlabeled"),
                    "role": w.get("role", "unknown"),
                    "balance_usd": w.get("balance_usd", w.get("estimated_value", 0)),
                    "first_seen": w.get("first_seen", ""),
                    "last_active": w.get("last_active", ""),
                    "transaction_count": w.get("transaction_count", w.get("tx_count", 0)),
                    "associated_entities": w.get("associated_with", w.get("related", [])),
                    "evidence_tier": w.get("evidence_tier", "unknown"),
                    "risk_flags": w.get("risk_flags", w.get("flags", [])),
                }
            )
        return result

    def financial_detail(self) -> dict:
        """Full financial analysis."""
        return self.data.get("financial_analysis", {})

    def evidence_summary(self) -> dict:
        """Evidence overview with counts."""
        evidence = self.data.get("evidence", {})
        return {
            tier: len(items) if isinstance(items, list) else items
            for tier, items in evidence.items()
        }

    def persons(self) -> list[dict]:
        """Persons of interest."""
        return self.data.get("entities", {}).get("persons", [])[:20]

    def organizations(self) -> list[dict]:
        """Organizations involved."""
        return self.data.get("entities", {}).get("organizations", [])[:20]

    def legal_framework(self) -> dict:
        """Legal and prosecution framework."""
        return self.data.get("legal_framework", {})


# Singleton
sosana = SOSANACRM()
