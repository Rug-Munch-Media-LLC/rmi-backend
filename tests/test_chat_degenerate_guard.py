"""Degenerate-output + vague-question guards for the shared chat pipeline.

Covers two fixes:
- ai_router._is_degenerate: bare bullet / empty / punctuation-only LLM output.
- chat._vague_question: underspecified questions get a clarification prompt.
"""

from __future__ import annotations

from app.ai_router import _is_degenerate
from app.routers.chat import _vague_question


def test_is_degenerate_bare_bullet() -> None:
    assert _is_degenerate("- ") is True
    assert _is_degenerate("-\n-") is True
    assert _is_degenerate("* ") is True
    assert _is_degenerate("•") is True


def test_is_degenerate_empty_and_punctuation() -> None:
    assert _is_degenerate("") is True
    assert _is_degenerate("   ") is True
    assert _is_degenerate("...") is True


def test_is_degenerate_real_content() -> None:
    assert _is_degenerate("Bitcoin is down 2% today") is False
    assert _is_degenerate("- not a real bullet") is False


def test_vague_question_triggers() -> None:
    msg = "give me an explanation of what happened with the one token the other day"
    answer = _vague_question(msg)
    assert answer is not None
    assert "$SYMBOL" in answer  # asks for a ticker/address


def test_vague_question_specific_symbol_passes() -> None:
    assert _vague_question("what happened to $PEPE yesterday") is None


def test_vague_question_specific_address_passes() -> None:
    addr = "0x" + "a" * 40
    assert _vague_question(f"what happened with {addr}") is None


def test_vague_question_identity_passes() -> None:
    assert _vague_question("who made you") is None
