"""
Entity Extraction - Redis-Backed Exact-Match Lookup
=====================================================
Redis-backed exact-match index that bypasses vector similarity for
entity-heavy queries. Cosine similarity CANNOT find exact addresses.

Key schema:
  entity:{type}:{value}        -> sorted set  member=JSON({doc_id,metadata}), score=relevance
  entity:doc:{doc_id}          -> hash        {entity_type: JSON([values])}
  entity:type:{type}           -> set         {value1, value2, ...}
"""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.entity_extraction.models import EntityExtractionResult

logger = get_logger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")


class EntityLookup:
    """
    Redis-backed exact-match entity index using sorted sets.

    The sorted-set score ranks docs by how strongly they relate to
    the entity (caller-supplied, defaults vary by type).
    """

    _instance: EntityLookup | None = None
    _lock = threading.Lock()

    def __init__(
        self,
        redis_host: str = REDIS_HOST,
        redis_port: int = REDIS_PORT,
        redis_password: str = REDIS_PASSWORD,
        redis_db: int = 0,
    ) -> None:
        self._redis_host = redis_host
        self._redis_port = redis_port
        self._redis_password = redis_password
        self._redis_db = redis_db
        self._redis = None  # lazy async connection

    # ── Singleton ──────────────────────────────────────────────────

    @classmethod
    def get_instance(cls, **kwargs: Any) -> EntityLookup:
        """Return (and lazily create) the singleton EntityLookup."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for tests)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance = None

    # ── Redis connection ────────────────────────────────────────────

    async def _get_redis(self) -> Any:
        """Lazily create and return the async Redis client."""
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.Redis(
                host=self._redis_host,
                port=self._redis_port,
                password=self._redis_password or None,
                db=self._redis_db,
                decode_responses=True,
            )
        return self._redis

    # ── Indexing ────────────────────────────────────────────────────

    async def index_entity(
        self,
        entity_type: str,
        entity_value: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
        score: float = 1.0,
    ) -> None:
        """Store an entity -> doc mapping in Redis."""
        try:
            r = await self._get_redis()
            norm_value = self._normalise(entity_type, entity_value)

            entity_key = f"entity:{entity_type}:{norm_value}"
            member = json.dumps({"doc_id": doc_id, "metadata": metadata or {}})
            await r.zadd(entity_key, {member: score})

            type_key = f"entity:type:{entity_type}"
            await r.sadd(type_key, norm_value)

            doc_key = f"entity:doc:{doc_id}"
            existing_raw = await r.hget(doc_key, entity_type)
            if existing_raw is not None:
                values: list = json.loads(existing_raw)
            else:
                values = []
            if norm_value not in values:
                values.append(norm_value)
                await r.hset(doc_key, entity_type, json.dumps(values))

            logger.debug("Indexed entity %s=%s -> doc %s", entity_type, norm_value, doc_id)
        except Exception as exc:
            logger.error("Failed to index entity %s=%s: %s", entity_type, entity_value, exc)
            raise

    @staticmethod
    def _normalise(entity_type: str, entity_value: str) -> str:
        _lower_types = {
            "evm_address",
            "tx_hash",
            "chain_name",
            "protocol_name",
            "scam_keyword",
            "ens_domain",
            "token_symbol",
        }
        if entity_type in _lower_types:
            return entity_value.lower()
        return entity_value

    # ── Lookups ─────────────────────────────────────────────────────

    async def lookup(self, entity_value: str) -> list[dict[str, Any]]:
        """Find all documents containing the exact entity value."""
        try:
            r = await self._get_redis()
            results: list[dict[str, Any]] = []
            entity_types = [
                "evm_address",
                "solana_address",
                "token_symbol",
                "chain_name",
                "protocol_name",
                "scam_keyword",
                "tx_hash",
                "ens_domain",
            ]
            norm_value = entity_value.lower()

            for etype in entity_types:
                lookup_val = norm_value if etype != "solana_address" else entity_value
                key = f"entity:{etype}:{lookup_val}"
                members_with_scores = await r.zrange(key, 0, -1, withscores=True)
                for member_json, score in members_with_scores:
                    try:
                        member = json.loads(member_json)
                        results.append(
                            {
                                "doc_id": member["doc_id"],
                                "entity_type": etype,
                                "metadata": member.get("metadata", {}),
                                "score": float(score),
                            }
                        )
                    except (json.JSONDecodeError, KeyError) as parse_err:
                        logger.warning("Malformed entity member in %s: %s", key, parse_err)

            return results
        except Exception as exc:
            logger.error("Entity lookup failed for '%s': %s", entity_value, exc)
            return []

    async def lookup_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        """Find all entities of a given type and their associated documents."""
        try:
            r = await self._get_redis()
            type_key = f"entity:type:{entity_type}"
            values = await r.smembers(type_key)
            if not values:
                return []

            results: list[dict[str, Any]] = []
            for val in values:
                entity_key = f"entity:{entity_type}:{val}"
                members_with_scores = await r.zrange(entity_key, 0, -1, withscores=True)
                docs: list[dict[str, Any]] = []
                for member_json, score in members_with_scores:
                    try:
                        member = json.loads(member_json)
                        docs.append(
                            {
                                "doc_id": member["doc_id"],
                                "metadata": member.get("metadata", {}),
                                "score": float(score),
                            }
                        )
                    except (json.JSONDecodeError, KeyError) as parse_err:
                        logger.warning("Malformed member in %s: %s", entity_key, parse_err)
                if docs:
                    results.append({"value": val, "docs": docs})

            return results
        except Exception as exc:
            logger.error("lookup_by_type failed for '%s': %s", entity_type, exc)
            return []

    # ── Batch indexing ───────────────────────────────────────────────

    async def batch_index(
        self,
        doc_id: str,
        text: str,
        collection: str,
        metadata: dict[str, Any] | None = None,
    ) -> EntityExtractionResult:
        """Extract entities from text and index them all in one call."""
        from app.entity_extraction.extractor import extract_entities

        extraction = extract_entities(text)
        base_meta: dict[str, Any] = {
            "collection": collection,
            **(metadata or {}),
        }

        for entity in extraction.all_entities():
            score = self._entity_score(entity["type"])
            await self.index_entity(
                entity_type=entity["type"],
                entity_value=entity["value"],
                doc_id=doc_id,
                metadata=base_meta,
                score=score,
            )

        logger.info("Batch-indexed %d entities for doc %s", extraction.total_count, doc_id)
        return extraction

    @staticmethod
    def _entity_score(entity_type: str) -> float:
        scores: dict[str, float] = {
            "evm_address": 3.0,
            "solana_address": 3.0,
            "tx_hash": 3.0,
            "ens_domain": 2.5,
            "token_symbol": 2.0,
            "scam_keyword": 2.0,
            "protocol_name": 1.5,
            "chain_name": 1.0,
        }
        return scores.get(entity_type, 1.0)

    # ── Maintenance ──────────────────────────────────────────────────

    async def remove_doc(self, doc_id: str) -> int:
        """Remove all entity references for a document."""
        try:
            r = await self._get_redis()
            doc_key = f"entity:doc:{doc_id}"
            entity_map = await r.hgetall(doc_key)
            if not entity_map:
                return 0

            removed = 0
            for entity_type, values_json in entity_map.items():
                try:
                    values = json.loads(values_json)
                except json.JSONDecodeError:
                    continue

                for val in values:
                    entity_key = f"entity:{entity_type}:{val}"
                    members = await r.zrange(entity_key, 0, -1)
                    for member_json in members:
                        try:
                            member = json.loads(member_json)
                            if member.get("doc_id") == doc_id:
                                await r.zrem(entity_key, member_json)
                                removed += 1
                        except json.JSONDecodeError:
                            pass
                    if await r.zcard(entity_key) == 0:
                        await r.delete(entity_key)

            await r.delete(doc_key)
            return removed
        except Exception as exc:
            logger.error("remove_doc failed for %s: %s", doc_id, exc)
            return 0

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
