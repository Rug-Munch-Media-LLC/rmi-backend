"""
Hallucination Guard Module - NLI-based hallucination detection for RAG outputs.

Primary method: Cross-encoder NLI model (DeBERTa-v3) scores (context, answer) pairs.
Secondary method: LLM self-check via OpenRouter API when DeBERTa is unavailable.

Used AFTER LLM generation to verify answers are grounded in retrieved context.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NLI_MODEL_PRIMARY = "cross-encoder/nli-deberta-v3-small"
NLI_MODEL_FALLBACK = "moritz23/nli-deberta-v3-xsmall"

OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
LLM_API_KEY_ENV = "LLM_API_KEY"
# Decode base64 LLM key if present, otherwise use plain LLM_API_KEY
# (safety net: ensures key is decoded even when imported without main.py)
if os.getenv("LLM_API_KEY_B64"):
    import base64 as _b64

    os.environ["LLM_API_KEY"] = _b64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4.1-mini"

MAX_CHECK_TIMEOUT_S = 5.0

# NLI label indices (standard for cross-encoder NLI models)
LABEL_ENTAILMENT = 0
LABEL_NEUTRAL = 1
LABEL_CONTRADICTION = 2

# Confidence thresholds
CONFIDENCE_HIGH = 0.85
CONFIDENCE_MEDIUM = 0.60

class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Result of a single hallucination check."""

    is_faithful: bool
    confidence: float
    method: str
    contradiction_details: list[str] = field(default_factory=list)
    flagged_claims: list[str] = field(default_factory=list)

    @property
    def confidence_level(self) -> ConfidenceLevel:
        if self.confidence > CONFIDENCE_HIGH:
            return ConfidenceLevel.HIGH
        elif self.confidence >= CONFIDENCE_MEDIUM:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

@dataclass
class CitationVerificationResult:
    """Result of verifying inline citations against sources."""

    total_citations: int
    verified_citations: int
    unsupported_citations: list[dict[str, Any]] = field(default_factory=list)
    citation_details: list[dict[str, Any]] = field(default_factory=list)
    all_supported: bool = True

# ---------------------------------------------------------------------------
# Claim splitter - simple sentence-level extraction
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

def _split_claims(text: str) -> list[str]:
    """Split answer into individual claim sentences for granular checking."""
    sentences = _SENTENCE_RE.split(text.strip())
    return [s.strip() for s in sentences if s.strip()]

# ---------------------------------------------------------------------------
# Citation parser
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"\[(\d+)\]")

def _extract_citations(text: str) -> list[tuple[int, str]]:
    """
    Parse inline citations like [1], [2] from the answer.
    Returns list of (citation_number, sentence_containing_citation).
    """
    results: list[tuple[int, str]] = []
    sentences = _split_claims(text)
    for sentence in sentences:
        nums = _CITATION_RE.findall(sentence)
        for n in nums:
            results.append((int(n), sentence))
    return results

# ---------------------------------------------------------------------------
# HallucinationGuard
# ---------------------------------------------------------------------------

class HallucinationGuard:
    """NLI-based hallucination detector with LLM self-check fallback."""

    _instance: HallucinationGuard | None = None

    def __init__(
        self,
        nli_model_name: str = NLI_MODEL_PRIMARY,
        fallback_model_name: str = NLI_MODEL_FALLBACK,
        timeout_s: float = MAX_CHECK_TIMEOUT_S,
    ) -> None:
        self._nli_model_name = nli_model_name
        self._fallback_model_name = fallback_model_name
        self._timeout_s = timeout_s

        # Lazy-loaded model artefacts
        self._tokenizer = None
        self._model = None
        self._model_loaded = False
        self._model_load_failed = False
        self._active_model_name: str | None = None

    # -----------------------------------------------------------------------
    # Singleton
    # -----------------------------------------------------------------------

    @classmethod
    async def get_guard(cls) -> HallucinationGuard:
        """Return the singleton HallucinationGuard, creating if needed."""
        if cls._instance is None:
            cls._instance = cls()
        if not cls._instance._model_loaded and not cls._instance._model_load_failed:
            await cls._instance._load_model()
        return cls._instance

    # -----------------------------------------------------------------------
    # Model loading
    # -----------------------------------------------------------------------

    async def _load_model(self) -> None:
        """Attempt to load the primary NLI model, then the fallback."""
        for model_name in (self._nli_model_name, self._fallback_model_name):
            try:
                logger.info("Loading NLI model: %s", model_name)
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                loop = asyncio.get_event_loop()
                tokenizer, model = await loop.run_in_executor(
                    None,
                    lambda mn=model_name: (
                        AutoTokenizer.from_pretrained(mn),
                        AutoModelForSequenceClassification.from_pretrained(mn),
                    ),
                )
                model.eval()
                self._tokenizer = tokenizer
                self._model = model
                self._model_loaded = True
                self._active_model_name = model_name
                logger.info("Successfully loaded NLI model: %s", model_name)
                return
            except Exception as exc:
                logger.warning("Failed to load NLI model %s: %s", model_name, exc)
                continue

        self._model_load_failed = True
        logger.warning("All NLI models failed to load. Falling back to LLM self-check.")

    # -----------------------------------------------------------------------
    # Warm-up & health
    # -----------------------------------------------------------------------

    async def warm_up(self) -> None:
        """Pre-load the NLI model so first real check is fast."""
        if not self._model_loaded and not self._model_load_failed:
            await self._load_model()
        logger.info(
            "HallucinationGuard warm-up complete. model_loaded=%s active=%s",
            self._model_loaded,
            self._active_model_name,
        )

    def health_check(self) -> dict[str, Any]:
        """Return current model status for health endpoints."""
        return {
            "nli_model_loaded": self._model_loaded,
            "nli_model_name": self._active_model_name,
            "fallback_active": self._model_load_failed,
            "primary_model": self._nli_model_name,
            "fallback_model": self._fallback_model_name,
            "method": "nli" if self._model_loaded else "llm_self_check",
        }

    # -----------------------------------------------------------------------
    # Core NLI scoring
    # -----------------------------------------------------------------------

    async def _nli_score(self, premise: str, hypothesis: str) -> dict[str, float]:
        """
        Run NLI scoring on a (premise, hypothesis) pair.
        Returns dict with keys: entailment, neutral, contradiction.
        """
        import torch

        def _sync_score() -> dict[str, float]:
            inputs = self._tokenizer(
                premise,
                hypothesis,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            with torch.no_grad():
                logits = self._model(**inputs).logits
            probs = torch.softmax(logits, dim=-1).squeeze().tolist()
            # Ensure probs is a list even for single-entry batches
            if isinstance(probs, float):
                probs = [probs]
            return {
                "entailment": float(probs[LABEL_ENTAILMENT]),
                "neutral": float(probs[LABEL_NEUTRAL]),
                "contradiction": float(probs[LABEL_CONTRADICTION]),
            }

        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _sync_score),
            timeout=self._timeout_s,
        )
        return result

    # -----------------------------------------------------------------------
    # LLM self-check fallback
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_llm_prompt(context: str, answer: str) -> str:
        return (
            "Given ONLY the following retrieved context, determine whether the answer "
            "is faithful to the context. An answer is faithful if every claim it makes "
            "is supported by the context. If the answer contradicts the context or adds "
            "information not present in the context, it is not faithful.\n\n"
            f"Context:\n{context}\n\n"
            f"Answer:\n{answer}\n\n"
            "Is this answer faithful to the context? Answer Yes or No, then provide "
            "brief reasoning.\n\nFormat:\nFaithful: Yes/No\nReasoning: ..."
        )

    async def _llm_self_check(self, answer: str, context: str) -> CheckResult:
        """Use an LLM (DeepSeek primary, OpenRouter fallback) to check faithfulness when NLI is unavailable."""
        api_key = os.environ.get(LLM_API_KEY_ENV, "") or os.environ.get(OPENROUTER_API_KEY_ENV, "")
        if not api_key:
            logger.error("LLM_API_KEY / OPENROUTER_API_KEY not set; LLM self-check unavailable.")
            return CheckResult(
                is_faithful=False,
                confidence=0.0,
                method="llm_self_check_unavailable",
                contradiction_details=["LLM API key not configured"],
                flagged_claims=[],
            )

        # Use DeepSeek URL/model if LLM_API_KEY is set, else fall back to OpenRouter
        use_llm = bool(os.environ.get(LLM_API_KEY_ENV, ""))
        api_url = LLM_BASE_URL if use_llm else OPENROUTER_API_URL
        model = LLM_MODEL if use_llm else OPENROUTER_MODEL

        prompt = self._build_llm_prompt(context, answer)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 256,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(api_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            content = data["choices"][0]["message"]["content"].strip()
            is_faithful = content.lower().startswith("faithful: yes") or (
                "faithful: yes" in content.lower()[:80]
            )

            # Extract reasoning
            reasoning_parts = content.split("Reasoning:")
            reasoning = reasoning_parts[1].strip() if len(reasoning_parts) > 1 else content

            confidence = 0.7 if is_faithful else 0.3

            contradiction_details: list[str] = []
            flagged_claims: list[str] = []
            if not is_faithful:
                contradiction_details.append(reasoning)
                flagged_claims.append(answer[:200])

            return CheckResult(
                is_faithful=is_faithful,
                confidence=confidence,
                method="llm_self_check",
                contradiction_details=contradiction_details,
                flagged_claims=flagged_claims,
            )
        except TimeoutError:
            logger.warning("LLM self-check timed out after %ss", self._timeout_s)
            return CheckResult(
                is_faithful=False,
                confidence=0.0,
                method="llm_self_check_timeout",
                contradiction_details=["LLM self-check timed out"],
                flagged_claims=[],
            )
        except Exception as exc:
            logger.error("LLM self-check failed: %s", exc)
            return CheckResult(
                is_faithful=False,
                confidence=0.0,
                method="llm_self_check_error",
                contradiction_details=[str(exc)],
                flagged_claims=[],
            )

    # -----------------------------------------------------------------------
    # Public API - single check
    # -----------------------------------------------------------------------

    async def check_answer(self, answer: str, context: str) -> CheckResult:
        """
        Check a single (answer, context) pair for hallucinations.

        Uses NLI model if loaded, otherwise falls back to LLM self-check.
        Checks each claim sentence individually and aggregates.
        """
        if self._model_loaded:
            return await self._check_answer_nli(answer, context)
        else:
            return await self._llm_self_check(answer, context)

    async def _check_answer_nli(self, answer: str, context: str) -> CheckResult:
        """NLI-based check: score each claim sentence against the context."""
        claims = _split_claims(answer)
        if not claims:
            return CheckResult(
                is_faithful=True,
                confidence=1.0,
                method="nli",
                contradiction_details=[],
                flagged_claims=[],
            )

        flagged_claims: list[str] = []
        contradiction_details: list[str] = []
        total_entailment = 0.0
        total_neutral = 0.0
        total_contradiction = 0.0

        for claim in claims:
            try:
                scores = await asyncio.wait_for(
                    self._nli_score(context, claim),
                    timeout=self._timeout_s,
                )
            except TimeoutError:
                logger.warning("NLI scoring timed out for claim: %.80s", claim)
                flagged_claims.append(claim)
                contradiction_details.append(f"Timeout checking: {claim[:80]}")
                total_contradiction += 1.0
                continue
            except Exception as exc:
                logger.error("NLI scoring error: %s", exc)
                flagged_claims.append(claim)
                contradiction_details.append(f"Error checking: {claim[:80]}")
                total_contradiction += 1.0
                continue

            entailment = scores["entailment"]
            neutral = scores["neutral"]
            contradiction = scores["contradiction"]

            total_entailment += entailment
            total_neutral += neutral
            total_contradiction += contradiction

            if contradiction > entailment and contradiction > neutral:
                flagged_claims.append(claim)
                contradiction_details.append(
                    f"Contradiction (p={contradiction:.2f}): {claim[:120]}"
                )

        n = len(claims)
        avg_entailment = total_entailment / n
        avg_contradiction = total_contradiction / n

        # Faithful = no flagged contradictions. A claim is only flagged when
        # contradiction > entailment AND contradiction > neutral (line 411).
        # High neutral scores just mean "not enough info to confirm" - not a hallucination.
        is_faithful = len(flagged_claims) == 0
        confidence = (
            (avg_entailment + (1.0 - avg_contradiction)) / 2.0
            if is_faithful
            else 1.0 - avg_contradiction
        )

        return CheckResult(
            is_faithful=is_faithful,
            confidence=round(confidence, 4),
            method="nli",
            contradiction_details=contradiction_details,
            flagged_claims=flagged_claims,
        )

    # -----------------------------------------------------------------------
    # Public API - multi-source check
    # -----------------------------------------------------------------------

    async def check_answer_with_sources(
        self, answer: str, sources: list[dict[str, Any]]
    ) -> CheckResult:
        """
        Check an answer against each source individually and aggregate.

        Each source dict should have a 'text' or 'content' key with the
        source content. The answer is evaluated against each source; if ANY
        source entails a claim, it is considered supported.
        """
        if not sources:
            return CheckResult(
                is_faithful=False,
                confidence=0.0,
                method="nli" if self._model_loaded else "llm_self_check",
                contradiction_details=["No sources provided for verification"],
                flagged_claims=[],
            )

        claims = _split_claims(answer)
        if not claims:
            return CheckResult(
                is_faithful=True,
                confidence=1.0,
                method="nli" if self._model_loaded else "llm_self_check",
            )

        # Extract text from each source
        source_texts: list[str] = []
        for src in sources:
            text = src.get("text", src.get("content", src.get("page_content", "")))
            if text:
                source_texts.append(str(text))

        if not source_texts:
            return CheckResult(
                is_faithful=False,
                confidence=0.0,
                method="nli" if self._model_loaded else "llm_self_check",
                contradiction_details=["Sources contain no usable text"],
                flagged_claims=[],
            )

        if self._model_loaded:
            return await self._check_answer_with_sources_nli(claims, source_texts)
        else:
            # Combine sources for LLM self-check
            combined_context = "\n\n---\n\n".join(source_texts)
            return await self._llm_self_check(answer, combined_context)

    async def _check_answer_with_sources_nli(
        self, claims: list[str], source_texts: list[str]
    ) -> CheckResult:
        """NLI-based multi-source check: a claim is supported if ANY source entails it."""
        flagged_claims: list[str] = []
        contradiction_details: list[str] = []

        claim_verdicts: list[float] = []  # best entailment score per claim

        for claim in claims:
            best_entailment = 0.0
            best_contradiction = 0.0

            for source in source_texts:
                try:
                    scores = await asyncio.wait_for(
                        self._nli_score(source, claim),
                        timeout=self._timeout_s,
                    )
                except TimeoutError:
                    logger.warning("NLI timeout for claim against source: %.60s", claim[:60])
                    continue
                except Exception as exc:
                    logger.error("NLI error in multi-source check: %s", exc)
                    continue

                best_entailment = max(best_entailment, scores["entailment"])
                best_contradiction = max(best_contradiction, scores["contradiction"])

            claim_verdicts.append(best_entailment)

            if best_contradiction > best_entailment and best_contradiction > 0.5:
                flagged_claims.append(claim)
                contradiction_details.append(
                    f"Contradiction (best p={best_contradiction:.2f}): {claim[:120]}"
                )

        n = len(claims)
        avg_entailment = sum(claim_verdicts) / n if n else 0.0
        has_contradiction = len(flagged_claims) > 0
        is_faithful = not has_contradiction
        confidence = avg_entailment if is_faithful else 1.0 - (len(flagged_claims) / n)

        return CheckResult(
            is_faithful=is_faithful,
            confidence=round(confidence, 4),
            method="nli",
            contradiction_details=contradiction_details,
            flagged_claims=flagged_claims,
        )

    # -----------------------------------------------------------------------
    # Citation verification
    # -----------------------------------------------------------------------

    async def verify_citations(
        self, answer: str, sources: list[dict[str, Any]]
    ) -> CitationVerificationResult:
        """
        Parse inline citations [1], [2] from the answer and verify each cited
        source actually supports the claim it is attached to.

        Each source dict is expected to have a numeric index or be ordered
        such that sources[0] corresponds to [1], sources[1] to [2], etc.
        """
        citations = _extract_citations(answer)
        if not citations:
            return CitationVerificationResult(
                total_citations=0,
                verified_citations=0,
                unsupported_citations=[],
                citation_details=[],
                all_supported=True,
            )

        citation_details: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        verified = 0

        for cit_num, sentence in citations:
            src_idx = cit_num - 1  # [1] -> index 0
            source_text = ""

            if 0 <= src_idx < len(sources):
                src = sources[src_idx]
                source_text = src.get("text", src.get("content", src.get("page_content", "")))
                source_text = str(source_text) if source_text else ""

            detail: dict[str, Any] = {
                "citation_number": cit_num,
                "sentence": sentence,
                "source_found": bool(source_text),
            }

            if not source_text:
                detail["supported"] = False
                detail["reason"] = "Source not found or empty"
                unsupported.append(detail)
                citation_details.append(detail)
                continue

            # Verify citation supports the claim
            if self._model_loaded:
                try:
                    scores = await asyncio.wait_for(
                        self._nli_score(source_text, sentence),
                        timeout=self._timeout_s,
                    )
                except TimeoutError:
                    detail["supported"] = False
                    detail["reason"] = "NLI timeout"
                    unsupported.append(detail)
                    citation_details.append(detail)
                    continue
                except Exception as exc:
                    detail["supported"] = False
                    detail["reason"] = f"NLI error: {exc}"
                    unsupported.append(detail)
                    citation_details.append(detail)
                    continue

                entailment = scores["entailment"]
                contradiction = scores["contradiction"]
                neutral = scores["neutral"]

                if entailment >= contradiction and entailment >= neutral:
                    detail["supported"] = True
                    detail["score"] = scores
                    verified += 1
                elif contradiction > entailment:
                    detail["supported"] = False
                    detail["score"] = scores
                    detail["reason"] = f"Contradiction detected (p={contradiction:.2f})"
                    unsupported.append(detail)
                else:
                    # Neutral - the source doesn't contradict but doesn't entail either
                    detail["supported"] = False
                    detail["score"] = scores
                    detail["reason"] = "Citation does not support claim (neutral)"
                    unsupported.append(detail)
            else:
                # Fallback: LLM self-check for citation
                try:
                    llm_result = await asyncio.wait_for(
                        self._llm_self_check(sentence, source_text),
                        timeout=self._timeout_s,
                    )
                    detail["supported"] = llm_result.is_faithful
                    detail["method"] = "llm_self_check"
                    if not llm_result.is_faithful:
                        detail["reason"] = "; ".join(llm_result.contradiction_details)
                        unsupported.append(detail)
                    else:
                        verified += 1
                except TimeoutError:
                    detail["supported"] = False
                    detail["reason"] = "LLM check timed out"
                    unsupported.append(detail)

            citation_details.append(detail)

        all_supported = len(unsupported) == 0

        return CitationVerificationResult(
            total_citations=len(citations),
            verified_citations=verified,
            unsupported_citations=unsupported,
            citation_details=citation_details,
            all_supported=all_supported,
        )

    # -----------------------------------------------------------------------
    # Batch checking
    # -----------------------------------------------------------------------

    async def check_multiple(
        self, answers_and_contexts: list[tuple[str, str]]
    ) -> list[CheckResult]:
        """
        Check multiple (answer, context) pairs concurrently.

        Each call is bounded by the timeout; results are returned in order.
        """
        if not answers_and_contexts:
            return []

        tasks = [self.check_answer(answer, context) for answer, context in answers_and_contexts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: list[CheckResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Batch check %d failed: %s", i, result)
                output.append(
                    CheckResult(
                        is_faithful=False,
                        confidence=0.0,
                        method="error",
                        contradiction_details=[f"Batch check error: {result}"],
                        flagged_claims=[],
                    )
                )
            else:
                output.append(result)

        return output

    # -----------------------------------------------------------------------
    # Reset (mainly for testing)
    # -----------------------------------------------------------------------

    @classmethod
    def _reset_singleton(cls) -> None:
        """Reset the singleton instance (for testing purposes only)."""
        cls._instance = None

# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

async def get_guard() -> HallucinationGuard:
    """Async singleton accessor - the recommended entry point."""
    return await HallucinationGuard.get_guard()
