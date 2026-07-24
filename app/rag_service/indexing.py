"""Document ingestion pipeline for RAG."""

import hashlib
import json
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.crypto_embeddings import COLLECTIONS, EmbeddingResult, get_embedder
from app.rag_service.state import _pattern_cache, _pattern_cache_loaded, _seeded
from app.rag_service.cache import _get_redis

logger = get_logger(__name__)


async def _preembed_scam_patterns() -> None:
    """Pre-embed all KNOWN_SCAM_PATTERNS and cache results."""
    global _pattern_cache, _pattern_cache_loaded
    from app.crypto_embeddings import KNOWN_SCAM_PATTERNS

    embedder = await get_embedder()
    r = await _get_redis()

    for pattern in KNOWN_SCAM_PATTERNS:
        pattern_name = pattern["name"]
        if pattern_name in _pattern_cache:
            continue

        redis_key = f"rag:pattern_cache:{pattern_name}"
        try:
            cached_data = await r.get(redis_key)
            if cached_data:
                cached = json.loads(cached_data)
                _pattern_cache[pattern_name] = EmbeddingResult(
                    vector=cached["vector"], dims=cached["dims"],
                    model=cached["model"], head=cached.get("head", "scam_pattern"),
                )
                continue
        except (json.JSONDecodeError):
            pass

        try:
            result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"], description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            _pattern_cache[pattern_name] = result
            try:
                await r.setex(redis_key, 86400 * 30, json.dumps({
                    "vector": result.vector, "dims": result.dims,
                    "model": result.model, "head": result.head,
                }))
            except (json.JSONDecodeError):
                pass
            logger.info(f"Pre-embedded scam pattern: {pattern_name}")
        except Exception as e:
            logger.warning(f"Failed to pre-embed pattern {pattern_name}: {e}")

    _pattern_cache_loaded = True


async def _embed_by_collection(embedder, collection: str, content: str, metadata: dict) -> EmbeddingResult:
    """Route to the right embedding head based on collection type."""
    if collection in ("scam_patterns", "known_scams"):
        return await embedder.embed_scam_pattern(
            pattern_name=metadata.get("name", content[:50]),
            description=content, severity=metadata.get("severity", "medium"),
        )
    elif collection == "token_analysis":
        code = metadata.get("contract_code", "")
        return await embedder.embed_token_scam(
            name=metadata.get("name", ""), symbol=metadata.get("symbol", ""),
            description=content, contract_code=code,
            chain=metadata.get("chain", "solana"), metadata=metadata,
        )
    elif collection == "wallet_profiles":
        return await embedder.embed_wallet(
            address=metadata.get("address", ""), labels=metadata.get("labels", []),
            transactions=metadata.get("transactions", []),
            chain=metadata.get("chain", "solana"),
            balance_usd=float(metadata.get("balance_usd", 0) or 0),
        )
    elif collection == "contract_audits":
        vec = await embedder._semantic_embed_one(content, "semantic")
        return EmbeddingResult(vector=vec, dims=len(vec), model="local/bge-small-en-v1.5", head="contract_audit")
    else:
        vec = await embedder._semantic_embed_one(content, "semantic")
        return EmbeddingResult(vector=vec, dims=len(vec), model="openai/text-embedding-3-large", head="semantic")


async def ingest_document(
    collection: str, content: str, metadata: dict | None = None, doc_id: str | None = None,
) -> dict:
    """Ingest a document with real semantic embedding."""
    embedder = await get_embedder()
    metadata = metadata or {}

    if not doc_id:
        doc_id = hashlib.sha256(
            f"{collection}:{content[:100]}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]

    result = await _embed_by_collection(embedder, collection, content, metadata)

    r = await _get_redis()
    doc = {
        "id": doc_id, "collection": collection,
        "vector": result.vector, "dims": result.dims, "model": result.model,
        "metadata": {**metadata, "head": result.head},
        "content": content[:5000],
        "stored_at": datetime.now(UTC).isoformat(),
    }
    _TTL_MAP = {
        "scam_patterns": 0, "contract_audits": 0, "transaction_patterns": 0,
        "forensic_reports": 0, "wallet_profiles": 86400 * 365,
        "known_scams": 86400 * 365, "market_intel": 86400 * 365,
        "news_articles": 86400 * 30, "token_analysis": 86400 * 90, "general": 86400 * 30,
    }
    ttl = _TTL_MAP.get(collection, 86400 * 30)
    key = f"rag:{collection}:{doc_id}"
    if ttl == 0:
        await r.set(key, json.dumps(doc))
    else:
        await r.setex(key, ttl, json.dumps(doc))
    await r.sadd(f"rag:idx:{collection}", doc_id)

    # Bump ANN index version
    try:
        from app.ann_index import get_ann_index
        ann = get_ann_index()
        await ann.bump_version(collection)
    except (json.JSONDecodeError):
        pass

    # BM25 index invalidation
    try:
        from app.splade_bm25 import _bm25_built_at, _bm25_index
        if _bm25_index is not None:
            _bm25_built_at = 0
    except (AttributeError, ImportError):
        pass

    # Entity extraction + indexing
    try:
        from app.entity_extraction import EntityLookup, extract_entities
        entities = extract_entities(content)
        if entities.has_entities:
            lookup = EntityLookup.get_instance()
            for entity in entities.all_entities():
                await lookup.index_entity(entity["type"], entity["value"], doc_id, metadata={**metadata, "collection": collection})
    except (AttributeError, ImportError):
        pass

    # Knowledge Graph edge creation
    try:
        from app.knowledge_graph import get_knowledge_graph
        kg = await get_knowledge_graph()
        await kg.ingest_rag_document(collection, doc_id, content, metadata)
    except (AttributeError, ImportError):
        pass

    logger.info(f"Ingested {collection}/{doc_id}: {content[:60]}...")
    return {"id": doc_id, "dims": result.dims, "collection": collection}


async def seed_known_scams() -> dict:
    """Seed the RAG database with known scam patterns."""
    global _seeded
    from app.crypto_embeddings import KNOWN_SCAM_PATTERNS

    r = await _get_redis()
    existing = await r.scard("rag:idx:known_scams")
    if existing > 0 and _seeded:
        return {"status": "already_seeded", "count": existing}

    embedder = await get_embedder()
    count = 0

    for pattern in KNOWN_SCAM_PATTERNS:
        try:
            pid = hashlib.sha256(f"scam:{pattern['name']}".encode()).hexdigest()[:16]
            result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"], description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            doc = {
                "id": pid, "collection": "known_scams",
                "vector": result.vector, "dims": result.dims,
                "model": result.model, "metadata": pattern,
                "content": pattern["description"],
                "stored_at": datetime.now(UTC).isoformat(),
            }
            key = f"rag:known_scams:{pid}"
            await r.set(key, json.dumps(doc))
            await r.sadd("rag:idx:known_scams", pid)
            count += 1
        except Exception as e:
            logger.error(f"Failed to seed {pattern['name']}: {e}")

    _seeded = True
    return {"status": "seeded", "count": count, "patterns": [p["name"] for p in KNOWN_SCAM_PATTERNS]}


async def ingest_forensic_report(report_text: str, report_name: str = "forensic_report", metadata: dict | None = None) -> dict:
    """Ingest a forensic report into the RAG knowledge base (permanent storage)."""
    meta = {"name": report_name, "type": "forensic_report", **(metadata or {})}
    return await ingest_document(collection="forensic_reports", content=report_text, metadata=meta)
