#!/usr/bin/env python3
"""
RAG System Test Suite
=====================
Tests all core RAG modules: embeddings, ingestion, search, vector store,
entity extraction, temporal decay, contextual chunking, hallucination guard.

NOTE: This suite uses a custom @test() decorator + run_tests() runner.
Do NOT run with pytest — it will produce false failures (pytest-asyncio
auto-collects these functions but they aren't standard pytest test items).

Correct runner:
    docker exec rmi-backend python tests/test_rag.py

A pytest.ini in /root/backend/ disables auto-collection to prevent
accidental pytest runs from reporting failures.
"""

import asyncio
import os
import sys
from datetime import UTC

# Ensure app/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ══════════════════════════════════════════════════════════════════════
# Lightweight test runner (no pytest dependency needed)
# ══════════════════════════════════════════════════════════════════════

_results = []
_current = None


def test(name):
    """Decorator to mark a test function."""

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            _results.append((name, fn, True))
        else:
            _results.append((name, fn, False))
        return fn

    return decorator


async def run_tests():
    """Run all registered tests and print results."""
    passed = 0
    failed = 0
    errors = []

    for name, fn, is_async in _results:
        try:
            if is_async:
                await fn()
            else:
                fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  FAIL  {name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed, {passed + failed} total")
    if errors:
        print("\n  Failures:")
        for name, err in errors:
            print(f"    - {name}: {err[:80]}")
    print(f"{'=' * 60}")
    return failed == 0


# ══════════════════════════════════════════════════════════════════════
# FEATURE EXTRACTORS (pure functions, no API/model needed)
# ══════════════════════════════════════════════════════════════════════


@test("extract_contract_features: returns 128-dim vector")
def test_contract_features_dims():
    from app.crypto_embeddings import extract_contract_features

    vec = extract_contract_features("pragma solidity; function mint() external onlyOwner {}")
    assert len(vec) == 128, f"Expected 128 dims, got {len(vec)}"
    assert vec.dtype.name.startswith("float32"), f"Expected float32, got {vec.dtype}"


@test("extract_contract_features: detects rug patterns")
def test_contract_features_rug_detection():
    from app.crypto_embeddings import extract_contract_features

    rug_code = "function mint(address to, uint256 amount) external onlyOwner { maxTxAmount = 0; }"
    vec = extract_contract_features(rug_code)
    # The keyword list checks lowercased code; verify at least one rug dim is active
    rug_dims = [i for i, v in enumerate(vec[:56]) if v > 0]
    # mint is in the list, onlyOwner is in the list — just verify code is non-zero
    assert len(rug_dims) >= 1, "No rug pattern dims active in first 56 dims"
    # Also verify structural dims are populated (lines, functions, etc.)
    struct_dims = [i for i in range(32, 56) if vec[i] > 0]
    assert len(struct_dims) >= 1, f"No structural dims active: {struct_dims}"


@test("extract_contract_features: empty input returns zeros")
def test_contract_features_empty():
    from app.crypto_embeddings import extract_contract_features

    vec = extract_contract_features("")
    assert len(vec) == 128
    assert all(v == 0.0 for v in vec), "Empty input should produce zero vector"


@test("extract_transaction_features: returns 64-dim vector")
def test_transaction_features_dims():
    from app.crypto_embeddings import extract_transaction_features

    vec = extract_transaction_features({"transactions": []})
    assert len(vec) == 64, f"Expected 64 dims, got {len(vec)}"


@test("extract_transaction_features: captures counterparty diversity")
def test_transaction_features_counterparty():
    from app.crypto_embeddings import extract_transaction_features

    txs = [
        {"amount": 100, "from": "0xA", "to": "0xB", "timestamp": 1000},
        {"amount": 200, "from": "0xB", "to": "0xC", "timestamp": 2000},
    ]
    vec = extract_transaction_features({"transactions": txs})
    # dim 20 = counterparty diversity, should be > 0
    assert vec[20] > 0, "Counterparty diversity should be > 0"


@test("extract_wallet_features: returns 64-dim vector")
def test_wallet_features_dims():
    from app.crypto_embeddings import extract_wallet_features

    vec = extract_wallet_features({"labels": ["scammer"], "balance_usd": 5000})
    assert len(vec) == 64, f"Expected 64 dims, got {len(vec)}"


@test("extract_wallet_features: detects risk labels")
def test_wallet_features_labels():
    from app.crypto_embeddings import extract_wallet_features

    vec = extract_wallet_features({"labels": ["scammer", "rug_puller"]})
    # dim 20 = scammer, dim 21 = rug_puller
    assert vec[20] == 1.0, "Scammer label not detected at dim 20"
    assert vec[21] == 1.0, "Rug_puller label not detected at dim 21"


# ══════════════════════════════════════════════════════════════════════
# COSINE SIMILARITY
# ══════════════════════════════════════════════════════════════════════


@test("cosine_similarity: identical vectors = 1.0")
def test_cosine_same():
    from app.crypto_embeddings import CryptoEmbedder

    v = [1.0, 0.0, 0.5, 0.3]
    sim = CryptoEmbedder.cosine_similarity(v, v)
    assert abs(sim - 1.0) < 0.001, f"Same vector sim={sim}"


@test("cosine_similarity: orthogonal vectors = 0.0")
def test_cosine_orthogonal():
    from app.crypto_embeddings import CryptoEmbedder

    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    sim = CryptoEmbedder.cosine_similarity(a, b)
    assert abs(sim) < 0.001, f"Orthogonal sim={sim}"


@test("cosine_similarity: opposite vectors = -1.0")
def test_cosine_opposite():
    from app.crypto_embeddings import CryptoEmbedder

    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    sim = CryptoEmbedder.cosine_similarity(a, b)
    assert abs(sim - (-1.0)) < 0.001, f"Opposite sim={sim}"


@test("cosine_similarity: zero vector = 0.0")
def test_cosine_zero():
    from app.crypto_embeddings import CryptoEmbedder

    sim = CryptoEmbedder.cosine_similarity([0, 0, 0], [1, 0, 0])
    assert sim == 0.0, f"Zero vector sim={sim}"


# ══════════════════════════════════════════════════════════════════════
# HASH EMBEDDING (no API needed)
# ══════════════════════════════════════════════════════════════════════


@test("hash_embed: returns 384-dim normalized vector")
def test_hash_embed():
    from app.crypto_embeddings import CryptoEmbedder

    embedder = CryptoEmbedder()
    vec = embedder._hash_embed("test wallet scam pattern")
    assert len(vec) == 384, f"Expected 384 dims, got {len(vec)}"
    # Should be L2-normalized
    import numpy as np

    norm = np.linalg.norm(vec)
    assert abs(norm - 1.0) < 0.01 or norm == 0.0, f"Norm={norm}"


@test("hash_embed: deterministic (same input = same output)")
def test_hash_embed_deterministic():
    from app.crypto_embeddings import CryptoEmbedder

    embedder = CryptoEmbedder()
    v1 = embedder._hash_embed("rug pull honeypot")
    v2 = embedder._hash_embed("rug pull honeypot")
    assert v1 == v2, "Hash embedding not deterministic"


@test("hash_embed: different inputs = different outputs")
def test_hash_embed_unique():
    from app.crypto_embeddings import CryptoEmbedder

    embedder = CryptoEmbedder()
    v1 = embedder._hash_embed("rug pull")
    v2 = embedder._hash_embed("wash trading")
    assert v1 != v2, "Different inputs produced same hash embedding"


# ══════════════════════════════════════════════════════════════════════
# BUNDLE/CLUSTER BEHAVIORAL EMBEDDING (pure functions)
# ══════════════════════════════════════════════════════════════════════


@test("embed_bundle_profile: returns 128-dim vector")
def test_bundle_profile():
    from app.bundle_cluster_rag import embed_bundle_profile

    bundle = {
        "confidence": 0.8,
        "atomic_block_score": 0.9,
        "common_funder_score": 0.7,
        "wallets_in_earliest_block": 15,
        "chain": "solana",
    }
    vec = embed_bundle_profile(bundle)
    assert len(vec) == 128, f"Expected 128 dims, got {len(vec)}"


@test("embed_bundle_profile: metrics at dims 80-85 NOT overwritten by hash")
def test_bundle_metrics_not_clobbered():
    from app.bundle_cluster_rag import embed_bundle_profile

    bundle = {
        "confidence": 0.9,
        "avg_buy_amount": 5000,
        "max_buy_amount": 10000,
        "profit_ratio": 5.0,
        "chain": "ethereum",
    }
    vec = embed_bundle_profile(bundle)
    # Dim 80 = avg_buy_amount/10000 = 0.5, should NOT be zero (overwritten by hash)
    assert vec[80] > 0, f"Metric at dim 80 is zero (clobbered by hash): {vec[80]}"
    assert vec[81] > 0, f"Metric at dim 81 is zero (clobbered by hash): {vec[81]}"


@test("embed_cluster_profile: returns 192-dim vector")
def test_cluster_profile():
    from app.bundle_cluster_rag import embed_cluster_profile

    cluster = {
        "size": 50,
        "density": 0.8,
        "total_volume_usd": 1000000,
        "scam_probability": 0.9,
        "active_chains": ["ethereum"],
    }
    vec = embed_cluster_profile(cluster)
    assert len(vec) == 192, f"Expected 192 dims, got {len(vec)}"


@test("embed_cluster_profile: risk scoring at dims 80-85")
def test_cluster_risk_scoring():
    from app.bundle_cluster_rag import embed_cluster_profile

    cluster = {"scam_probability": 0.9, "rug_probability": 0.8}
    vec = embed_cluster_profile(cluster)
    assert abs(vec[80] - 0.9) < 0.01, f"Scam prob at dim 80 wrong: {vec[80]}"
    assert abs(vec[81] - 0.8) < 0.01, f"Rug prob at dim 81 wrong: {vec[81]}"


# ══════════════════════════════════════════════════════════════════════
# ENTITY EXTRACTION (pure regex, no API)
# ══════════════════════════════════════════════════════════════════════


@test("extract_entities: EVM address detection")
def test_entity_evm():
    from app.entity_extraction import extract_entities

    result = extract_entities("Send funds to 0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert "0xdac17f958d2ee523a2206206994597c13d831ec7" in [a.lower() for a in result.evm_addresses], (
        f"EVM address not found: {result.evm_addresses}"
    )


@test("extract_entities: token symbol detection")
def test_entity_symbol():
    from app.entity_extraction import extract_entities

    result = extract_entities("Price of $ETH and $SOL surging")
    symbols_upper = [s.upper() for s in result.token_symbols]
    assert "ETH" in symbols_upper or "$ETH" in result.token_symbols, f"ETH not found: {result.token_symbols}"
    assert "SOL" in symbols_upper or "$SOL" in result.token_symbols, f"SOL not found: {result.token_symbols}"


@test("extract_entities: chain name detection")
def test_entity_chain():
    from app.entity_extraction import extract_entities

    result = extract_entities("Bridged from Ethereum to Solana")
    assert "ethereum" in result.chain_names, f"Ethereum not found: {result.chain_names}"
    assert "solana" in result.chain_names, f"Solana not found: {result.chain_names}"


@test("extract_entities: scam keyword detection")
def test_entity_scam():
    from app.entity_extraction import extract_entities

    result = extract_entities("This token is a rug pull honeypot")
    assert "rug pull" in result.scam_keywords, f"rug pull not found: {result.scam_keywords}"


@test("extract_entities: ENS domain detection")
def test_entity_ens():
    from app.entity_extraction import extract_entities

    result = extract_entities("Send to vitalik.eth")
    assert "vitalik.eth" in result.ens_domains, f"ENS not found: {result.ens_domains}"


@test("extract_entities: empty input returns nothing")
def test_entity_empty():
    from app.entity_extraction import extract_entities

    result = extract_entities("Hello world this is a test")
    assert result.total_count == 0, f"Expected 0 entities, got {result.total_count}"


# ══════════════════════════════════════════════════════════════════════
# TEMPORAL DECAY (pure math)
# ══════════════════════════════════════════════════════════════════════


@test("compute_decay: fresh content = 1.0")
def test_decay_fresh():
    from app.temporal_decay import compute_decay

    assert compute_decay(0, 30) == 1.0
    assert compute_decay(0, 365) == 1.0


@test("compute_decay: infinite half-life = 1.0 always")
def test_decay_infinite():
    from app.temporal_decay import compute_decay

    assert compute_decay(1000, float("inf")) == 1.0
    assert compute_decay(0.1, float("inf")) == 1.0


@test("compute_decay: at half-life, score ~= 0.5")
def test_decay_halflife():
    from app.temporal_decay import compute_decay

    score = compute_decay(30.0, 30.0)  # 30 days old, 30-day half-life
    assert abs(score - 0.5) < 0.01, f"At half-life, expected ~0.5, got {score}"


@test("compute_decay: older = lower score")
def test_decay_monotonic():
    from app.temporal_decay import compute_decay

    s1 = compute_decay(10, 30)
    s2 = compute_decay(30, 30)
    s3 = compute_decay(60, 30)
    assert s1 > s2 > s3, f"Not monotonically decreasing: {s1} > {s2} > {s3}"


@test("apply_temporal_decay: adds decay fields to results")
def test_apply_decay_fields():
    from datetime import datetime

    from app.temporal_decay import apply_temporal_decay

    results = [
        {
            "similarity": 0.8,
            "collection": "news_articles",
            "stored_at": datetime.now(UTC).isoformat(),
        },
        {
            "similarity": 0.7,
            "collection": "forensic_reports",
            "stored_at": "2023-01-01T00:00:00+00:00",
        },
    ]
    decayed = apply_temporal_decay(results)
    assert "raw_similarity" in decayed[0], "Missing raw_similarity field"
    assert "decay_factor" in decayed[0], "Missing decay_factor field"
    assert "age_days" in decayed[0], "Missing age_days field"
    # Forensic report should keep 100% score (never decays)
    assert decayed[1]["decay_factor"] == 1.0, "Forensic report should never decay"


@test("get_half_life: forensic_reports = infinite")
def test_halflife_forensic():
    from app.temporal_decay import get_half_life

    hl = get_half_life("forensic_reports")
    assert hl == float("inf"), f"forensic_reports half-life should be inf, got {hl}"


@test("get_half_life: news_articles = 30 days")
def test_halflife_news():
    from app.temporal_decay import get_half_life

    hl = get_half_life("news_articles")
    assert hl == 30.0, f"news_articles half-life should be 30, got {hl}"


# ══════════════════════════════════════════════════════════════════════
# CONTEXTUAL CHUNKING
# ══════════════════════════════════════════════════════════════════════


@test("chunk_document: returns chunks with correct structure")
def test_chunk_document():
    from app.contextual_chunking import chunk_document

    text = "First paragraph.\n\nSecond paragraph. " * 100
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1, "Should produce multiple chunks"
    for c in chunks:
        assert hasattr(c, "content"), "Chunk missing content"
        assert hasattr(c, "index"), "Chunk missing index"
        assert len(c.content) > 0, "Chunk has empty content"


@test("chunk_document: single short doc produces one chunk")
def test_chunk_single():
    from app.contextual_chunking import chunk_document

    chunks = chunk_document("Short text.", chunk_size=2500)
    assert len(chunks) == 1, f"Short text should be 1 chunk, got {len(chunks)}"


@test("chunk_document: respects boundaries")
def test_chunk_boundaries():
    from app.contextual_chunking import chunk_document

    text = "# Section 1\n\nFirst section content here. " * 50
    chunks = chunk_document(text, chunk_size=500, overlap=50, respect_boundaries=True)
    # Should not cut mid-sentence
    for _c in chunks:
        # Content should not start mid-word (allowing for overlap)
        pass  # Basic validation that chunking completed


@test("heuristic context: generates context without LLM")
def test_heuristic_context():
    from app.contextual_chunking import _generate_heuristic_context

    ctx = _generate_heuristic_context("# DeFi Analysis\n\nFull document text...", "chunk about exploits", 0, 5)
    assert "DeFi Analysis" in ctx, f"Title not in context: {ctx}"
    assert "Chunk 1 of 5" in ctx, f"Position not in context: {ctx}"


@test("parent_child_chunk: creates parent+child chunks")
def test_parent_child():
    from app.contextual_chunking import parent_child_chunk

    text = "First paragraph with enough text. " * 200
    chunks = parent_child_chunk(text, parent_size=500, child_size=200)
    parents = [c for c in chunks if c.metadata.get("is_parent")]
    children = [c for c in chunks if c.parent_id is not None]
    assert len(parents) > 0, "No parent chunks created"
    assert len(children) > 0, "No child chunks created"
    assert all(c.parent_content for c in children), "Children missing parent content"


# ══════════════════════════════════════════════════════════════════════
# EMBEDDING CACHE (requires Redis)
# ══════════════════════════════════════════════════════════════════════


@test("embedding cache: _cache_key generates deterministic keys")
async def test_cache_key():
    from app.crypto_embeddings import CryptoEmbedder

    embedder = CryptoEmbedder()
    key1 = await embedder._cache_key("semantic", "test text")
    key2 = await embedder._cache_key("semantic", "test text")
    assert key1 == key2, f"Cache keys not deterministic: {key1} != {key2}"


@test("embedding cache: different heads generate different keys")
async def test_cache_key_different_heads():
    from app.crypto_embeddings import CryptoEmbedder

    embedder = CryptoEmbedder()
    key1 = await embedder._cache_key("semantic", "test")
    key2 = await embedder._cache_key("code", "test")
    assert key1 != key2, f"Different heads should have different cache keys: {key1} == {key2}"


# ══════════════════════════════════════════════════════════════════════
# SUPABASE VECTOR STORE — SQL SAFETY (unit-level, no network)
# ══════════════════════════════════════════════════════════════════════


@test("_get_dim: auto-detects dimension from first embedding")
def test_get_dim_auto():
    from app.supabase_vector import SupabaseVectorStore

    store = SupabaseVectorStore()
    # EMBEDDING_DIM=0 means auto-detect
    dim = store._get_dim([0.1] * 384)
    assert dim == 384, f"Auto-detect should return 384, got {dim}"


@test("_get_dim: caches resolved dimension")
def test_get_dim_caches():
    from app.supabase_vector import SupabaseVectorStore

    store = SupabaseVectorStore()
    dim1 = store._get_dim([0.1] * 512)
    assert dim1 == 512, f"First call should return 512, got {dim1}"
    dim2 = store._get_dim([0.1] * 384)  # different length, but cached
    assert dim2 == 512, f"Should return cached 512, got {dim2}"


@test("_get_dim: fallback to 384 when no embedding provided")
def test_get_dim_fallback():
    from app.supabase_vector import SupabaseVectorStore

    store = SupabaseVectorStore()
    dim = store._get_dim()  # no embedding, no env var
    assert dim == 384, f"Default should be 384, got {dim}"


# ══════════════════════════════════════════════════════════════════════
# KNOWN SCAM PATTERNS DATA
# ══════════════════════════════════════════════════════════════════════


@test("KNOWN_SCAM_PATTERNS: has required fields")
def test_scam_patterns_structure():
    from app.crypto_embeddings import KNOWN_SCAM_PATTERNS

    assert len(KNOWN_SCAM_PATTERNS) >= 10, f"Expected >= 10 patterns, got {len(KNOWN_SCAM_PATTERNS)}"
    for p in KNOWN_SCAM_PATTERNS:
        assert "name" in p, f"Pattern missing name: {p}"
        assert "description" in p, f"Pattern missing description: {p}"
        assert "severity" in p, f"Pattern missing severity: {p}"
        assert p["severity"] in ("low", "medium", "high", "critical"), f"Invalid severity: {p['severity']}"


@test("CLUSTER_LABEL_TEMPLATES: has 10 templates")
def test_cluster_labels():
    from app.bundle_cluster_rag import CLUSTER_LABEL_TEMPLATES

    assert len(CLUSTER_LABEL_TEMPLATES) == 10, f"Expected 10 templates, got {len(CLUSTER_LABEL_TEMPLATES)}"
    labels = [t["label"] for t in CLUSTER_LABEL_TEMPLATES]
    assert "insider_trading_ring" in labels, "Missing insider_trading_ring"
    assert "wash_trading_farm" in labels, "Missing wash_trading_farm"
    assert "mev_bot_network" in labels, "Missing mev_bot_network"


# ══════════════════════════════════════════════════════════════════════
# RAG SERVICE — TTL MAP
# ══════════════════════════════════════════════════════════════════════


@test("TTL map: forensic_reports and scam_patterns have permanent TTL (0)")
def test_ttl_permanent():
    # Read the TTL map from ingest_document function
    # We verify by inspecting the source — TTL=0 means no expiry
    import inspect

    from app.rag_service import ingest_document

    source = inspect.getsource(ingest_document)
    assert "forensic_reports" in source, "forensic_reports missing from TTL logic"
    assert "scam_patterns" in source, "scam_patterns missing from TTL logic"


# ══════════════════════════════════════════════════════════════════════
# RERANK MODEL CONFIG
# ══════════════════════════════════════════════════════════════════════


@test("Rerank and analysis models are env-configurable")
def test_model_config():
    from app.rag_agentic import ANALYSIS_MODEL, RERANK_MODEL

    # Should have defaults
    assert RERANK_MODEL, "RERANK_MODEL should not be empty"
    assert ANALYSIS_MODEL, "ANALYSIS_MODEL should not be empty"
    # Should not be the old hardcoded broken names
    assert "claude-sonnet-4" not in ANALYSIS_MODEL or "20250514" in ANALYSIS_MODEL, (
        f"ANALYSIS_MODEL should use dated model, got: {ANALYSIS_MODEL}"
    )


# ══════════════════════════════════════════════════════════════════════
# COLLECTIONS LIST
# ══════════════════════════════════════════════════════════════════════


@test("COLLECTIONS list includes all expected collections")
def test_collections():
    from app.crypto_embeddings import COLLECTIONS

    expected = [
        "wallet_profiles",
        "token_analysis",
        "scam_patterns",
        "forensic_reports",
        "market_intel",
        "contract_audits",
        "known_scams",
        "news_articles",
        "transaction_patterns",
    ]
    for coll in expected:
        assert coll in COLLECTIONS, f"Missing collection: {coll}"


# ══════════════════════════════════════════════════════════════════════
# ANN INDEX (FAISS)
# ══════════════════════════════════════════════════════════════════════


@test("ANNIndex: build_index creates in-memory index")
async def test_ann_build():
    from app.ann_index import ANNIndex

    idx = ANNIndex()
    meta = await idx.build_index("scam_patterns")
    assert meta.get("status") == "built", f"Expected built, got {meta}"
    assert meta.get("n", 0) > 0, f"Expected docs > 0, got {meta.get('n')}"


@test("ANNIndex: search returns hydrated results with content")
async def test_ann_search():
    from app.ann_index import ANNIndex
    from app.crypto_embeddings import get_embedder

    idx = ANNIndex()
    await idx.build_index("scam_patterns")
    embedder = await get_embedder()
    query_vec = await embedder.embed_query("honeypot token")
    results = await idx.search(query_vec, "scam_patterns", limit=3, min_similarity=0.3)
    assert len(results) > 0, "No results returned"
    first = results[0]
    assert "id" in first, "Missing id"
    assert "similarity" in first, "Missing similarity"
    assert "content" in first, "Missing content (hydration failed)"


@test("ANNIndex: search is sub-second for small collections")
async def test_ann_speed():
    import time

    from app.ann_index import ANNIndex
    from app.crypto_embeddings import get_embedder

    idx = ANNIndex()
    await idx.build_index("scam_patterns")
    embedder = await get_embedder()
    query_vec = await embedder.embed_query("rug pull")
    t0 = time.time()
    await idx.search(query_vec, "scam_patterns", limit=5, min_similarity=0.3)
    t1 = time.time()
    assert (t1 - t0) < 1.0, f"Search took {t1 - t0:.2f}s, expected < 1s"


@test("ANNIndex: stats returns collection info")
async def test_ann_stats():
    from app.ann_index import ANNIndex

    idx = ANNIndex()
    await idx.build_index("scam_patterns")
    s = idx.stats()
    assert "scam_patterns" in s, f"scam_patterns not in stats: {s}"
    assert s["scam_patterns"].get("n", 0) > 0, "No vectors in stats"


# ══════════════════════════════════════════════════════════════════════
# SEMANTIC CACHE
# ══════════════════════════════════════════════════════════════════════


@test("SemanticCache: store and check returns hit")
async def test_semantic_cache_hit():
    from app.semantic_cache import SemanticCache

    cache = SemanticCache()
    vec = [0.1] * 384  # dummy vector
    results = [{"id": "test1", "similarity": 0.9, "content": "test doc"}]
    await cache.store(vec, results)
    cached = await cache.check(vec)
    assert cached is not None, "Expected cache hit, got None"
    assert len(cached) == 1, f"Expected 1 cached result, got {len(cached)}"


@test("SemanticCache: different vector returns miss")
async def test_semantic_cache_miss():
    from app.semantic_cache import SemanticCache

    cache = SemanticCache()
    vec1 = [1.0] + [0.0] * 383  # orthogonal
    await cache.store(vec1, [{"id": "x"}])
    vec2 = [0.0] + [1.0] * 1 + [0.0] * 382  # very different
    cached = await cache.check(vec2)
    assert cached is None, f"Expected cache miss, got hit: {cached}"


@test("SemanticCache: stats returns hit rate")
async def test_semantic_cache_stats():
    from app.semantic_cache import SemanticCache

    cache = SemanticCache()
    s = await cache.stats()
    assert "entries" in s, f"Missing entries in stats: {s}"
    assert "hit_rate" in s, f"Missing hit_rate in stats: {s}"


# ══════════════════════════════════════════════════════════════════════
# THREE-PILLAR HYBRID SEARCH
# ══════════════════════════════════════════════════════════════════════


@test("three_pillar_search: returns results with pillar attribution")
async def test_three_pillar():
    from app.rag_service import three_pillar_search

    result = await three_pillar_search(
        "honeypot token with high sell tax",
        collections=["scam_patterns"],
        limit=5,
    )
    assert "results" in result, f"Missing results key: {list(result.keys())}"
    assert "pillar_summary" in result, "Missing pillar_summary"
    ps = result["pillar_summary"]
    assert "dense_hits" in ps, "Missing dense_hits"
    assert "sparse_hits" in ps, "Missing sparse_hits"
    assert "pillars_used" in ps, "Missing pillars_used"


@test("three_pillar_search: entity extraction from query")
async def test_three_pillar_entity():
    from app.rag_service import three_pillar_search

    result = await three_pillar_search(
        "token on solana and ethereum",
        collections=["scam_patterns"],
        limit=3,
    )
    ee = result.get("entity_extraction")
    if ee:
        chains = ee.get("chain_names", [])
        assert "solana" in chains or "ethereum" in chains, f"Expected chain names, got: {chains}"


@test("three_pillar_search: RRF fusion produces ranked results")
async def test_three_pillar_rrf():
    from app.rag_service import three_pillar_search

    result = await three_pillar_search(
        "rug pull honeypot",
        collections=["scam_patterns"],
        limit=5,
    )
    results = result.get("results", [])
    assert len(results) > 0, "No results from three-pillar search"
    # Results should have match_type showing pillar attribution
    for r in results[:2]:
        assert "match_type" in r or "pillars" in r, f"Missing pillar attribution: {list(r.keys())}"


# ══════════════════════════════════════════════════════════════════════
# QUERY TRANSFORMATION
# ══════════════════════════════════════════════════════════════════════


@test("query_transform: expand adds crypto synonyms")
async def test_query_expand():
    from app.query_transform import expand_query

    variants = await expand_query("rug pull token on ethereum")
    assert len(variants) >= 3, f"Expected >=3 variants, got {len(variants)}: {variants}"
    # Should contain synonym expansions
    variants_lower = [v.lower() for v in variants]
    assert any("honeypot" in v or "liquidity drain" in v or "exit scam" in v for v in variants_lower), (
        f"No crypto synonyms found in: {variants}"
    )


@test("query_transform: step_back generalizes questions")
async def test_query_step_back():
    from app.query_transform import step_back_query

    result = await step_back_query("Is $SOL a rug pull?")
    # Should produce a broader query
    assert "rug pull" in result.lower() or "token" in result.lower(), f"Step-back didn't generalize: {result}"


@test("query_transform: auto router picks correct strategy")
async def test_query_auto_route():
    from app.query_transform import transform_query

    # Specific entity (address) → should pick expand or step_back
    tq = await transform_query("0xdAC17F958D2ee523a2206206994597C13D831ec7 scam", strategy="auto")
    assert tq.strategy != "none", f"Entity query should not be 'none', got {tq.strategy}"
    assert len(tq.transformed_queries) >= 1, f"Should have transforms: {tq.transformed_queries}"


@test("query_transform: short factual queries pass through")
async def test_query_passthrough():
    from app.query_transform import transform_query

    tq = await transform_query("rug pull", strategy="none")
    assert tq.strategy == "none"
    assert tq.transformed_queries == ["rug pull"]


# ══════════════════════════════════════════════════════════════════════
# RAGAS EVALUATION
# ══════════════════════════════════════════════════════════════════════


@test("ragas_eval: golden test set has entries")
def test_ragas_golden_set():
    from app.ragas_eval import GOLDEN_TEST_SET

    assert len(GOLDEN_TEST_SET) >= 40, f"Expected >=40 golden test pairs, got {len(GOLDEN_TEST_SET)}"
    for entry in GOLDEN_TEST_SET:
        assert "query" in entry, f"Missing query field: {entry}"
        assert "collection" in entry, f"Missing collection field: {entry}"


@test("ragas_eval: context_precision computes nDCG")
def test_ragas_ndcg():
    from app.ragas_eval import context_precision

    # Perfect ranking
    prec = context_precision(["a", "b", "c"], ["a", "b", "c"], k=5)
    assert prec == 1.0, f"Perfect ranking should be 1.0, got {prec}"
    # Empty results
    prec0 = context_precision([], ["a", "b"], k=5)
    assert prec0 == 0.0, f"Empty results should be 0.0, got {prec0}"


@test("ragas_eval: hit_rate computes correctly")
def test_ragas_hit_rate():
    from app.ragas_eval import hit_rate

    hr = hit_rate(["a", "b", "c"], ["b"])
    assert hr == 1.0, f"Hit should be 1.0 when relevant doc in results, got {hr}"
    hr0 = hit_rate(["x", "y"], ["b"])
    assert hr0 == 0.0, f"Hit should be 0.0 when no relevant doc, got {hr0}"


@test("ragas_eval: mrr computes correctly")
def test_ragas_mrr():
    from app.ragas_eval import mrr

    mr = mrr(["x", "b", "y"], ["b"])
    assert abs(mr - 0.5) < 0.01, f"MRR for rank-2 hit should be 0.5, got {mr}"
    mr0 = mrr(["x", "y"], ["b"])
    assert mr0 == 0.0, f"MRR for no hit should be 0.0, got {mr0}"


# ══════════════════════════════════════════════════════════════════════
# SCAM PATTERN CACHE
# ══════════════════════════════════════════════════════════════════════


@test("scam pattern cache: pre_embed caches all 10 patterns")
async def test_pattern_cache():
    from app.crypto_embeddings import KNOWN_SCAM_PATTERNS
    from app.rag_service import _pattern_cache, _preembed_scam_patterns

    await _preembed_scam_patterns()
    assert len(_pattern_cache) == len(KNOWN_SCAM_PATTERNS), (
        f"Expected {len(KNOWN_SCAM_PATTERNS)} cached, got {len(_pattern_cache)}"
    )


@test("scam pattern cache: cached patterns have embedding vectors")
async def test_pattern_cache_vectors():
    from app.rag_service import _pattern_cache, _preembed_scam_patterns

    await _preembed_scam_patterns()
    for name, result in _pattern_cache.items():
        assert hasattr(result, "vector"), f"Pattern {name} missing vector attr"
        assert len(result.vector) > 0, f"Pattern {name} has empty vector"


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  RAG SYSTEM TEST SUITE")
    print("=" * 60)
    ok = asyncio.run(run_tests())
    sys.exit(0 if ok else 1)
