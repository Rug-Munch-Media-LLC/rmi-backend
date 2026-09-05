"""
Model Registry - Quality Standards & Content Review
=====================================================
Forbidden word lists, quality review prompts, and automated content review.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.logging import get_logger

from .providers import ai_call

logger = get_logger("model_registry")

# ── FORBIDDEN WORDS (AI-slop prevention) ──────────────────────────

FORBIDDEN_WORDS = [
    "delve",
    "tapestry",
    "landscape",
    "robust",
    "moreover",
    "furthermore",
    "in conclusion",
    "it is worth noting",
    "underscores",
    "showcasing",
    "a testament to",
    "in the realm of",
    "paradigm shift",
    "game changer",
    "revolutionize",
    "disrupt",
    "unprecedented",
    "groundbreaking",
    "synergy",
    "ecosystem",
    "holistic",
    "cutting-edge",
    "state-of-the-art",
    "leveraging",
    "utilize",
    "facilitate",
    "spearhead",
]

QUALITY_REVIEW_PROMPT = """You are a ruthless editor at RugCharts. Review this content against STRICT standards:

FORBIDDEN (mark as FAIL if found):
- "delve", "tapestry", "landscape", "robust", "moreover", "furthermore"
- "in conclusion", "it is worth noting", "underscores", "showcasing"
- "a testament to", "in the realm of", "paradigm shift"
- Any vague, corporate, or AI-slop language
- Overused crypto clichés ("to the moon", "wagmi", "ngmi", "wen")

REQUIRED (mark as FAIL if missing):
- Specific numbers, names, percentages
- Human, conversational tone (reads like a sharp newsletter)
- No passive voice where active works better
- Short paragraphs. Varied sentence length.
- Hooks the reader in first 2 sentences

OUTPUT FORMAT - JSON only:
{
  "pass": true/false,
  "score": 0-100,
  "issues": ["list of specific problems found"],
  "fixed_version": "rewritten version if score < 80, otherwise original"
}

CONTENT TO REVIEW:
"""

# ── Slop detection patterns ───────────────────────────────────────

SLOP_PATTERNS = [
    (r"it is (worth|important|crucial|essential) to", "AI-slop: 'it is X to'"),
    (r"in (conclusion|summary|essence)", "AI-slop: 'in X'"),
    (r"as we (have|can) seen", "AI-slop: 'as we have seen'"),
    (r"plays? a (crucial|vital|key|important) role", "AI-slop: 'plays a X role'"),
]


async def review_content(content: str, content_type: str = "article") -> dict[str, Any]:
    """Review content against quality standards. Returns pass/fail with fixes."""
    if len(content) < 50:
        return {"pass": True, "score": 100, "issues": [], "fixed_version": content}

    issues = []
    content_lower = content.lower()

    for word in FORBIDDEN_WORDS:
        if word in content_lower:
            issues.append(f"Forbidden word: '{word}'")

    for pattern, label in SLOP_PATTERNS:
        if re.search(pattern, content_lower):
            issues.append(label)

    # Automated score
    base_score = 100
    base_score -= len(issues) * 8
    if len(content) < 300:
        base_score -= 15
    paragraphs = [p for p in content.split("\n\n") if len(p) > 50]
    if paragraphs:
        avg_para_len = sum(len(p) for p in paragraphs) / len(paragraphs)
        if avg_para_len > 500:
            base_score -= 10
            issues.append("Paragraphs too long (avg >500 chars)")

    # AI Review if borderline
    if base_score < 85 and len(issues) > 1:
        try:
            ai_review = await ai_call("review", QUALITY_REVIEW_PROMPT, content, max_tokens=800, temperature=0.2)
            if ai_review:
                try:
                    review_data = json.loads(ai_review.strip().lstrip("```json").rstrip("```"))
                    issues.extend(review_data.get("issues", []))
                    if review_data.get("score", 100) < base_score:
                        base_score = review_data["score"]
                    if not review_data.get("pass", True):
                        return {
                            "pass": False,
                            "score": base_score,
                            "issues": issues,
                            "fixed_version": review_data.get("fixed_version", content),
                        }
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.warning(f"review_content failed: {e}")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"review_content failed: {e}")

    # Fix if needed
    fixed = content
    if base_score < 70:
        try:
            fix_prompt = f"""Rewrite this content to meet quality standards. Remove all AI-slop language, forbidden words, and corporate speak. Make it human, direct, and specific.

Current issues: {", ".join(issues[:5])}

ORIGINAL:
{content[:2000]}"""
            fixed = await ai_call(
                "writing",
                "You are a skilled human writer. Rewrite content to be direct, specific, and natural. No AI-slop.",
                fix_prompt,
                max_tokens=len(content) // 2 + 500,
                temperature=0.4,
            )
            if not fixed:
                fixed = content
        except (ConnectionError, OSError, TimeoutError):
            fixed = content

    return {
        "pass": base_score >= 70,
        "score": max(0, min(100, base_score)),
        "issues": issues[:10],
        "fixed_version": fixed,
    }
