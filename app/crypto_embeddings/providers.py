"""Embedding providers: Qwen3-Embedding-4B (primary), OpenRouter, HuggingFace."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.crypto_embeddings.features import extract_contract_features
from app.crypto_embeddings.models import (
    OR_FALLBACK_EMBED,
    OR_PRIMARY_EMBED,
)

if TYPE_CHECKING:
    import numpy as np

logger = get_logger(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")


class LocalBGEEmbedder:
    """Legacy small-model embedder — HARD-DISABLED.

    Superseded by Qwen3-Embedding-4B (2560-dim; DeepInfra primary + local
    :8004 backup). The in-process sentence-transformers/torch path (~300MB
    resident in the bot process, never unloaded) was the direct cause of
    memory pressure. This class now fails fast and can NEVER load a local
    model or touch torch. Use the remote embedders (Qwen3 primary,
    OpenRouter, HuggingFace) or the hash fallback instead.
    """

    # Legacy model id retained only as a historical marker; never loaded.
    # Superseded by Qwen3-Embedding-4B (2560-dim).
    MODEL_NAME = "legacy/bge-small-en-v1.5-superseded-by-qwen3-embedding-4b"

    @classmethod
    def _get_model(cls) -> Any:
        raise RuntimeError(
            "LocalBGEEmbedder disabled: in-process sentence-transformers/torch model "
            "loading removed for memory safety. Use remote embedders "
            "(Qwen3/OpenRouter/HuggingFace) or the hash fallback instead."
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._get_model()  # always raises — no local model is ever loaded
        raise RuntimeError("LocalBGEEmbedder disabled (see _get_model)")

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


# Back-compat alias: embedder.py imports LocalEmbedder (hard-disabled local
# path, always None). Renamed to LocalBGEEmbedder for clarity; keep the old
# name so `from app.crypto_embeddings.embedder import CryptoEmbedder` and any
# importer of LocalEmbedder keeps resolving.
LocalEmbedder = LocalBGEEmbedder


class Qwen3Embedder:
    """Qwen3-Embedding-4B — DeepInfra PRIMARY, local llama.cpp fallback.

    2026-08-29 embed consolidation: DeepInfra hosts the exact same
    Qwen3-Embedding-4B the local :8004 serves — native 2560-dim, same vector
    space, always warm, no local CPU contention. This class tries DeepInfra
    first, then the local llama.cpp lane; both emit the SAME 2560-dim space so
    a fallback never breaks index compatibility.

    DIMS was fixed from a stale 1024 (the retired 0.6B SPEED-tier dim) to the
    canonical 4B native 2560 — every rmi-qdrant-v2 collection is 2560-dim.
    """

    EMBEDDER_URL = os.getenv("EMBEDDER_URL", "http://127.0.0.1:8004/v1")
    MODEL = "qwen3-embedding-4b"
    DIMS = int(os.getenv("RAG_EMBEDDING_DIM", "2560"))
    # DeepInfra primary lane (same model family as local :8004).
    DEEPINFRA_EMBED_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
    DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")

    def __init__(self, qwen3_url: str | None = None) -> None:
        self.url = qwen3_url or os.getenv("EMBEDDER_URL", self.EMBEDDER_URL)

    @staticmethod
    def _deepinfra_key() -> str:
        """DeepInfra API key: env var first, secrets-file fallback."""
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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        # ── DeepInfra PRIMARY (2026-08-29) ──────────────────────────
        key = self._deepinfra_key()
        if key:
            try:
                out: list[list[float]] = []
                async with httpx.AsyncClient(timeout=120) as client:
                    for s in range(0, len(texts), 32):
                        batch = texts[s : s + 32]
                        r = await client.post(
                            self.DEEPINFRA_EMBED_URL,
                            json={"model": self.DEEPINFRA_EMBED_MODEL, "input": [t[:8000] for t in batch]},
                            headers={"Authorization": f"Bearer {key}"},
                        )
                        if r.status_code != 200:
                            raise httpx.HTTPError(f"deepinfra_http_{r.status_code}")
                        data = sorted(r.json().get("data", []), key=lambda i: i.get("index", 0))
                        for vec in (d.get("embedding", []) for d in data):
                            if len(vec) == self.DIMS:
                                out.append(vec)
                            elif len(vec) > self.DIMS:
                                cut = vec[: self.DIMS]
                                norm = sum(x * x for x in cut) ** 0.5
                                out.append([x / norm for x in cut] if norm else cut)
                            else:
                                raise httpx.HTTPError(f"deepinfra dim mismatch: got {len(vec)}, want {self.DIMS}")
                return out
            except Exception as exc:
                logger.warning("deepinfra_embed_failed (%s) — falling back to local :8004", exc)

        # ── Local llama.cpp fallback lane ────────────────────────────
        results = []
        async with httpx.AsyncClient(timeout=120) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{self.url}/embeddings",
                        json={"model": self.MODEL, "input": [text[:8000]]},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        if data:
                            vec = data[0].get("embedding", [])
                            if len(vec) == self.DIMS:
                                results.append(vec)
                                continue
                except httpx.HTTPError:
                    pass
                results.append([0.0] * self.DIMS)
        return results

    async def embed_one(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]


class OpenRouterEmbedder:
    """OpenRouter embedding API - NVIDIA Nemotron primary, qwen3 fallback."""

    BASE = "https://openrouter.ai/api/v1/embeddings"
    MODEL = OR_PRIMARY_EMBED
    FALLBACK = OR_FALLBACK_EMBED

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or OPENROUTER_KEY
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        model_id = model or self.MODEL

        async def _try_embed(m: str) -> list[list[float]]:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {"model": m, "input": texts}
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.post(self.BASE, headers=headers, json=payload)
                if resp.status_code == 429:
                    raise RuntimeError("RATE_LIMITED")
                resp.raise_for_status()
                data = resp.json()
                return [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]

        try:
            return await _try_embed(model_id)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and model_id != self.FALLBACK:
                logger.warning(f"NVIDIA embed rate-limited, falling back to {self.FALLBACK}")
                return await _try_embed(self.FALLBACK)
            raise

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class HuggingFaceEmbedder:
    """HuggingFace Inference API - free tier fallback."""

    BASE = "https://api-inference.huggingface.co/models"
    MODEL = "qwen3-embedding-4b"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or HF_TOKEN
        if not self.token:
            raise ValueError("HUGGINGFACE_TOKEN required")

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import asyncio as _asyncio

        import httpx

        model_id = model or self.MODEL
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true",
        }
        url = f"{self.BASE}/{model_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            results: list[list[float]] = []
            for text in texts:
                resp = await client.post(url, headers=headers, json={"inputs": text})
                if resp.status_code == 503:
                    await _asyncio.sleep(3)
                    resp = await client.post(url, headers=headers, json={"inputs": text})
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], list):
                        results.append(data[0] if len(data) == 1 else data[0])
                    else:
                        results.append(data)
                else:
                    logger.warning(f"HF returned unexpected shape: {type(data)}")
                    results.append([0.0] * 1024)
            return results

    async def embed_one(self, text: str, model: str | None = None) -> list[float]:
        results = await self.embed([text], model)
        return results[0]


class ContractCodeEmbedder:
    """Specialized contract code embedder using structural features + semantic embedding."""

    @staticmethod
    async def embed(code: str, semantic_embedder=None) -> np.ndarray:
        import numpy as np

        structural = extract_contract_features(code)
        if semantic_embedder and len(code) > 50:
            code_snippet = code[:4000]
            try:
                semantic_vec = await semantic_embedder.embed_one(f"Smart contract code: {code_snippet[:3000]}")
                semantic = np.array(semantic_vec[:192], dtype=np.float32)
                if len(semantic) < 192:
                    semantic = np.pad(semantic, (0, 192 - len(semantic)))
            except Exception as e:
                logger.warning(f"Semantic contract embedding failed: {e}")
                semantic = np.zeros(192, dtype=np.float32)
        else:
            semantic = np.zeros(192, dtype=np.float32)
        return np.concatenate([structural, semantic])
