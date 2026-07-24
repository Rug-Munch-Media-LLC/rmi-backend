"""

MBAL (Multi-blockchain Anti-Laundering) Label Integration

Integrates with MBAL's 10M+ wallet labels via the MBAL API or direct ClickHouse access.
MBAL provides comprehensive blockchain attribution, wallet clustering, and risk scoring
across Bitcoin, Ethereum, and 15+ major chains.

API Documentation: https://mbal.io/api
Rate Limits: Free tier - 100 queries/day, Pro - 1000/day, Enterprise - Unlimited
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

logger = get_logger(__name__)


class MBALService:
    """MBAL API Integration for multi-blockchain wallet labels"""

    def __init__(self, api_key: str | None = None) -> None:
        """
        Initialize MBAL service.

        Args:
            api_key: MBAL API key. If None, uses free tier with limitations.
        """
        self.base_url = "https://api.mbal.io/v1"
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        # Cache for rate limiting
        self.last_request_time: float = 0.0
        self.request_count: int = 0
        self.reset_time: float = 0.0  # Unix timestamp when quota resets

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_labels(self, address: str, chain: str) -> list[Label]:
        """
        Retrieve labels for a blockchain address from MBAL.

        Args:
            address: The blockchain address to label
            chain: The blockchain (ethereum, bitcoin, tron, etc.)

        Returns:
            List of labels with metadata
        """
        try:
            # MBAL requires different endpoints per chain
            labels = []

            # Prepare query payload
            params = {
                "address": address,
                "chain": chain.lower(),
            }

            # Rate limiting - max 100 requests per day for free
            await self._rate_limit()

            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            # Query MBAL's address endpoint
            response = await self.client.get(
                f"{self.base_url}/address", params=params, headers=headers
            )
            response.raise_for_status()

            data = response.json()

            # Process the result according to MBAL's schema
            if "entities" in data:
                for entity in data["entities"]:
                    label_value = entity.get("category", "unknown")
                    entity.get("type", "address")
                    confidence = entity.get("confidence", 0.8)  # MBAL typically confident

                    # Map MBAL categories to our standardized label types
                    label_type = self._map_category_to_type(label_value)

                    labels.append(
                        Label(
                            address=address,
                            chain=chain,
                            label_type=label_type,
                            label_value=label_value,
                            source=LabelSource.MBAL,  # MBAL source
                            verified_at=datetime.now(),
                            confidence=confidence,
                            attributes={
                                "mbal_entity_id": entity.get("entityId"),
                                "mbal_tags": entity.get("tags", []),
                                "mbal_risk_score": entity.get("riskScore", 0),
                                "mbal_cluster_id": entity.get("clusterId"),
                            },
                        )
                    )

            if "behaviors" in data and len(labels) == 0:
                # If no main entities, fall back to behavioral labels
                for behavior in data["behaviors"]:
                    label_value = behavior["type"]
                    label_type = self._map_behavior_to_type(label_value)
                    confidence = behavior.get("confidence", 0.7)

                    labels.append(
                        Label(
                            address=address,
                            chain=chain,
                            label_type=label_type,
                            label_value=label_value,
                            source=LabelSource.MBAL,  # MBAL API source
                            verified_at=datetime.now(),
                            confidence=confidence,
                            attributes={
                                "behavior_description": behavior.get("description"),
                                "behavior_risk_level": behavior.get("riskLevel", "medium"),
                            },
                        )
                    )

            return labels

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"MBAL rate limit exceeded: {e}")
                return []
            logger.error(f"MBAL API error: {e} for {chain}:{address}")
            return []
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            KeyError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Unexpected error querying MBAL: {e}")
            return []

    def _map_category_to_type(self, category: str) -> str:
        """Map MBAL category strings to our standard label type strings."""
        category_lower = category.lower()

        # Exchange mappings
        if any(
            exchange in category_lower
            for exchange in [
                "binance",
                "coinbase",
                "kraken",
                "kucoin",
                "huobi",
                "gate.io",
                "okx",
                "bybit",
                "gemini",
            ]
        ):
            return "exchange"

        # Service mappings
        if any(
            service in category_lower
            for service in [
                "miner",
                "validator",
                "staking",
                "liquidity",
                "amm",
                "dex",
                "bridge",
                "cex",
                "dexe",
            ]
        ):
            return "service"

        # Actor mappings
        if any(
            actor in category_lower
            for actor in [
                "blacklist",
                "scammer",
                "hacker",
                "tornado",
                "mixer",
                "launderer",
                "crime",
                "malicious",
            ]
        ):
            return "actor"

        # Default to entity for unknown but potentially identifiable categories
        return "actor" if "blacklist" in category_lower else "entity"

    def _map_behavior_to_type(self, behavior: str) -> str:
        """Map MBAL behaviors to our standard label type strings."""
        behavior_lower = behavior.lower()

        if any(
            risky in behavior_lower
            for risky in ["malware", "ransom", "exploit", "theft", "scam", "hacking", "malicious"]
        ):
            return "actor"

        if any(
            behavior in behavior_lower
            for behavior in ["mining", "block generation", "transaction fee", "gas"]
        ):
            return "service"

        return "entity"  # Default

    async def _rate_limit(self) -> None:
        """Implement simple rate limiting based on MBAL tier."""
        now = asyncio.get_event_loop().time()

        # Check daily quota if using free tier
        if not self.api_key and self.request_count >= 100:
            # Free tier: 100 queries/day
            # Simple approach: wait until reset (24 hours from first query)
            if now < self.reset_time:
                sleep_time = self.reset_time - now
                logger.info(f"Waiting for MBAL daily quota reset: {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)

        # Ensure minimum delay between requests
        time_since_last = now - self.last_request_time
        min_delay = 1.0  # At least 1s between requests
        if time_since_last < min_delay:
            await asyncio.sleep(min_delay - time_since_last)

        # Update counters
        self.last_request_time = now
        if now >= self.reset_time:
            self.request_count = 0
            self.reset_time = now + 86400  # Reset in 24 hours
        self.request_count += 1

    async def health_check(self) -> bool:
        """
        Check if MBAL service is accessible.

        Returns:
            True if MBAL API responds successfully
        """
        try:
            response = await self.client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except (httpx.HTTPError, ConnectionError, TimeoutError, OSError):
            return False


# Global instance
_mbalsy_instance: MBALService | None = None


def get_mbalsy_service() -> MBALService:
    """Get the global MBAL service instance."""
    global _mbalsy_instance
    if _mbalsy_instance is None:
        import os

        api_key = os.getenv("MBAL_API_KEY", None)
        _mbalsy_instance = MBALService(api_key=api_key)
    return _mbalsy_instance
