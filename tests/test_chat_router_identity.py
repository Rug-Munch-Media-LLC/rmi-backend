"""Pure unit tests for the chat identity fast-path and history ordering.

Covers ``app/routers/chat.py``:
  - ``_identity_answer`` fires canned persona answers for identity/self
    questions and returns ``None`` for everything else.
  - ``_build_messages`` (with explicit history) always ends with the CURRENT
    user message as the LAST turn, merges consecutive same-role turns, and
    drops a dangling trailing assistant turn.

No DB, network, or LLM calls are made: the identity helper is a pure regex
function, and ``_build_messages`` only touches the DB when ``history`` is
``None`` (we always pass an explicit history here).
"""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "dev"

import pytest

from app.routers.chat import _build_messages, _identity_answer


@pytest.mark.parametrize(
    "message",
    [
        "who are you",
        "Who are you?",
        "who made you",
        "who made you?",
        "who created you",
        "who built you?",
        "who developed you",
        "who runs you",
        "who is your creator?",
        "who is behind you",
        "what are you",
        "What are you?",
        "tell me about yourself",
        "tell me about you",
        "Tell me about yourself.",
        "introduce yourself",
        "what can you do",
        "What can you do?",
    ],
)
def test_identity_answer_fires_for_identity_questions(message: str) -> None:
    answer = _identity_answer(message)
    assert answer is not None
    assert "RMI Terminal" in answer
    assert "crypto security analysis" in answer


@pytest.mark.parametrize(
    "message",
    [
        "what is bitcoin",
        "what are you doing",
        "what are you wearing",
        "what can you do about this token",
        "what can you do with my wallet address",
        "who made your day better",
        "what is the price of solana",
        "tell me about solana security",
        "analyze 0x1234567890abcdef1234567890abcdef12345678",
    ],
)
def test_identity_answer_returns_none_for_non_identity(message: str) -> None:
    assert _identity_answer(message) is None


async def test_build_messages_ends_with_current_user_message() -> None:
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "what is solana"},
        {"role": "assistant", "content": "solana is a blockchain"},
    ]
    messages = await _build_messages("tester", "who made you", None, history)
    assert messages[-1] == {"role": "user", "content": "who made you"}
    # Trailing assistant turn was dropped by _normalize_history so the model
    # answers the CURRENT question; the last history user turn precedes it.
    assert messages[-2] == {"role": "user", "content": "what is solana"}
    assert messages[0]["role"] == "system"


async def test_build_messages_merges_consecutive_same_role_turns() -> None:
    history = [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "answer"},
    ]
    messages = await _build_messages("tester", "third", None, history)
    # The two consecutive user turns were merged into one, and the trailing
    # assistant turn was dropped so the current message is the last turn.
    assert messages[-1] == {"role": "user", "content": "third"}
    merged_users = [m for m in messages if m["role"] == "user"]
    assert len(merged_users) == 2  # merged history user + current user
    assert "first" in merged_users[0]["content"]
    assert "second" in merged_users[0]["content"]


async def test_build_messages_drops_trailing_assistant_turn() -> None:
    history = [
        {"role": "user", "content": "what is a rug pull"},
        {"role": "assistant", "content": "a rug pull is when..."},
    ]
    messages = await _build_messages("tester", "and how do I avoid it?", None, history)
    # The dangling assistant turn is dropped so the model answers the CURRENT
    # question instead of continuing its prior answer.
    assert messages[-1] == {"role": "user", "content": "and how do I avoid it?"}
    assert messages[-2] == {"role": "user", "content": "what is a rug pull"}


async def test_build_messages_empty_history_ends_with_current() -> None:
    messages = await _build_messages("tester", "hello", None, [])
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert messages[0]["role"] == "system"
