"""Embedding Quality Tiers — Qwen3 dual-tier era (2026-08 final).

Two self-hosted Qwen3 tiers on the CPU server, with an OpenRouter paid
backup. Gemini is NOT part of this system (never an option).

Tiers
-----
deep  — Qwen3-Embedding-4B local (:8004). Corpus search queries +
        ingestion + anything feeding Qdrant collections. The ONLY tier
        allowed to touch corpus vector spaces.
        Fallback ladder: local 4B → OpenRouter ``qwen/qwen3-embedding-8b``
        (PAID backup account — heavier docs, higher detail) → OpenRouter
        ``liquid/lfm-2.5-embedding-350m:free`` (MAIN account free tier,
        native 1024-dim).

fast  — Qwen3-Embedding-0.6B local (:8007). Utility jobs where BOTH
        sides of the comparison run on 0.6B: near-duplicate detection,
        clustering, cache keys, internal similarity tools. NEVER for
        corpus search — a 0.6B query against a 4B-embedded collection is
        a cross-model mismatch (same dims, different space) and returns
        garbage.

Gemini is NOT part of this system. Never was an option.

Dims
----
All Qdrant collections run 1024-dim. The 4B model serves MRL-truncated
1024-dim vectors (verified live); the 0.6B model is natively 1024.
:meth:`normalize_dims` enforces the collection dim on every vector that
passes through, so any provider output dim is handled.
"""

from __future__ import annotations

import math
from typing import Final

# ── Collection contract ────────────────────────────────────────────────
COLLECTION_DIM: Final = 1024

# ── Tier → allowed provider ids (must match rate_limiter PROVIDERS) ───
TIER_PROVIDERS: dict[str, set[str]] = {
    # deep: local 4B -> OR paid 8B (backup acct) -> OR free 1024 (main acct)
    "deep": {"qwen3_4b_local", "qwen3_8b_openrouter_paid", "qwen3_free_openrouter"},
    # fast: local 0.6B -> local 4B (same-space escalation)
    "fast": {"qwen3_06b_local", "qwen3_4b_local"},
    "default": {"qwen3_4b_local", "qwen3_06b_local", "qwen3_8b_openrouter_paid", "qwen3_free_openrouter"},
}

TIER_MIN_DIMS: dict[str, int] = {
    "deep": 1024,
    "fast": 512,
    "default": 512,
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
    # fast: utility tasks where both sides of the comparison are 0.6B
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
    return TIER_MIN_DIMS.get(get_tier(task), 512)


def is_corpus_task(task: str) -> bool:
    """True when the task touches a corpus vector space (deep-only)."""
    return get_tier(task) == "deep"


def assert_fast_tier_safe(task: str) -> None:
    """Raise if a fast-tier task is actually corpus-bound (mismatch guard).

    Cross-model search (0.6B query vs 4B corpus) silently returns garbage:
    same dimensionality, completely different geometry. This guard makes
    that failure loud at the call site instead of quiet in retrieval.
    """
    if is_corpus_task(task):
        raise ValueError(
            f"task '{task}' is a corpus task — the fast (0.6B) tier must never serve corpus search or ingestion"
        )


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
