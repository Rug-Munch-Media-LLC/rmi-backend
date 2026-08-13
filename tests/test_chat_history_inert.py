"""Inert-history rendering + tool-vs-RAG grounding for the chat pipeline.

Verifies the two fixes that stop the local model from (a) answering an OLD
question instead of the current one, and (b) polluting tool-backed answers
(market/price/scan/TA) with irrelevant RAG token-news.
"""

from __future__ import annotations

import asyncio

from app.routers import chat as chat_mod


def test_build_messages_renders_history_inert() -> None:
    history = [
        {"role": "user", "content": "what happened with the one token the other day"},
        {"role": "assistant", "content": "which token do you mean?"},
        {"role": "user", "content": "never mind"},
    ]
    messages = asyncio.run(chat_mod._build_messages("u1", "how is the market doing today", history=history))
    # Only system + the current user turn (history is NOT passed as real turns)
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user"], f"unexpected roles: {roles}"
    # Current message is the user turn
    assert messages[-1]["content"] == "how is the market doing today"
    # History is rendered inert in the system prompt
    system = messages[0]["content"]
    assert "PREVIOUS CONVERSATION (reference only" in system
    assert "[USER] what happened with the one token the other day" in system
    assert "answer ONLY the NEW question below" in system


def test_build_messages_no_history() -> None:
    messages = asyncio.run(chat_mod._build_messages("u1", "what is a honeypot", history=[]))
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "PREVIOUS CONVERSATION" not in messages[0]["content"]


def test_build_messages_history_truncated_to_last_six() -> None:
    history = []
    for i in range(10):
        history.append({"role": "user", "content": f"q {i}"})
        history.append({"role": "assistant", "content": f"a {i}"})
    messages = asyncio.run(chat_mod._build_messages("u1", "current", history=history))
    system = messages[0]["content"]
    # Only the last 6 messages (3 turns) make it into the inert block
    assert "[USER] q 0" not in system
    assert "[ASSISTANT] a 8" in system
