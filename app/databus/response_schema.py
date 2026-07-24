"""DataBus Response Schema Validation"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.response_schema")

class SchemaValidator:
    """Lightweight schema validation for DataBus responses.

    Each data type has an expected schema. If a provider returns data
    that doesn't match, the DataBus falls back to the next provider.
    """

    SCHEMAS = {
        "token_price": {
            "required": ["price_usd"],
            "optional": ["change_24h", "volume_24h", "market_cap"],
        },
        "wallet_labels": {"required": ["label"], "optional": ["source", "confidence", "category"]},
        "risk_scan": {
            "required": ["risk_score"],
            "optional": ["is_honeypot", "threats", "risk_factors"],
        },
        "entity_intel": {
            "required": ["entity_name"],
            "optional": ["category", "addresses", "links"],
        },
        "arkham_entity": {
            "required": ["entity_name"],
            "optional": ["category", "description", "website"],
        },
        "arkham_portfolio": {
            "required": ["total_value_usd"],
            "optional": ["token_count", "tokens", "chain_exposures"],
        },
        "market_overview": {
            "required": ["total_mcap"],
            "optional": ["btc_dom", "eth_dom", "fgi", "volume_24h"],
        },
        "trending": {
            "required": ["name"],
            "optional": ["symbol", "price_usd", "change_24h", "volume_24h"],
        },
        "funding_source": {
            "required": ["funders"],
            "optional": ["first_funder", "funding_tx_count", "source_type"],
        },
        "alerts": {"required": ["alerts"], "optional": ["count", "severity"]},
        "dex_data": {
            "required": ["pair_address"],
            "optional": ["liquidity", "volume_24h", "price_usd"],
        },
        "news": {
            "required": ["title"],
            "optional": ["source_name", "published_at", "url", "sentiment"],
        },
        "threat_check": {
            "required": ["threat_score"],
            "optional": ["threat_detected", "threats", "recommendation"],
        },
    }

    def validate(self, data_type: str, data: Any) -> tuple:
        """Validate response data against expected schema.
        Returns (is_valid, missing_fields).
        """
        if not isinstance(data, dict):
            return False, ["data must be dict"]
        schema = self.SCHEMAS.get(data_type)
        if not schema:
            return True, []  # No schema = pass through
        required = schema.get("required", [])
        missing = [f for f in required if f not in data]
        if missing:
            return False, missing
        return True, []

    def check_response(self, data_type: str, result: dict) -> dict:
        """Check a full DataBus response dict. Returns annotated result."""
        if not result or "data" not in result:
            return result
        data = result["data"]
        is_valid, missing = self.validate(data_type, data)
        result["schema_valid"] = is_valid
        if not is_valid:
            result["schema_missing"] = missing
            logger.warning(f"Schema validation failed for {data_type}: missing {missing}")
        return result

# Module-level singleton instance
schema_validator = SchemaValidator()
