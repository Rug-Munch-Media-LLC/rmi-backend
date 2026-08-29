"""Embedding Quality Tiers — Qwen3 4B single-tier era (2026-08-29 final).

Self-hosted Qwen3 on the CPU server, with an OpenRouter paid backup.
Gemini is NOT part of this system (never an option).

2026-08-29 CONSOLIDATION: the 0.6B fast tier (:8007) is RETIRED. The
``deep``/``fast`` vocabulary is kept for API compatibility; every tier
now routes through the canonical 4B lane. All vectors live in the same
Qwen3-Embedding-4B space — no cross-model mismatch is possible.

Tiers
-----
deep  — Qwen3-Embedding-4B local (:8004). Corpus search queries +
        ingestion + anything feeding Qdrant collections. The ONLY tier
        allowed to touch corpus vector spaces.
        Fallback ladder: local 4B → OpenRouter ``qwen/qwen3-embedding-8b``
        (PAID backup account — heavier docs, higher detail) → OpenRouter
        ``liquid/lfm-2.5-embedding-350m:free`` (MAIN account free tier,
        native 1024-dim).

fast  — Alias of deep (2026-08-29): utility jobs (near-duplicate
        detection, clustering, cache keys, internal similarity tools)
        map here, but resolve to the same Qwen3-Embedding-4B lane as
        deep. There is no separate fast model anymore.

Gemini is NOT part of this system. Never was an option.

Dims
----
All Qdrant collections run 1024-dim. The 4B model serves MRL-truncated
1024-dim vectors (verified live). :meth:`normalize_dims` enforces the
collection dim on every vector that passes through, so any provider
output dim is handled.
"""

from __future__ import annotations

import math
from typing import Final

# ── Collection contract ────────────────────────────────────────────────
COLLECTION_DIM: Final = 1024

# ── Tier → allowed provider ids (must match rate_limiter PROVIDERS) ───
# 2026-08-29 consolidation: the 0.6B fast tier (:8007) is retired. Every
# tier resolves to the canonical 4B lane (local :8004 first, then the
# OpenRouter fallbacks) — all vectors live in the same 4B space.
TIER_PROVIDERS: dict[str, set[str]] = {
    "deep": {"qwen3_4b_local", "qwen3_8b_openrouter_paid", "qwen3_free_openrouter"},
    # fast == deep: no separate fast model anymore.
    "fast": {"qwen3_4b_local", "qwen3_8b_openrouter_paid", "qwen3_free_openrouter"},
    "default": {"qwen3_4b_local", "qwen3_8b_openrouter_paid", "qwen3_free_openrouter"},
}

TIER_MIN_DIMS: dict[str, int] = {
    "deep": 1024,
    "fast": 1024,
    "default": 1024,
}

# ── Task → tier mapping ────────────────────────────────────────────────
TASK_TIERS: dict[str, str] = {
    # deep: everything feeding or querying corpus vector spaces
    "corpus_search": "deep",
    "user_search": "deep",
    "scam_detection": "deep",
    "forensic_analysis": "deep",
    "vulnerability_mapping": "deep",
    "contract_audit": "deep",
    "news_ingestion": "deep",
    "bulk_ingestion": "deep",
    "rag_ingest": "deep",
    "deep_investigation": "deep",
    # fast: utility tasks — 2026-08-29 the fast tier is an alias of deep
    # (0.6B retired); the mapping is kept for API compatibility.
    "dedup": "fast",
    "near_duplicate": "fast",
    "clustering": "fast",
    "cache_key": "fast",
    "utility_similarity": "fast",
}


def get_tier(task: str) -> str:
    """Tier for a task name (unknown tasks → deep — fail toward quality)."""
    return TASK_TIERS.get(task, "deep")


def get_allowed_providers(task: str) -> set[str]:
    """Provider ids allowed for this task's tier."""
    return TIER_PROVIDERS.get(get_tier(task), TIER_PROVIDERS["deep"])


def get_min_dims(task: str) -> int:
    return TIER_MIN_DIMS.get(get_tier(task), 1024)


def is_corpus_task(task: str) -> bool:
    """True when the task touches a corpus vector space (deep-only)."""
    return get_tier(task) == "deep"


def assert_fast_tier_safe(task: str) -> None:
    """Raise if a fast-tier task is actually corpus-bound (mismatch guard).

    2026-08-29: the fast tier is no longer a separate model space — it
    resolves to the same 4B providers as deep — so the cross-model danger
    is gone. The guard is retained for API compatibility and policy
    clarity: corpus tasks should still go through the deep tier.
    """
    if is_corpus_task(task):
        raise ValueError(f"task '{task}' is a corpus task — route it through the deep tier, not the fast alias")


def normalize_dims(vector: list[float], target: int = COLLECTION_DIM) -> list[float]:
    """MRL-style truncate + renormalize to ``target`` dims.

    Qwen3 embedding models support Matryoshka Representation Learning:
    the first N dims of a larger embedding remain a valid (smaller) space.
    All our collections run 1024 — vectors arriving larger are truncated
    and re-normalized; smaller ones are rejected by the caller's min-dims
    filter before reaching here.
    """
    if len(vector) < target:
        raise ValueError(f"vector dim {len(vector)} < required {target}")
    cut = vector[:target]
    norm = math.sqrt(sum(x * x for x in cut))
    if norm == 0:
        return cut
    return [x / norm for x in cut]
