"""Tests for the identity canned reply + gate agreement (chat_ai/_helpers.py).

Fix 1: "who made you / who created you / who built you / who are you" must
hit an INSTANT persona answer in ``_INTENT_RULES`` (registered BEFORE the
generic capability rule) instead of falling through to the slow LLM path.
Pure unit tests — no network, DB, or Telegram objects.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import (
    _INTENT_RULES,
    _is_capability_question,
    local_ai_response,
)

# The identity phrases carried by BOTH _is_capability_question (the tier-2
# gate) and the dedicated identity rule in _INTENT_RULES.
IDENTITY_PHRASES = [
    "who made you",
    "who created you",
    "who built you",
    "who developed you",
    "who runs you",
    "who is your creator",
    "who is behind you",
    "who are you",
]


class TestIdentityCannedReply:
    @pytest.mark.parametrize("phrase", IDENTITY_PHRASES)
    def test_identity_phrase_returns_persona_answer(self, phrase: str) -> None:
        reply = local_ai_response(phrase, None)
        assert reply is not None
        assert "Rug Munch Bot" in reply
        assert "Rug Munch Intelligence" in reply
        # Must be the identity answer, not the generic capabilities card.
        assert "Rug Munch Capabilities" not in reply

    def test_identity_rule_precedes_capability_rule(self) -> None:
        # "who are you" is ALSO matched by the generic capability regex, so
        # the identity rule must win by being listed first in _INTENT_RULES.
        capability_idx = next(i for i, (pat, _resp) in enumerate(_INTENT_RULES) if "capabilities" in pat.pattern)
        identity_idx = next(i for i, (pat, _resp) in enumerate(_INTENT_RULES) if "who made you" in pat.pattern)
        assert identity_idx < capability_idx

    def test_capability_question_still_returns_capabilities_card(self) -> None:
        reply = local_ai_response("what can you do?", None)
        assert reply is not None
        assert "Rug Munch Capabilities" in reply


class TestGateAgreement:
    """The tier-2 gate and the canned reply must agree: every identity phrase
    that fires the canned persona answer must also pass the gate (and vice
    versa for the identity phrases the gate recognizes)."""

    @pytest.mark.parametrize("phrase", IDENTITY_PHRASES)
    def test_gate_matches_every_identity_phrase(self, phrase: str) -> None:
        assert _is_capability_question(phrase) is True

    @pytest.mark.parametrize("phrase", IDENTITY_PHRASES)
    def test_gate_and_canned_reply_agree(self, phrase: str) -> None:
        # Gate says "capability question" → local_ai_response must return the
        # canned identity answer (never None / never the LLM path).
        assert _is_capability_question(phrase)
        reply = local_ai_response(phrase, None)
        assert reply is not None
        assert "Rug Munch Bot" in reply
