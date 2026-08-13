"""
Cross-Encoder Reranker Module

Local cross-encoder reranker using BAAI/bge-reranker-v2-m3 for CPU inference.
Part of a 3-stage RAG pipeline: vector search (stage 1) -> cross-encoder (stage 2) -> LLM reranking (stage 3).

Uses sentence-transformers CrossEncoder for batched scoring and combines
cross-encoder relevance with original vector similarity.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sentence_transformers import CrossEncoder

from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_INFERENCE_TIMEOUT_SECS = 10
_CROSS_ENCODER_WEIGHT = 0.6
_VECTOR_SIM_WEIGHT = 0.4

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _min_max_normalize(scores: list[float]) -> list[float]:
    """Min-max normalize a list of floats to [0, 1].

    If all scores are identical (range == 0), return 0.5 for every element
    so that the combined formula still degrades gracefully.
    """
    if not scores:
        return []
    min_s = min(scores)
    max_s = max(scores)
    rng = max_s - min_s
    if rng == 0:
        return [0.5] * len(scores)
    return [(s - min_s) / rng for s in scores]


# ---------------------------------------------------------------------------
# CrossEncoderReranker - singleton
# ---------------------------------------------------------------------------


class CrossEncoderReranker:
    """Cross-encoder reranker powered by BAAI/bge-reranker-v2-m3.

    Lazy-loads the model on first use (or via ``warm_up()``).
    Use ``get_reranker()`` to obtain the singleton instance.
    """

    _instance: CrossEncoderReranker | None = None
    _lock = asyncio.Lock()

    def __init__(self) -> None:
        # Do NOT call _load_model here - lazy-load on first use.
        self._model: CrossEncoder | None = None
        self._model_loaded: bool = False
        self._model_type: str = "none"  # "onnx", "fp32", or "none"
        self._load_time_secs: float | None = None
        self._memory_estimate_mb: float | None = None
        self._tokenizer: Any = None  # for ONNX path
        self._onnx_session: Any = None  # for ONNX path

    # -----------------------------------------------------------------------
    # Singleton accessor
    # -----------------------------------------------------------------------

    @classmethod
    async def get_reranker(cls) -> CrossEncoderReranker:
        """Return the singleton ``CrossEncoderReranker``, creating it if needed."""
        if cls._instance is None:
            async with cls._lock:
                # Double-check after acquiring lock
                if cls._instance is None:
                    logger.info("Creating CrossEncoderReranker singleton")
                    cls._instance = cls()
        return cls._instance

    # -----------------------------------------------------------------------
    # Model loading
    # -----------------------------------------------------------------------

    def _load_model(self) -> None:
        """Synchronously load the cross-encoder model into memory.

        Prefers ONNX INT8 quantized model if available (~500MB, 2-3x faster).
        Falls back to fp32 CrossEncoder from sentence-transformers.
        """
        if self._model_loaded:
            return

        # Check for quantized ONNX model first
        import os as _os

        _onnx_path = "/app/data/models/bge-reranker-v2-m3-onnx"
        _onnx_model = _os.path.join(_onnx_path, "model.onnx")

        if _os.path.exists(_onnx_model):
            logger.info("Loading quantized ONNX reranker from %s ...", _onnx_path)
            start = time.perf_counter()
            try:
                import onnxruntime as ort
                from optimum.onnxruntime import ORTModelForSequenceClassification  # noqa: F401 — availability probe
                from transformers import AutoTokenizer

                # Use CPU execution provider
                self._tokenizer = AutoTokenizer.from_pretrained(_onnx_path)
                self._onnx_session = ort.InferenceSession(
                    _onnx_model,
                    providers=["CPUExecutionProvider"],
                )
                self._model_type = "onnx"
            except ImportError:
                logger.warning("ONNX model found but optimum not installed. Falling back to fp32.")
                self._load_fp32_model()
                return

            elapsed = time.perf_counter() - start
            self._load_time_secs = round(elapsed, 2)
            self._model_loaded = True
            self._memory_estimate_mb = 500.0  # approximate for INT8 quantized

            logger.info(
                "ONNX reranker loaded in %.2fs (~500 MB INT8)",
                elapsed,
            )
            return

        # Fallback: load full fp32 model
        self._load_fp32_model()

    def _load_fp32_model(self) -> None:
        """Load the full fp32 cross-encoder (2.1 GB, ~45s)."""
        logger.info("Loading cross-encoder model '%s' (CPU, fp32) ...", _MODEL_NAME)
        start = time.perf_counter()
        # TODO: pin to specific revision SHA for supply-chain integrity
        self._model = CrossEncoder(_MODEL_NAME, device="cpu")
        elapsed = time.perf_counter() - start
        self._load_time_secs = round(elapsed, 2)
        self._model_loaded = True
        self._model_type = "fp32"

        # Rough memory estimate: parameter count * 4 bytes (fp32)
        try:
            inner_model = self._model.model
            total_params = sum(p.numel() for p in (inner_model.parameters() if inner_model is not None else []))
            self._memory_estimate_mb = round(total_params * 4 / (1024 * 1024), 1)
        except (ConnectionError, OSError, TimeoutError):
            self._memory_estimate_mb = 420.0

        logger.info(
            "Cross-encoder model loaded in %.2fs (~%.0f MB parameters in fp32)",
            elapsed,
            self._memory_estimate_mb or 0,
        )

    # -----------------------------------------------------------------------
    # Public API - warm_up / health_check
    # -----------------------------------------------------------------------

    async def warm_up(self) -> None:
        """Pre-load the model so first inference has no cold-start penalty.

        Call this at application startup.
        """
        if self._model_loaded:
            logger.debug("warm_up() called but model already loaded - skipping")
            return
        # Model loading is CPU-bound; run in executor to avoid blocking the
        # async event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_model)

    def health_check(self) -> dict[str, Any]:
        """Return model status, load time, and memory estimate."""
        return {
            "model_name": _MODEL_NAME,
            "loaded": self._model_loaded,
            "load_time_secs": self._load_time_secs,
            "memory_estimate_mb": self._memory_estimate_mb,
            "device": "cpu",
            "inference_timeout_secs": _INFERENCE_TIMEOUT_SECS,
            "weights": {
                "cross_encoder": _CROSS_ENCODER_WEIGHT,
                "vector_similarity": _VECTOR_SIM_WEIGHT,
            },
        }

    # -----------------------------------------------------------------------
    # Core inference (internal)
    # -----------------------------------------------------------------------

    def _score_pairs(self, query: str, texts: list[str]) -> list[float]:
        """Score (query, text) pairs via the cross-encoder in one forward pass.

        Uses ONNX Runtime if quantized model is loaded, else sentence-transformers.
        Returns raw cross-encoder scores (one per text).
        """
        if self._model_type == "onnx" and self._onnx_session:
            scores = []
            for text in texts:
                inputs = self._tokenizer(
                    query,
                    text,
                    return_tensors="np",
                    truncation=True,
                    max_length=512,
                    padding=True,
                )
                ort_inputs = {
                    "input_ids": inputs["input_ids"],
                    "attention_mask": inputs["attention_mask"],
                }
                # Some ONNX models also expect token_type_ids
                if "token_type_ids" in inputs:
                    ort_inputs["token_type_ids"] = inputs["token_type_ids"]

                outputs = self._onnx_session.run(None, ort_inputs)
                logits = outputs[0]
                # Cross-encoder output: logit for positive class
                score = float(logits[0][0]) if logits.ndim > 1 else float(logits[0])
                scores.append(score)
            return scores

        # Fallback: sentence-transformers
        model = self._model
        if model is None:
            return []
        pairs = [(query, t) for t in texts]
        raw_scores = model.predict(pairs, batch_size=len(pairs))  # type: ignore[arg-type]  # stubs reject list[tuple[str, str]]
        return [float(v) for v in raw_scores.tolist()]

    # -----------------------------------------------------------------------
    # Public API - rerank
    # -----------------------------------------------------------------------

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Re-rank *documents* against *query* combining cross-encoder + vector similarity.

        Parameters
        ----------
        query:
            The user query string.
        documents:
            List of document dicts, each containing at least ``"content"``
            and ``"similarity"`` keys.
        top_k:
            Number of top results to return.

        Returns
        -------
        list[Dict]
            Documents sorted by combined score (descending), each augmented
            with ``"cross_score"``, ``"cross_score_normalized"``, and
            ``"combined_score"`` keys.  A ``"reasoning"`` key describes why
            the document was ranked where it is.
        """
        if not documents:
            return []

        top_k = min(top_k, len(documents))

        # Ensure model is loaded
        if not self._model_loaded:
            await self.warm_up()

        texts = [doc.get("content", "") for doc in documents]
        vector_sims = [float(doc.get("similarity", 0.0)) for doc in documents]

        # Attempt cross-encoder inference with timeout guard
        cross_scores: list[float] | None = None
        try:
            loop = asyncio.get_running_loop()
            cross_scores = await asyncio.wait_for(
                loop.run_in_executor(None, self._score_pairs, query, texts),
                timeout=_INFERENCE_TIMEOUT_SECS,
            )
            logger.debug(
                "Cross-encoder scored %d documents for query %.80s",
                len(documents),
                query,
            )
        except TimeoutError:
            logger.warning(
                "Cross-encoder inference timed out after %ds - falling back to vector-only ranking for query: %.80s",
                _INFERENCE_TIMEOUT_SECS,
                query,
            )
        except (ConnectionError, OSError):
            logger.exception(
                "Cross-encoder inference failed - falling back to vector-only ranking for query: %.80s",
                query,
            )

        # Build result list
        results: list[dict[str, Any]] = []
        if cross_scores is not None:
            # Normalize cross-encoder scores to [0, 1]
            cross_normed = _min_max_normalize(cross_scores)

            for i, doc in enumerate(documents):
                ce_norm = cross_normed[i]
                vsim = vector_sims[i]
                combined = _CROSS_ENCODER_WEIGHT * ce_norm + _VECTOR_SIM_WEIGHT * vsim

                # Build a reasoning string
                if ce_norm > 0.8 and vsim > 0.8:
                    reason = "Strong cross-encoder and vector agreement"
                elif ce_norm > 0.5 and vsim > 0.5:
                    reason = "Moderate agreement from both signals"
                elif ce_norm > vsim + 0.3:
                    reason = "Cross-encoder significantly boosted relevance over vector similarity"
                elif vsim > ce_norm + 0.3:
                    reason = "Vector similarity significantly higher than cross-encoder score"
                else:
                    reason = "Mixed signals with comparable strength"

                enriched = dict(doc)  # shallow copy
                enriched["cross_score"] = round(cross_scores[i], 4)
                enriched["cross_score_normalized"] = round(ce_norm, 4)
                enriched["combined_score"] = round(combined, 4)
                enriched["reasoning"] = reason
                results.append(enriched)
        else:
            # Fallback: vector-similarity-only ranking
            for i, doc in enumerate(documents):
                enriched = dict(doc)
                enriched["cross_score"] = None
                enriched["cross_score_normalized"] = None
                enriched["combined_score"] = round(vector_sims[i], 4)
                enriched["reasoning"] = "Vector-only ranking (cross-encoder unavailable)"
                results.append(enriched)

        # Sort descending by combined_score
        results.sort(key=lambda d: d["combined_score"], reverse=True)
        return results[:top_k]

    # -----------------------------------------------------------------------
    # Public API - rerank_only (simpler)
    # -----------------------------------------------------------------------

    async def rerank_only(
        self,
        query: str,
        texts: list[str],
        top_k: int = 10,
    ) -> list[str]:
        """Score and sort *texts* by cross-encoder relevance to *query*.

        Simplified API that returns just the sorted text strings.

        Parameters
        ----------
        query:
            The user query string.
        texts:
            List of text passages to rank.
        top_k:
            Number of top texts to return.

        Returns
        -------
        list[str]
            Texts sorted by cross-encoder score (descending), truncated to
            *top_k*.
        """
        if not texts:
            return []

        top_k = min(top_k, len(texts))

        if not self._model_loaded:
            await self.warm_up()

        try:
            loop = asyncio.get_running_loop()
            cross_scores = await asyncio.wait_for(
                loop.run_in_executor(None, self._score_pairs, query, texts),
                timeout=_INFERENCE_TIMEOUT_SECS,
            )
        except TimeoutError:
            logger.warning(
                "rerank_only: cross-encoder timed out after %ds - returning texts in original order",
                _INFERENCE_TIMEOUT_SECS,
            )
            return texts[:top_k]
        except (ConnectionError, OSError):
            logger.exception(
                "rerank_only: cross-encoder failed - returning texts in original order",
            )
            return texts[:top_k]

        # Pair texts with scores, sort descending
        paired = list(zip(texts, cross_scores, strict=False))
        paired.sort(key=lambda t: t[1], reverse=True)
        return [t for t, _ in paired[:top_k]]


# ---------------------------------------------------------------------------
# Module-level convenience (optional thin wrappers)
# ---------------------------------------------------------------------------


async def get_reranker() -> CrossEncoderReranker:
    """Async convenience function to obtain the singleton reranker."""
    return await CrossEncoderReranker.get_reranker()
