"""
RugCharts Model Registry & Quality Standards - Package
=======================================================
Smart model routing across free providers. Quality review pipeline.
Split into:
  - models:     MODELS, AI_ROLES, PROVIDER_LIMITS dicts
  - tracking:   RateLimitTracker, _can_use, _track_usage
  - providers:  _call_openrouter, _call_groq, _call_mistral, ai_call
  - quality:    FORBIDDEN_WORDS, QUALITY_REVIEW_PROMPT, review_content
  - prompts:    build_research_prompt, build_writing_prompt, get_usage_stats
"""

from __future__ import annotations
from .models import AI_ROLES, MODELS, PROVIDER_LIMITS
from .prompts import build_research_prompt, build_writing_prompt, get_usage_stats
from .providers import _call_groq, _call_mistral, _call_openrouter, ai_call
from .quality import FORBIDDEN_WORDS, QUALITY_REVIEW_PROMPT, review_content
from .tracking import RateLimitTracker, _can_use, _track_usage, rate_tracker

__all__ = [
    "AI_ROLES",
    "MODELS",
    "PROVIDER_LIMITS",
    "RateLimitTracker",
    "FORBIDDEN_WORDS",
    "QUALITY_REVIEW_PROMPT",
    "ai_call",
    "build_research_prompt",
    "build_writing_prompt",
    "get_usage_stats",
    "rate_tracker",
    "review_content",
]
