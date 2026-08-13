"""Pure unit tests for chat web-search gating.

Covers ``app/routers/chat.py::_should_web_search`` — the decision function
that replaces the old ``rag_count == 0 or _looks_like_current_events(...)``
trigger.

The regression being guarded: ``POST /api/v1/chat`` for "who made you"
returned a numbered list of Genius-lyrics web-search results. That happened
because RAG returned 0 hits and web search fired as a blanket fallback.
After the fix, web search fires ONLY for current-events queries — never for
identity/definition questions with no RAG hits.

No DB, network, or LLM calls are made — this is a pure boolean decision.
"""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "dev"

import pytest

from app.routers.chat import _should_web_search


@pytest.mark.parametrize(
    ("message", "rag_count"),
    [
        # The production regression: identity question, RAG empty → NO web search.
        ("who made you", 0),
        ("who are you", 0),
        # Definition / knowledge questions, RAG empty → NO web search.
        ("what is bitcoin", 0),
        ("what is a rug pull", 0),
        ("tell me about solana", 0),
        # RAG-empty crypto questions → NO web search.
        ("analyze this token 0x1234567890abcdef1234567890abcdef12345678", 0),
        # Even with RAG hits, non-current-events → NO web search.
        ("what is solana", 3),
        ("is ethereum secure", 5),
    ],
)
def test_should_web_search_false_for_non_current_events(message: str, rag_count: int) -> None:
    assert _should_web_search(message, rag_count) is False


@pytest.mark.parametrize(
    ("message", "rag_count"),
    [
        ("what happened with the fed today", 0),
        ("latest news on iran", 0),
        ("breaking: major exchange hack", 0),
        ("this week's election results", 0),
        # Current-events keywords fire web search even when RAG has hits.
        ("war updates today", 4),
    ],
)
def test_should_web_search_true_for_current_events(message: str, rag_count: int) -> None:
    assert _should_web_search(message, rag_count) is True
