"""Embed tier system — Qwen3 4B-only + OpenRouter fallback contracts
(2026-08-29 consolidation: the 0.6B fast tier is RETIRED).

Offline (no network). Verifies the FINAL configuration:

* provider registry: local-first priorities, gemini/mistral/nvidia/bge
  museum fully removed, no qwen3_06b_local anywhere, OR paid 8B on
  backup key, free LFM on main key
* tier mapping: corpus tasks → deep; utility tasks → fast (now an alias
  of deep); unknown → deep
* fast-tier guard: corpus task through fast tier raises loudly
* dim normalization: MRL truncate+renormalize to collection dim (1024),
  undersized vectors rejected
* dispatcher wiring: embed() prefers local, escalates in priority order,
  normalizes output dims (transport stubbed)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import embed_tiers as et
from app.rate_limiter import configure_providers, get_dispatcher

# ── Provider registry ─────────────────────────────────────────────────


def test_no_gemini_mistral_nvidia_bge_anywhere():
    configure_providers(has_openrouter_credits=True)
    d = get_dispatcher_sync()
    ids = {p.id for p in d._providers}
    for banned in ("gemini_1", "gemini_2", "gemini_3", "mistral", "nv_direct", "nv_openrouter", "bge_m3", "local_bge"):
        assert banned not in ids, f"stale provider {banned} still registered"


def get_dispatcher_sync():
    import asyncio

    return asyncio.get_event_loop().run_until_complete(get_dispatcher())


def test_no_fast_tier_remains():
    """Structural guard: the retired 0.6B tier must not reappear anywhere.

    A 0.6B query against a 4B-embedded collection is a cross-model space
    mismatch — Qdrant rejects it and search silently returns empty. The
    0.6B embed (:8007) lane was retired 2026-08-29.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    offenders = []
    for p in (root / "app").rglob("*.py"):
        src = p.read_text()
        if (
            "qwen3_06b_local" in src
            or "QWEN_FAST_URL" in src
            or "127.0.0.1:8007" in src
            or "qwen3-embedding-0.6b" in src
        ):
            offenders.append(str(p.relative_to(root)))
    assert not offenders, f"retired 0.6B embed tier leaked back in: {offenders}"


def test_local_first_priorities():
    configure_providers(True)
    d = get_dispatcher_sync()
    by_id = {p.id: p for p in d._providers}
    assert by_id["qwen3_4b_local"].priority > by_id["qwen3_8b_openrouter_paid"].priority
    assert by_id["qwen3_4b_local"].priority > by_id["qwen3_free_openrouter"].priority
    # locals are free-flagged and use no API key
    assert by_id["qwen3_4b_local"].free
    assert not by_id["qwen3_4b_local"].key_env
    # 2026-08-29: the 0.6B provider is gone from the registry
    assert "qwen3_06b_local" not in by_id


def test_backup_account_separation():
    configure_providers(True)
    d = get_dispatcher_sync()
    by_id = {p.id: p for p in d._providers}
    # paid 8B → BACKUP account key; free LFM → MAIN account key
    assert by_id["qwen3_8b_openrouter_paid"].key_env == "OPENROUTER_BACKUP_API_KEY"
    assert by_id["qwen3_free_openrouter"].key_env == "OPENROUTER_API_KEY"
    assert "qwen/qwen3-embedding-8b" in by_id["qwen3_8b_openrouter_paid"].model
    assert ":free" in by_id["qwen3_free_openrouter"].model


def test_all_dims_match_collection():
    configure_providers(True)
    d = get_dispatcher_sync()
    for p in d._providers:
        if p.id == "hash":
            continue
        msg = f"{p.id} below collection dim"
        assert p.dims >= et.COLLECTION_DIM, msg


# ── Tier mapping + guard ──────────────────────────────────────────────


def test_corpus_tasks_map_to_deep():
    for task in ("corpus_search", "user_search", "scam_detection", "news_ingestion", "rag_ingest"):
        assert et.get_tier(task) == "deep"
        assert et.is_corpus_task(task)


def test_utility_tasks_map_to_fast():
    for task in ("dedup", "near_duplicate", "clustering", "cache_key"):
        assert et.get_tier(task) == "fast"
        assert not et.is_corpus_task(task)


def test_unknown_task_fails_toward_deep():
    assert et.get_tier("brand_new_task") == "deep"


def test_fast_guard_blocks_corpus_tasks():
    with pytest.raises(ValueError, match="corpus"):
        et.assert_fast_tier_safe("user_search")
    # utility tasks pass
    et.assert_fast_tier_safe("dedup")


def test_fast_tier_routes_to_4b():
    """2026-08-29: the 0.6B fast lane is retired — every tier resolves to
    the canonical 4B providers, so fast == deep."""
    allowed = et.get_allowed_providers("user_search")
    assert "qwen3_06b_local" not in allowed
    fast_allowed = et.get_allowed_providers("dedup")
    assert "qwen3_06b_local" not in fast_allowed
    assert fast_allowed == allowed, "fast tier must alias the deep (4B) providers"


# ── Dim normalization ─────────────────────────────────────────────────


def test_normalize_truncates_and_renormalizes():
    v = [0.5] * 2560  # e.g. native 4B output at full dims
    out = et.normalize_dims(v)
    assert len(out) == et.COLLECTION_DIM
    norm = sum(x * x for x in out) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_normalize_passthrough_at_target():
    v = [1.0] + [0.0] * 1023
    assert et.normalize_dims(v) == v


def test_normalize_rejects_undersized():
    with pytest.raises(ValueError, match="required"):
        et.normalize_dims([0.1] * 384)


# ── Dispatcher end-to-end (transport stubbed) ─────────────────────────


@pytest.mark.asyncio
async def test_embed_prefers_local_and_normalizes(monkeypatch):
    # Stub redis-backed side channels at CLASS level (dispatcher __init__
    # touches them before any instance patch could apply).
    from app.rate_limiter import EmbeddingCache, RateTracker

    monkeypatch.setattr(EmbeddingCache, "load_from_redis", lambda self: None)
    monkeypatch.setattr(EmbeddingCache, "set", lambda self, *a, **k: None)
    monkeypatch.setattr(RateTracker, "_connect", AsyncMock())
    configure_providers(True)
    d = await get_dispatcher()
    monkeypatch.setattr(d.cache, "get", lambda t: None, raising=False)
    monkeypatch.setattr(d.tracker, "can_call", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(d.tracker, "record", AsyncMock(), raising=False)

    sent: dict = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.5] * 2048}]}

    class FakeHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json=None):
            sent["url"] = url
            return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", lambda **k: FakeHttp())
    vecs, provider = await d.embed(["hello world"], task="user_search")
    assert vecs, "no vectors returned"
    assert len(vecs[0]) == et.COLLECTION_DIM
    assert "4B" in provider  # dispatcher returns provider NAME, not id
    assert ":8004" in sent["url"] or "8004" in sent["url"]
