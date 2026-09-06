"""Embedding utilities — DeepInfra primary, rugbrain HTTP fallback.

Primary embedder (2026-08-29 embed consolidation): DeepInfra
Qwen/Qwen3-Embedding-4B via POST /v1/embeddings — native 2560-dim, the SAME
model family the corpus was built with, so vectors stay index-compatible with
every rmi-qdrant-v2 collection (verified 2560-dim Cosine on all of them).

Fallback ladder (in order):
  1. DeepInfra API       (Qwen/Qwen3-Embedding-4B, 2560d) — CANONICAL primary
  2. rugbrain /api/v1/embed (RAG_API_URL — itself runs DeepInfra-primary)
  3. returns [] (caller degrades; no hash fallback)

Dimension MUST match rugbrain's EMBEDDING_DIM and the Qdrant collection.
"""

import logging
import os
from typing import Final

import httpx

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME: Final = "qwen3-embedding-4b"
EMBEDDING_DIM: Final = 2560

# DeepInfra primary lane (same model family as local :8004 ⇒ same vector space).
DEEPINFRA_EMBED_URL: Final = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
DEEPINFRA_EMBED_MODEL: Final = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")

# Secrets-file fallback for the DeepInfra lane (env var wins). Mirrors the
# pattern in rugbrain multi_embedder + rmi-codebase-mcp.
_DEV_SKIP_LOAD = object()


def _deepinfra_key() -> str:
    """Resolve the DeepInfra API key: env var first, secrets-file fallback."""
    val = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if val:
        return val
    for path in (
        os.getenv("DEEPINFRA_KEY_FILE", ""),
        "/run/secrets/deepinfra_api_key",
        "/srv/secrets/deepinfra_api_key",
    ):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return key
        except OSError:
            continue
    return ""


def _resize(vec: list[float], dim: int = EMBEDDING_DIM) -> list[float]:
    """L2-normalize a vector and enforce the collection dim (2560).

    Larger-than-target vectors are MRL-truncate-down + renormalize (mirrors
    rugbrain dim_policy); undersized vectors fail loud per T04.
    """
    if len(vec) > dim:
        cut = vec[:dim]
        norm = sum(x * x for x in cut) ** 0.5
        vec = [x / norm for x in cut] if norm else cut
    if len(vec) != dim:
        raise ValueError(f"embedding dim mismatch: got {len(vec)}, expected {dim}")
    return vec


class RAGUnavailableError(Exception):
    """RAG system is unavailable."""


async def _deepinfra_embed(text: str) -> list[float] | None:
    """Embed via DeepInfra primary lane. None on any failure (fall through)."""
    key = _deepinfra_key()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                DEEPINFRA_EMBED_URL,
                json={"model": DEEPINFRA_EMBED_MODEL, "input": [text[:32000]]},
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code != 200:
                logger.debug("deepinfra_embed_http_%s", r.status_code)
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            vec = data[0].get("embedding")
            if not vec or not isinstance(vec, list):
                return None
            return _resize(vec)
    except Exception as exc:
        logger.debug("deepinfra_embed_failed: %s", exc)
        return None


async def _rugbrain_embed(text: str) -> list[float]:
    """Fall back to rugbrain POST /api/v1/embed (itself DeepInfra-primary)."""
    api_url = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1")
    api_key = os.getenv("RMI_RAG_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{api_url}/embed",
                json={"text": text[:32000]},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return _resize(r.json().get("embedding", []))
    except Exception as exc:
        logger.debug("rugbrain_embed_failed: %s", exc)
    return []


async def get_embedding(text: str, model: str = "") -> list[float]:
    """Get an embedding vector — DeepInfra primary, rugbrain fallback.

    Returns a 2560-dim float list, or [] when every lane fails (caller
    degrades; no hash fallback per T04).
    """
    if not text or not text.strip():
        return []

    vec = await _deepinfra_embed(text)
    if vec is not None:
        return vec
    return await _rugbrain_embed(text)
