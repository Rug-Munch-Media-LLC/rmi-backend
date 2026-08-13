"""Purity/decomposition tests for the extracted chat helpers.

Covers the chat.py → app/routers/chat/ package split (2026-08-14):
- The pure helper groups (prompts, fast-path detectors, message-building)
  are importable from their submodules (``app.routers.chat._prompts`` /
  ``app.routers.chat._messages``).
- They are re-exported from ``app.routers.chat`` so every existing importer
  and test keeps working against the SAME objects.

No network, no DB, no LLM: everything here is a pure function of its inputs.
"""

from __future__ import annotations

import asyncio

import app.routers.chat as chat
from app.routers.chat import (
    SYSTEM_PROMPT,
    _build_messages,
    _identity_answer,
    _looks_like_current_events,
    _messages as msg_mod,
    _normalize_history,
    _prompts as prompts_mod,
    _sanitize_history,
    _should_web_search,
    _vague_question,
)

# ── Re-export identity: submodule object === chat.py object ──────────────


def test_re_exported_functions_are_the_same_objects() -> None:
    """chat.py re-exports the submodule objects (same object, no wrappers)."""
    assert chat._identity_answer is prompts_mod._identity_answer
    assert chat._vague_question is prompts_mod._vague_question
    assert chat._should_web_search is prompts_mod._should_web_search
    assert chat._looks_like_current_events is prompts_mod._looks_like_current_events
    assert chat.SYSTEM_PROMPT is prompts_mod.SYSTEM_PROMPT
    assert chat._sanitize_history is msg_mod._sanitize_history
    assert chat._normalize_history is msg_mod._normalize_history
    assert chat._build_messages is msg_mod._build_messages
    # The shared guard instance is one object too (routes + message builder).
    assert chat.ai_guard is msg_mod.ai_guard


def test_shared_token_hint_regexes_are_shared() -> None:
    """The token-hint regexes live in _prompts but stay visible on chat.py."""
    assert chat._EVM_ADDRESS_RE is prompts_mod._EVM_ADDRESS_RE
    assert chat._SOLANA_ADDRESS_RE is prompts_mod._SOLANA_ADDRESS_RE
    assert chat._SYMBOL_RE is prompts_mod._SYMBOL_RE


# ── Identity fast-path (pure) ────────────────────────────────────────────


def test_identity_answer_matches_who_are_you() -> None:
    answer = _identity_answer("who are you?")
    assert answer is not None
    assert "RMI Terminal" in answer


def test_identity_answer_ignores_unrelated_questions() -> None:
    assert _identity_answer("what is solana") is None
    assert _identity_answer("what are you doing") is None  # negative lookahead
    # "who are you protecting" still hits the fast-path (regex ends at a word
    # boundary after "you") — that is the pre-split behavior, unchanged.
    assert _identity_answer("who are you protecting") is not None


def test_identity_answer_constant_in_submodule() -> None:
    assert _identity_answer("who made you") == prompts_mod._IDENTITY_ANSWER


# ── Vague-question guard (pure) ──────────────────────────────────────────


def test_vague_question_returns_clarification() -> None:
    answer = _vague_question("what happened with the one token the other day")
    assert answer is not None
    assert "$SYMBOL" in answer


def test_vague_question_passes_identity() -> None:
    assert _vague_question("who created you") is None


def test_vague_question_passes_specific_referents() -> None:
    assert _vague_question("what happened to $PEPE yesterday") is None
    addr = "0x" + "b" * 40
    assert _vague_question(f"analyze {addr}") is None
    sol_addr = "So1anA" + "x" * 26  # base58-ish 32 chars
    assert _vague_question(f"check {sol_addr}") is None


# ── Web-search detector (pure) ───────────────────────────────────────────


def test_should_web_search_current_events() -> None:
    assert _should_web_search("any latest news on the fed today?", 0) is True
    assert _should_web_search("breaking: iran election", 3) is True
    assert _looks_like_current_events("what is the price today") is True


def test_should_web_search_definitional_questions() -> None:
    # RAG miss on identity/definition must NOT trigger web search.
    assert _should_web_search("what is solana", 0) is False
    assert _should_web_search("explain MEV", 0) is False
    assert _looks_like_current_events("explain MEV") is False


def test_should_web_search_ignores_rag_count() -> None:
    # rag_count is accepted for signature stability only — never influences it.
    assert _should_web_search("today", 0) is True
    assert _should_web_search("today", 42) is True


# ── History normalization / sanitization (pure) ──────────────────────────


def test_sanitize_history_whitelists_roles_and_truncates() -> None:
    long_content = "x" * 5000
    out = _sanitize_history([{"role": "system", "content": "drop me"}, {"role": "user", "content": long_content}])
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert len(out[0]["content"]) == 4000


def test_normalize_history_merges_same_role_and_drops_trailing_assistant() -> None:
    merged = _normalize_history(
        [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": "answer"},
            {"role": "assistant", "content": "trailing"},
        ]
    )
    # Consecutive same-role turns merge; the dangling trailing assistant turn
    # is dropped (it would sit unanswered before the current user message).
    assert [t["role"] for t in merged] == ["user"]
    assert merged[0]["content"] == "hi\n\nagain"


def test_normalize_history_drops_trailing_assistant_turn() -> None:
    merged = _normalize_history([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
    assert [t["role"] for t in merged] == ["user"]


# ── _build_messages with EXPLICIT history (no DB access) ─────────────────


def test_build_messages_with_explicit_history_no_db() -> None:
    """Explicit history path never touches the DB — safe in a pure test."""
    messages = asyncio.run(
        _build_messages(
            user_id="pure-test-user",
            message="what is solana?",
            system_context="RAG Context:\nsome grounding",
            history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        )
    )
    # The hardened builder always appends the CURRENT message as the last turn.
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "what is solana?"
    system_prompt = next(m["content"] for m in messages if m["role"] == "system")
    assert "RMI Terminal" in system_prompt
    assert "PREVIOUS CONVERSATION" in system_prompt  # inert history block
    assert "some grounding" in system_prompt  # system_context injected


def test_build_messages_system_prompt_has_date() -> None:
    messages = asyncio.run(
        _build_messages(
            user_id="pure-test-user",
            message="hi",
            history=[{"role": "user", "content": "hi"}],
        )
    )
    system_prompt = next(m["content"] for m in messages if m["role"] == "system")
    assert "Current date:" in system_prompt


def test_system_prompt_and_disclaimer_constants() -> None:
    assert "RMI Terminal" in SYSTEM_PROMPT
    assert "{date}" in SYSTEM_PROMPT
    assert "not financial advice" in chat.DISCLAIMER
