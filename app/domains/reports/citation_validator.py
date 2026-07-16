"""T05 - RAG Citation Validator.

Per RMIV5 §T05 (G05 FIX). After an LLM generates a report section from
retrieved RAG chunks, every claim in the output must cite a source by
number [1], [2], etc. This module enforces that.

Pipeline:
  1. LLM produces text constrained to retrieved chunks (in generator.py)
  2. This validator parses every [N] citation in the text
  3. Verifies that:
     a. N is a valid index into the retrieved chunks list
     b. The cited sentence/paragraph is supported by source N
        (substring match on key terms)
  4. Returns a citation report:
     {
       "validated_text": str with unciteable claims replaced by
                        "[Data not available]" or removed,
       "citations": [{"claim": str, "source_idx": int, "source_text": str}],
       "unciteable_count": int,
       "validation_rate": float  # 0.0-1.0
     }

Why this exists:
  Reports that hallucinate destroy trust. Every claim in a $5 report
  must be backed by a source we can show. If we can't find support,
  we say so - explicitly - rather than fabricating.
"""

from __future__ import annotations

import re
from typing import Any

# ── Regexes ──────────────────────────────────────────────────────────
# Match inline citations like "...some claim [1]..." or "[2, 3]" or "[1-3]"
_CITATION_RE = re.compile(r"\[(\d+(?:\s*[,\-]\s*\d+)*)\]")
# Match sentences (rough - splits on .!? followed by whitespace + uppercase)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\d])")
# Key term extraction (rough): words with 4+ chars, lowercase, no stopwords
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "day",
        "get",
        "has",
        "him",
        "his",
        "how",
        "its",
        "may",
        "new",
        "now",
        "old",
        "see",
        "two",
        "way",
        "who",
        "boy",
        "did",
        "use",
        "what",
        "when",
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "will",
        "they",
        "their",
        "which",
        "would",
        "there",
        "could",
        "about",
        "other",
        "into",
        "than",
        "more",
        "some",
        "very",
        "most",
        "only",
        "over",
        "such",
        "also",
        "after",
        "before",
        "should",
        "because",
        "where",
        "these",
        "those",
        "being",
        "through",
    }
)


def _key_terms(text: str) -> set[str]:
    """Extract key terms (lowercase, 4+ chars, not stopwords) from text."""
    words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _extract_citation_indices(citation_str: str, max_index: int) -> list[int]:
    """Parse '1', '1,2,3', or '1-3' into [1, 2, 3] (1-indexed).

    Out-of-range indices are silently dropped (we'll flag them as
    invalid in the citation report).
    """
    result = []
    for part in citation_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                lo_i, hi_i = int(lo.strip()), int(hi.strip())
                for n in range(lo_i, hi_i + 1):
                    if 1 <= n <= max_index:
                        result.append(n)
            except ValueError:
                continue
        else:
            try:
                n = int(part)
                if 1 <= n <= max_index:
                    result.append(n)
            except ValueError:
                continue
    return result


def _split_sentences_with_citations(text: str) -> list[tuple[str, list[int]]]:
    """Split text into sentences, each annotated with its [N] citations.

    A citation belongs to the sentence that contains it (not the
    preceding one). Returns [(sentence, [citation_indices])].
    """
    sentences: list[tuple[str, list[int]]] = []
    for sent in _SENTENCE_SPLIT_RE.split(text.strip()):
        sent = sent.strip()
        if not sent:
            continue
        # Find all citations in this sentence
        matches = _CITATION_RE.findall(sent)
        indices: list[int] = []
        for m in matches:
            indices.extend(_extract_citation_indices(m, max_index=10_000))
        sentences.append((sent, sorted(set(indices))))
    return sentences


def _claim_supported_by_source(claim: str, source_text: str, threshold: float = 0.4) -> bool:
    """Check if the claim's key terms appear in the source text.

    Uses Jaccard-like overlap on key terms (4+ chars, non-stopwords).
    A claim is "supported" if at least `threshold` of its key terms
    appear in the source. Threshold 0.4 = 40% overlap required.

    This is a heuristic - it catches obvious fabrications (where the
    LLM cites a source but the claim isn't in it) without being so
    strict that paraphrased but accurate claims get flagged.
    """
    claim_terms = _key_terms(claim)
    if not claim_terms:
        # No key terms (very short sentence) - assume supported
        return True
    source_terms = _key_terms(source_text)
    if not source_terms:
        return False
    overlap = len(claim_terms & source_terms)
    return (overlap / len(claim_terms)) >= threshold


def validate_section(
    text: str,
    sources: list[str],
    *,
    min_support_overlap: float = 0.4,
    on_unciteable: str = "strip",
) -> dict[str, Any]:
    """Validate that every claim in `text` cites a real source.

    Args:
        text: The LLM-generated section text.
        sources: List of source texts the LLM was supposed to use.
                 Index 0 in this list = citation [1], etc.
        min_support_overlap: Minimum fraction of claim key terms that
                            must appear in source for claim to be
                            considered supported (default 0.4 = 40%).
        on_unciteable: What to do with unsupported claims.
                       "strip" (default) - replace with [Data not available]
                       "keep"  - leave as-is, flag in citations report
                       "drop"  - remove the sentence entirely

    Returns:
        {
            "validated_text": str,
            "citations": [{"claim": str, "source_idx": int,
                          "source_text": str, "supported": bool}],
            "unciteable_count": int,
            "validation_rate": float,  # supported/total
        }
    """
    if not sources:
        # No sources provided - every claim is unciteable
        return {
            "validated_text": (
                "[Data not available - no RAG sources retrieved]"
                if on_unciteable == "strip"
                else text
            ),
            "citations": [],
            "unciteable_count": _count_sentences(text),
            "validation_rate": 0.0,
        }

    sentences = _split_sentences_with_citations(text)
    validated_sentences: list[str] = []
    citations: list[dict[str, Any]] = []
    unciteable_count = 0

    for sent, indices in sentences:
        if not indices:
            # No citations at all - unciteable
            unciteable_count += 1
            citations.append(
                {"claim": sent, "source_idx": 0, "source_text": "", "supported": False}
            )
            if on_unciteable == "strip":
                validated_sentences.append("[Data not available]")
            elif on_unciteable == "keep":
                validated_sentences.append(sent)
            # "drop" - add nothing
            continue

        # Filter out out-of-range indices (defensive - extract_citation_indices
        # already does this, but defense-in-depth for malformed input)
        valid_indices = [i for i in indices if 1 <= i <= len(sources)]
        if not valid_indices:
            # All citations were out of range - unciteable
            unciteable_count += 1
            citations.append(
                {
                    "claim": sent,
                    "source_idx": 0,
                    "source_text": "",
                    "supported": False,
                }
            )
            if on_unciteable == "strip":
                validated_sentences.append("[Data not available]")
            elif on_unciteable == "keep":
                validated_sentences.append(sent)
            continue

        # We have at least one valid citation - check each one
        best_source_idx = valid_indices[0]
        source_text = sources[best_source_idx - 1]  # [1] = sources[0]
        supported = _claim_supported_by_source(sent, source_text, threshold=min_support_overlap)

        # If first citation isn't supported, try the others
        if not supported and len(valid_indices) > 1:
            for idx in valid_indices[1:]:
                candidate = sources[idx - 1]
                if _claim_supported_by_source(sent, candidate, threshold=min_support_overlap):
                    best_source_idx = idx
                    source_text = candidate
                    supported = True
                    break

        citations.append(
            {
                "claim": sent,
                "source_idx": best_source_idx,
                "source_text": source_text[:200] + ("..." if len(source_text) > 200 else ""),
                "supported": supported,
            }
        )

        if not supported:
            unciteable_count += 1
            if on_unciteable == "strip":
                validated_sentences.append("[Data not available]")
            elif on_unciteable == "keep":
                validated_sentences.append(sent)
            # "drop" - add nothing
        else:
            validated_sentences.append(sent)

    validated_text = " ".join(validated_sentences).strip()
    total = len(citations)
    validation_rate = (total - unciteable_count) / total if total > 0 else 0.0

    return {
        "validated_text": validated_text,
        "citations": citations,
        "unciteable_count": unciteable_count,
        "validation_rate": validation_rate,
    }


def _count_sentences(text: str) -> int:
    """Rough sentence count for empty-sources case."""
    return max(1, len([s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]))
