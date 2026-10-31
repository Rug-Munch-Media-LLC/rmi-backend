"""Premium enrichment sources: GMGN holder analysis, Birdeye overview/security."""

from __future__ import annotations

from typing import Any

from app.observability.silent_failures import record_silent_failure
from app.providers.base import ProviderBase


class EnrichmentMixin(ProviderBase):
    """Optional enrichment that must never break the caller."""

    async def _fetch_gmgn_enrichment(self, mint: str, chain: str) -> dict[str, Any] | None:
        """Enrich with GMGN data if available."""
        try:
            from app.databus.gmgn_provider import get_gmgn_provider

            gmgn = get_gmgn_provider()
            holder_data = await gmgn.get_holder_analysis(mint, chain)
            smart_money = await gmgn.get_smart_money(mint, chain)

            result: dict[str, Any] = {}
            if holder_data and "error" not in holder_data:
                result["holders"] = holder_data.get("total_holders")
                result["top_holders"] = holder_data.get("top_holders", [])[:20]
            if smart_money and "error" not in smart_money:
                result["smart_money"] = smart_money
            return result if result else None
        except Exception as exc:
            record_silent_failure("databus", "unified_solana_enrichment", exc, fallback="none")
            return None

    async def _fetch_birdeye_enrichment(self, mint: str) -> dict[str, Any] | None:
        """Enrich with Birdeye data (holder count, security)."""
        try:
            if not self.birdeye:
                return None
            overview = await self.birdeye.get_token_overview(mint)
            d = overview.get("data", {}) if isinstance(overview, dict) else {}
            security = d.get("security", {})
            return {
                "holders": d.get("holder"),
                "security": {
                    "risk_score": security.get("risk_score"),
                    "risk_level": security.get("risk_level"),
                    "flags": security.get("flags", []),
                },
            }
        except Exception as exc:
            record_silent_failure("databus", "unified_birdeye_enrichment", exc, fallback="none")
            return None
