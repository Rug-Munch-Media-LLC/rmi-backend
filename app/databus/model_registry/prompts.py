"""
Model Registry - Prompt Builders & Usage Stats
===============================================
Helpers for building research/writing prompts and getting usage stats.
"""

from __future__ import annotations
from typing import Any

import json

from .tracking import rate_tracker


def build_research_prompt(topic: str, data: dict | None = None) -> str:
    """Build a research prompt with all available context."""
    parts = [f"Research task: {topic}\n"]

    if data:
        for key, value in data.items():
            if isinstance(value, str):
                parts.append(f"## {key.upper()}\n{value[:2000]}")
            elif isinstance(value, list):
                parts.append(f"## {key.upper()}\n" + "\n".join(f"- {str(v)[:200]}" for v in value[:10]))
            elif isinstance(value, dict):
                parts.append(f"## {key.upper()}\n{json.dumps(value, default=str)[:1000]}")

    return "\n\n".join(parts)


def build_writing_prompt(topic: str, research_notes: str, style: str = "newsletter") -> str:
    """Build a writing prompt from research notes."""
    return f"""Write a {style} about: {topic}

RESEARCH NOTES:
{research_notes[:3000]}

Style guide:
- Direct, human voice. No corporate speak. No AI-slop.
- Lead with the most interesting detail.
- Use specific numbers, names, facts.
- Vary sentence length. Short paragraphs.
- End with a clear takeaway.

Write the complete piece now:"""


def get_usage_stats() -> dict:
    """Get current model usage statistics from rate tracker."""
    return rate_tracker.stats()
