"""Federated label API - orchestrates all 6 sources.

Per RMIV5 §T11: queries every configured source in parallel via
asyncio.gather(return_exceptions=True). Failed sources are logged
but don't fail the whole query - graceful degradation.

Deduplication: labels from different sources for the same
(address, chain, label_type, label_value) get merged, keeping the
highest-confidence version and tagging all contributing sources.

Caching: Redis-backed cache with 1h TTL keyed by (address, chain).
On cache hit, skip source queries entirely.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

log = get_logger(__name__)


# Default sources to query. Subclasses can override.
DEFAULT_SOURCES: list[LabelSource] = [
    LabelSource.ETH_LABELS_DB,
    LabelSource.ETHERSCAN_CSV,
    LabelSource.ETHEREUM_LABELS_CSV,
    LabelSource.SOLANA_LABELS_CSV,
    LabelSource.CLICKHOUSE,
    LabelSource.METASLEUTH,
    LabelSource.MBAL,
]


class FederatedLabelAPI:
    """Orchestrates wallet label queries across all configured sources.

    Usage:
        api = FederatedLabelAPI()
        labels = await api.get_labels("0x...", "ethereum")
        # Returns list[Label] aggregated from all sources

        sources_checked = api.sources_queried_last_call  # for observability
    """

    def __init__(
        self,
        sources: list[LabelSource] | None = None,
        cache_ttl_seconds: int = 3600,
        per_source_timeout: float = 5.0,
        max_concurrent: int = 6,
    ) -> None:
        self._sources = sources or DEFAULT_SOURCES
        self._cache_ttl = cache_ttl_seconds
        self._per_source_timeout = per_source_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._sources_queried_last_call: list[str] = []

    async def get_labels(
        self,
        address: str,
        chain: str = "ethereum",
        *,
        use_cache: bool = True,
        include_low_confidence: bool = False,
    ) -> list[Label]:
        """Get all labels for an address from all sources.

        Args:
            address: Wallet address (0x... for EVM, base58 for Solana, etc.)
            chain: Chain name (ethereum, solana, base, bitcoin, etc.)
            use_cache: If True, check Redis cache first (default True)
            include_low_confidence: If False (default), filter labels with
                                    confidence < 0.3 to reduce noise

        Returns:
            Deduplicated list of labels, sorted by confidence desc.
        """
        # Cache check
        if use_cache:
            cached = await self._cache_get(address, chain)
            if cached is not None:
                return cached

        # Query all sources in parallel
        tasks = [self._query_source_safe(source, address, chain) for source in self._sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect labels + track which sources worked
        all_labels: list[Label] = []
        sources_queried: list[str] = []
        for source, result in zip(self._sources, results, strict=False):
            if isinstance(result, BaseException):
                log.warning(
                    "federated_source_fail source=%s err=%s: %s",
                    source.value,
                    type(result).__name__,
                    result,
                )
                continue
            if result:
                sources_queried.append(source.value)
                all_labels.extend(result)

        # Deduplicate (merge same label from multiple sources)
        deduped = self._dedupe(all_labels)

        # Filter low confidence unless requested
        if not include_low_confidence:
            deduped = [line for line in deduped if line.confidence >= 0.3]

        # Sort by confidence desc
        deduped.sort(key=lambda line: -line.confidence)

        # Track for observability
        self._sources_queried_last_call = sources_queried

        # Cache
        if use_cache and deduped:
            await self._cache_set(address, chain, deduped)

        log.info(
            "federated_labels_query address=%s chain=%s sources=%d labels=%d",
            address[:10] + "..." if len(address) > 10 else address,
            chain,
            len(sources_queried),
            len(deduped),
        )
        return deduped

    async def get_labels_dict(
        self,
        address: str,
        chain: str = "ethereum",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Get labels as a dict (for JSON API responses).

        Returns:
            {
              "address": "0x...",
              "chain": "ethereum",
              "labels": [...],  # list of label dicts
              "sources_queried": [...],  # list of source names that worked
              "total": N,
              "verified_at": "2026-06-22T..."
            }
        """
        labels = await self.get_labels(address, chain, **kwargs)
        return {
            "address": address,
            "chain": chain,
            "labels": [line.to_dict() for line in labels],
            "sources_queried": self._sources_queried_last_call,
            "total": len(labels),
        }

    async def _query_source_safe(
        self,
        source: LabelSource,
        address: str,
        chain: str,
    ) -> list[Label]:
        """Query a single source with timeout + semaphore + exception handling."""
        async with self._semaphore:
            try:
                return await asyncio.wait_for(
                    self._query_source(source, address, chain),
                    timeout=self._per_source_timeout,
                )
            except TimeoutError:
                log.warning(
                    "federated_source_timeout source=%s address=%s", source.value, address[:10]
                )
                return []
            except (KeyError, TypeError) as e :
                log.warning(
                    "federated_source_error source=%s err=%s: %s",
                    source.value,
                    type(e).__name__,
                    e,
                )
                return []

    async def _query_source(
        self,
        source: LabelSource,
        address: str,
        chain: str,
    ) -> list[Label]:
        """Dispatch to the correct source adapter. Each adapter handles its own logic."""
        if source == LabelSource.ETH_LABELS_DB:
            from app.domains.labels.sources.eth_labels_db import query_eth_labels_db

            return await query_eth_labels_db(address, chain)
        if source == LabelSource.ETHERSCAN_CSV:
            from app.domains.labels.sources.etherscan_csv import query_etherscan_csv

            return await query_etherscan_csv(address, chain)
        if source == LabelSource.ETHEREUM_LABELS_CSV:
            from app.domains.labels.sources.ethereum_labels_csv import query_ethereum_labels_csv

            return await query_ethereum_labels_csv(address, chain)
        if source == LabelSource.SOLANA_LABELS_CSV:
            from app.domains.labels.sources.solana_labels_csv import query_solana_labels_csv

            return await query_solana_labels_csv(address, chain)
        if source == LabelSource.CLICKHOUSE:
            from app.domains.labels.sources.clickhouse_labels import query_clickhouse_labels

            return await query_clickhouse_labels(address, chain)
        if source == LabelSource.METASLEUTH:
            from app.domains.labels.sources.metasleuth import query_metasleuth

            return await query_metasleuth(address, chain)
        if source == LabelSource.OPEN_LABELS:
            from app.domains.labels.sources.open_labels import query_open_labels

            return await query_open_labels(address, chain)
        if source == LabelSource.CHAINBASE:
            from app.domains.labels.sources.chainbase import query_chainbase

            return await query_chainbase(address, chain)
        if source == LabelSource.INTERNAL:
            from app.domains.labels.sources.internal_labels import query_internal

            return await query_internal(address, chain)
        if source == LabelSource.MBAL:
            from app.domains.labels.sources.mbal import query_mbal

            return await query_mbal(address, chain)
        log.warning("federated_source_unknown source=%s", source.value)
        return []

    def _dedupe(self, labels: list[Label]) -> list[Label]:
        """Merge labels with same dedup_key into one (keeping highest confidence)."""
        by_key: dict[tuple[str, str, str, str], Label] = {}
        for label in labels:
            key = Label.dedupe_key(label.address, label.chain, label.label_type, label.label_value)
            if key not in by_key:
                by_key[key] = label
            else:
                by_key[key] = Label.merge([by_key[key], label])
        return list(by_key.values())

    async def _cache_get(self, address: str, chain: str) -> list[Label] | None:
        """Get cached labels from Redis. Returns None on miss/error."""
        try:
            import json as _json

            from app.core.redis import get_redis

            r = get_redis()
            if r is None:
                return None
            key = self._cache_key(address, chain)
            raw = r.get(key)
            if not raw:
                return None
            data = _json.loads(raw)
            # Reconstruct Label objects (don't restore full provenance)
            return [
                Label(
                    address=line["address"],
                    chain=line["chain"],
                    label_type=line["label_type"],
                    label_value=line["label_value"],
                    source=LabelSource(line["source"])
                    if line["source"] in [s.value for s in LabelSource]
                    else LabelSource.INTERNAL,
                    confidence=line["confidence"],
                    verified_at=line["verified_at"],
                    attributes=line.get("attributes", {}),
                    entity=line.get("entity"),
                    entity_category=line.get("entity_category"),
                )
                for line in data
            ]
        except (ConnectionError, OSError, ValueError, KeyError, TypeError) as e :
            log.debug("federated_cache_get_fail: %s", e)
            return None

    async def _cache_set(self, address: str, chain: str, labels: list[Label]) -> None:
        """Cache labels to Redis with TTL."""
        try:
            import json as _json

            from app.core.redis import get_redis

            r = get_redis()
            if r is None:
                return
            key = self._cache_key(address, chain)
            data = [line.to_dict() for line in labels]
            r.setex(key, self._cache_ttl, _json.dumps(data))
        except (ConnectionError, OSError, ValueError) as e :
            log.debug("federated_cache_set_fail: %s", e)

    def _cache_key(self, address: str, chain: str) -> str:
        """Redis key for cached labels. Includes a hash of the address (privacy)."""
        from app.core.rate_limiter import _hash_identifier

        return f"labels:{chain}:{_hash_identifier(address.lower())}"


_default_instance: FederatedLabelAPI | None = None


def get_federated_api() -> FederatedLabelAPI:
    """Get a process-wide FederatedLabelAPI instance."""
    global _default_instance
    if _default_instance is None:
        _default_instance = FederatedLabelAPI()
    return _default_instance
