r"""
Entity Extraction + Exact-Match Lookup for Crypto RAG
=====================================================
Fast regex-based entity extraction for crypto-specific entities.
Redis-backed exact-match index that bypasses vector similarity for
entity-heavy queries - critical because cosine similarity CANNOT
find 0xabc123... addresses or exact $SYMBOL tokens.

Entity types:
  - evm_address      : 0x[0-9a-fA-F]{40}
  - solana_address   : [1-9A-HJ-NP-Za-km-z]{32,44}  (base58)
  - token_symbol     : $[A-Z]{2,10}
  - chain_name       : ethereum, solana, base, bsc, ...
  - protocol_name    : uniswap, aave, curve, ...
  - scam_keyword     : rug pull, honeypot, flash loan attack, ...
  - tx_hash          : 0x[0-9a-f]{64}
  - ens_domain       : [a-z0-9]+\.eth

Redis key layout:
  entity:{type}:{value}  -> sorted set  member=JSON({doc_id,metadata}), score=relevance
  entity:doc:{doc_id}    -> hash        {entity_type: JSON([values])}
  entity:type:{type}     -> set         {value1, value2, ...}
"""

import json
import logging
import os
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# Redis config (matches rag_service conventions)
# ════════════════════════════════════════════════════════════════════

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# ════════════════════════════════════════════════════════════════════
# Entity pattern definitions
# ════════════════════════════════════════════════════════════════════

CHAIN_NAMES: set[str] = {
    "ethereum",
    "solana",
    "base",
    "bsc",
    "arbitrum",
    "polygon",
    "optimism",
    "avalanche",
    "fantom",
}

PROTOCOL_NAMES: set[str] = {
    "uniswap",
    "aave",
    "compound",
    "curve",
    "lido",
    "makerdao",
    "pancakeswap",
    "sushiswap",
    "raydium",
    "jupiter",
    "orca",
}

SCAM_KEYWORDS: set[str] = {
    "rug pull",
    "honeypot",
    "flash loan attack",
    "drain",
    "exploit",
    "hack",
    "phishing",
    "scam",
    "wash trading",
}

# Compiled regex patterns
# NOTE: tx_hash (64 hex) is matched BEFORE evm_address (40 hex),
# because the 64-char pattern is more specific and would otherwise
# be captured as an EVM address + extra chars.

_TX_HASH_RE = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
_EVM_ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_TOKEN_SYMBOL_RE = re.compile(r"\$[A-Z]{2,10}")
_ENS_DOMAIN_RE = re.compile(r"\b[a-z0-9]+\.eth\b")


# ════════════════════════════════════════════════════════════════════
# Data classes
# ════════════════════════════════════════════════════════════════════


@dataclass
class EntityExtractionResult:
    """Result of extracting entities from a text blob."""

    evm_addresses: list[str] = field(default_factory=list)
    solana_addresses: list[str] = field(default_factory=list)
    token_symbols: list[str] = field(default_factory=list)
    chain_names: list[str] = field(default_factory=list)
    protocol_names: list[str] = field(default_factory=list)
    scam_keywords: list[str] = field(default_factory=list)
    tx_hashes: list[str] = field(default_factory=list)
    ens_domains: list[str] = field(default_factory=list)

    @property
    def has_entities(self) -> bool:
        """True if any entity was extracted."""
        return bool(
            self.evm_addresses
            or self.solana_addresses
            or self.token_symbols
            or self.chain_names
            or self.protocol_names
            or self.scam_keywords
            or self.tx_hashes
            or self.ens_domains
        )

    @property
    def total_count(self) -> int:
        """Total number of distinct entities found."""
        return (
            len(self.evm_addresses)
            + len(self.solana_addresses)
            + len(self.token_symbols)
            + len(self.chain_names)
            + len(self.protocol_names)
            + len(self.scam_keywords)
            + len(self.tx_hashes)
            + len(self.ens_domains)
        )

    def all_entities(self) -> list[dict[str, str]]:
        """Flatten into a list of {type, value} dicts (normalised)."""
        out: list[dict[str, str]] = []
        for v in self.evm_addresses:
            out.append({"type": "evm_address", "value": v.lower()})
        for v in self.solana_addresses:
            out.append({"type": "solana_address", "value": v})
        for v in self.token_symbols:
            out.append({"type": "token_symbol", "value": v.upper()})
        for v in self.chain_names:
            out.append({"type": "chain_name", "value": v.lower()})
        for v in self.protocol_names:
            out.append({"type": "protocol_name", "value": v.lower()})
        for v in self.scam_keywords:
            out.append({"type": "scam_keyword", "value": v.lower()})
        for v in self.tx_hashes:
            out.append({"type": "tx_hash", "value": v.lower()})
        for v in self.ens_domains:
            out.append({"type": "ens_domain", "value": v.lower()})
        return out

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "evm_addresses": self.evm_addresses,
            "solana_addresses": self.solana_addresses,
            "token_symbols": self.token_symbols,
            "chain_names": self.chain_names,
            "protocol_names": self.protocol_names,
            "scam_keywords": self.scam_keywords,
            "tx_hashes": self.tx_hashes,
            "ens_domains": self.ens_domains,
            "has_entities": self.has_entities,
            "total_count": self.total_count,
        }


# ════════════════════════════════════════════════════════════════════
# extract_entities - pure function, no I/O
# ════════════════════════════════════════════════════════════════════


def extract_entities(text: str) -> EntityExtractionResult:
    """
    Extract crypto-specific entities from *text* using regex only.
    No external API or ML model required - runs in microseconds.

    Matching strategy:
      - Transaction hashes are matched first (64 hex chars); their spans
        are recorded so EVM addresses (40 hex chars) that overlap a tx
        hash span are excluded, preventing double-matching.
      - Solana address regex is broad (base58); candidates that look like
        plain English words are filtered out.
    """
    if not text:
        return EntityExtractionResult()

    # --- Transaction hashes (64 hex chars) ---
    tx_hashes: list[str] = []
    tx_hash_set: set[str] = set()
    tx_spans: list[tuple[int, int]] = []
    for m in _TX_HASH_RE.finditer(text):
        val = m.group(0)
        if val not in tx_hash_set:
            tx_hash_set.add(val)
            tx_hashes.append(val)
        tx_spans.append((m.start(), m.end()))

    def _overlaps_tx(start: int, end: int) -> bool:
        return any(start >= ts and end <= te for ts, te in tx_spans)

    # --- EVM addresses (40 hex chars, not inside a tx hash) ---
    evm_addresses: list[str] = []
    evm_set: set[str] = set()
    for m in _EVM_ADDRESS_RE.finditer(text):
        if _overlaps_tx(m.start(), m.end()):
            continue
        val = m.group(0)
        if val not in evm_set:
            evm_set.add(val)
            evm_addresses.append(val)

    # --- Solana addresses (base58, 32-44 chars) ---
    solana_addresses: list[str] = []
    solana_set: set[str] = set()
    for m in _SOLANA_ADDRESS_RE.finditer(text):
        val = m.group(0)
        # Filter out common English words - purely alpha strings <= 10 chars
        if len(val) <= 10 and val.isalpha():
            continue
        # Skip ENS domains (covered separately)
        if val.endswith(".eth"):
            continue
        # Must look address-like: has digits OR mixed case
        has_digit = any(c.isdigit() for c in val)
        has_upper = any(c.isupper() for c in val)
        has_lower = any(c.islower() for c in val)
        if not has_digit and not (has_upper and has_lower) and len(val) < 32:
            continue
        if val not in solana_set:
            solana_set.add(val)
            solana_addresses.append(val)

    # --- Token symbols ($ETH, $SOL, ...) ---
    token_symbols: list[str] = []
    token_set: set[str] = set()
    for m in _TOKEN_SYMBOL_RE.finditer(text):
        val = m.group(0)
        if val not in token_set:
            token_set.add(val)
            token_symbols.append(val)

    # --- ENS domains ---
    ens_domains: list[str] = []
    ens_set: set[str] = set()
    for m in _ENS_DOMAIN_RE.finditer(text):
        val = m.group(0)
        if val not in ens_set:
            ens_set.add(val)
            ens_domains.append(val)

    # --- Chain names (whole-word, case-insensitive) ---
    chain_names: list[str] = []
    chain_set: set[str] = set()
    text_lower = text.lower()
    for chain in CHAIN_NAMES:
        pattern = re.compile(r"\b" + re.escape(chain) + r"\b")
        if pattern.search(text_lower) and chain not in chain_set:
            chain_set.add(chain)
            chain_names.append(chain)

    # --- Protocol names (whole-word, case-insensitive) ---
    protocol_names: list[str] = []
    protocol_set: set[str] = set()
    for proto in PROTOCOL_NAMES:
        pattern = re.compile(r"\b" + re.escape(proto) + r"\b")
        if pattern.search(text_lower) and proto not in protocol_set:
            protocol_set.add(proto)
            protocol_names.append(proto)

    # --- Scam keywords (phrase match, case-insensitive) ---
    scam_keywords: list[str] = []
    scam_set: set[str] = set()
    for kw in SCAM_KEYWORDS:
        pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        if pattern.search(text) and kw not in scam_set:
            scam_set.add(kw)
            scam_keywords.append(kw)

    return EntityExtractionResult(
        evm_addresses=evm_addresses,
        solana_addresses=solana_addresses,
        token_symbols=token_symbols,
        chain_names=chain_names,
        protocol_names=protocol_names,
        scam_keywords=scam_keywords,
        tx_hashes=tx_hashes,
        ens_domains=ens_domains,
    )


# ════════════════════════════════════════════════════════════════════
# EntityLookup - Redis-backed exact-match index
# ════════════════════════════════════════════════════════════════════


class EntityLookup:
    """
    Redis-backed exact-match entity index using sorted sets.

    Key schema:
        entity:{type}:{value}  -> sorted set  member=JSON({doc_id,metadata}), score=relevance
        entity:doc:{doc_id}    -> hash        {entity_type: JSON([values])}
        entity:type:{type}     -> set         {value1, value2, ...}

    The sorted-set score is used to rank docs by how strongly they
    relate to the entity (caller-supplied, defaults vary by type).
    """

    _instance: Optional["EntityLookup"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        redis_host: str = REDIS_HOST,
        redis_port: int = REDIS_PORT,
        redis_password: str = REDIS_PASSWORD,
        redis_db: int = 0,
    ):
        self._redis_host = redis_host
        self._redis_port = redis_port
        self._redis_password = redis_password
        self._redis_db = redis_db
        self._redis = None  # lazy async connection

    # ── Singleton ──────────────────────────────────────────────────

    @classmethod
    def get_instance(cls, **kwargs: Any) -> "EntityLookup":
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

    async def _get_redis(self):
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
        """
        Store an entity -> doc mapping in Redis.

        Args:
            entity_type:  e.g. "evm_address", "token_symbol"
            entity_value:  e.g. "0xabc123...", "$ETH"
            doc_id:        Document identifier
            metadata:      Optional metadata dict stored alongside
            score:         Relevance score for the sorted set (default 1.0)
        """
        try:
            r = await self._get_redis()

            # Normalise value for consistent lookups
            norm_value = self._normalise(entity_type, entity_value)

            # Sorted set: member = JSON({doc_id, metadata}), score = relevance
            entity_key = f"entity:{entity_type}:{norm_value}"
            member = json.dumps({"doc_id": doc_id, "metadata": metadata or {}})
            await r.zadd(entity_key, {member: score})

            # Track which values exist for a type (enables lookup_by_type)
            type_key = f"entity:type:{entity_type}"
            await r.sadd(type_key, norm_value)

            # Reverse index: which entities does this doc contain?
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
        """Normalise entity value for consistent storage/lookup.

        Lower-case types where case is irrelevant (addresses, hashes, etc.).
        Solana addresses are kept as-is because base58 is case-sensitive.
        """
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
        """
        Find all documents containing the exact entity value.
        Searches across all entity types.

        Returns:
            List of dicts: {doc_id, entity_type, metadata, score}
        """
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
        """
        Find all entities of a given type and their associated documents.

        Returns:
            List of dicts: {value, docs: [{doc_id, metadata, score}, ...]}
        """
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
        """
        Extract entities from *text* and index them all in one call.

        Args:
            doc_id:      Document identifier
            text:        Raw text to extract entities from
            collection:  RAG collection name (stored in metadata)
            metadata:    Optional extra metadata

        Returns:
            The EntityExtractionResult (what was found and indexed)
        """
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

        logger.info(
            "Batch-indexed %d entities for doc %s",
            extraction.total_count,
            doc_id,
        )
        return extraction

    @staticmethod
    def _entity_score(entity_type: str) -> float:
        """Default relevance score by entity type.

        High-value types (addresses, hashes) are far more discriminative
        than chain/protocol names, so they receive higher scores.
        """
        _SCORES: dict[str, float] = {
            "evm_address": 3.0,
            "solana_address": 3.0,
            "tx_hash": 3.0,
            "ens_domain": 2.5,
            "token_symbol": 2.0,
            "scam_keyword": 2.0,
            "protocol_name": 1.5,
            "chain_name": 1.0,
        }
        return _SCORES.get(entity_type, 1.0)

    # ── Maintenance ──────────────────────────────────────────────────

    async def remove_doc(self, doc_id: str) -> int:
        """
        Remove all entity references for a document.
        Returns the number of entity keys cleaned up.
        """
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
                    # Remove this doc's members from the sorted set
                    members = await r.zrange(entity_key, 0, -1)
                    for member_json in members:
                        try:
                            member = json.loads(member_json)
                            if member.get("doc_id") == doc_id:
                                await r.zrem(entity_key, member_json)
                                removed += 1
                        except json.JSONDecodeError:
                            pass
                    # Delete the sorted set if now empty
                    if await r.zcard(entity_key) == 0:
                        await r.delete(entity_key)

            # Delete the reverse-index hash
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


# ════════════════════════════════════════════════════════════════════
# Reciprocal Rank Fusion
# ════════════════════════════════════════════════════════════════════


def reciprocal_rank_fusion(
    *ranked_lists: list[dict[str, Any]],
    k: int = 60,
    entity_boost: float = 1.5,
    entity_doc_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Merge multiple ranked result lists using Reciprocal Rank Fusion.

    RRF score for a document = sum( 1 / (k + rank_i) ) across all lists
    where the document appears.  Documents in *entity_doc_ids* get an
    additional boost multiplier.

    Each item in the input lists must have a ``doc_id`` or ``id`` key.

    Args:
        ranked_lists:   One or more ranked result lists.
        k:              RRF constant (default 60 dampens rank effects).
        entity_boost:   Multiplier for docs from entity exact-matches.
        entity_doc_ids: Set of doc_ids that came from entity lookup.
    """
    entity_doc_ids = entity_doc_ids or set()
    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}

    for rlist in ranked_lists:
        for rank, item in enumerate(rlist, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = 1.0 / (k + rank)
            if doc_id in entity_doc_ids:
                rrf *= entity_boost
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item

    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    results: list[dict[str, Any]] = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = scores[doc_id]
        entry["entity_match"] = doc_id in entity_doc_ids
        results.append(entry)

    return results


# ════════════════════════════════════════════════════════════════════
# hybrid_query - the main entry point for entity-aware RAG
# ════════════════════════════════════════════════════════════════════


async def hybrid_query(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    vector_search_fn: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    """
    Entity-aware hybrid query: exact-match lookup FIRST, then vector
    search, merged with Reciprocal Rank Fusion.

    When a query contains an address, symbol, hash, or other exact entity,
    pure cosine similarity will almost never surface the right document.
    This function:

      1. Extracts entities from the query.
      2. If entities found, fetches exact-match docs from EntityLookup.
      3. Runs vector (semantic) search as usual.
      4. Merges both result sets with RRF, boosting entity matches.

    Args:
        query:            User query string.
        collections:      Collections to search (default: all).
        limit:            Max results to return.
        min_similarity:   Minimum cosine similarity for vector results.
        entity_boost:     RRF boost multiplier for entity-matched docs.
        vector_search_fn: Async callable for vector search.
                          Default: rag_service.search_multi_collection.

    Returns:
        Dict with keys:
          results           - merged, RRF-ranked list
          entity_extraction - EntityExtractionResult.to_dict()
          entity_docs       - docs from exact-match lookup
          vector_docs       - docs from vector search
    """
    # Late import to avoid circular dependency at module level
    from app.rag_service import search_multi_collection

    # 1. Extract entities from query
    extraction = extract_entities(query)

    # 2. Entity exact-match lookup
    entity_docs: list[dict[str, Any]] = []
    entity_doc_ids: set[str] = set()

    if extraction.has_entities:
        lookup = EntityLookup.get_instance()
        for entity in extraction.all_entities():
            try:
                matches = await lookup.lookup(entity["value"])
                for match in matches:
                    if match["doc_id"] not in entity_doc_ids:
                        entity_doc_ids.add(match["doc_id"])
                        entity_docs.append(match)
            except Exception as exc:
                logger.warning("Entity lookup error for %s: %s", entity["value"], exc)

    # 3. Vector (semantic) search
    vector_docs: list[dict[str, Any]] = []
    search_fn = vector_search_fn or search_multi_collection
    try:
        if collections:
            vector_docs = await search_fn(
                query,
                collections,
                limit=limit * 2,
                min_similarity=min_similarity,
            )
        else:
            vector_docs = await search_fn(
                query,
                limit=limit * 2,
                min_similarity=min_similarity,
            )
    except Exception as exc:
        logger.warning("Vector search failed in hybrid_query: %s", exc)

    # 4. Merge with RRF (entity list first = higher rank = more weight)
    entity_ranked = sorted(entity_docs, key=lambda d: d.get("score", 0), reverse=True)
    vector_ranked = sorted(vector_docs, key=lambda d: d.get("similarity", 0), reverse=True)

    merged = reciprocal_rank_fusion(
        entity_ranked,
        vector_ranked,
        k=60,
        entity_boost=entity_boost,
        entity_doc_ids=entity_doc_ids,
    )

    return {
        "results": merged[:limit],
        "entity_extraction": extraction.to_dict(),
        "entity_docs": entity_docs,
        "vector_docs": vector_docs[:limit],
    }


# ════════════════════════════════════════════════════════════════════
# Convenience singleton accessor
# ════════════════════════════════════════════════════════════════════


def get_entity_lookup() -> EntityLookup:
    """Return the singleton EntityLookup instance."""
    return EntityLookup.get_instance()
