"""Tests for the self-improvement / developer-question canned answer.

The generic capability rule over-matched "capabilities" and dumped the /help
command menu for ANY capability mention — e.g. "/chat tell me some
capabilities you could use that if i as your developer added would help you
to perform your job better for the community" got the menu instead of an
answer. A dedicated self-improvement detector + rule (registered BEFORE the
capability rule) routes those questions to a specific wishlist answer that
lists the capabilities the bot already has + what would genuinely improve it.

Pure unit tests — no network, DB, or Telegram objects.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import (
    _INTENT_RULES,
    _SELF_IMPROVEMENT_ANSWER,
    _is_capability_question,
    _is_self_improvement_question,
    local_ai_response,
)

# The original bug report: capability mention → canned menu instead of an answer.
BUG_MESSAGE = (
    "tell me some capabilities you could use that if i as your developer "
    "added would help you to perform your job better for the community"
)

SELF_IMPROVEMENT_PHRASES = [
    BUG_MESSAGE,
    "what would help you improve",
    "what capabilities could you use to do your job better",
    "what would make you better at your job",
    "what do you need from me as your developer",
    "how could you improve",
    "what could i add to make you more useful",
]

# Plain capability/help questions that must STILL get the command menu.
MENU_PHRASES = [
    "what can you do",
    "help",
    "what are your features",
    "show me the commands",
]


class TestSelfImprovementDetector:
    @pytest.mark.parametrize("phrase", SELF_IMPROVEMENT_PHRASES)
    def test_detector_fires_for_self_improvement(self, phrase: str) -> None:
        assert _is_self_improvement_question(phrase) is True

    @pytest.mark.parametrize("phrase", MENU_PHRASES)
    def test_detector_does_not_fire_for_menu_questions(self, phrase: str) -> None:
        assert _is_self_improvement_question(phrase) is False

    def test_gate_still_routes_self_improvement_to_canned_path(self) -> None:
        # The tier-2 gate (chat_ai/__init__.py) consults _is_capability_question
        # to decide whether to hit the canned path at all — self-improvement
        # questions must still pass it so they get the canned answer.
        assert _is_capability_question(BUG_MESSAGE) is True


class TestSelfImprovementAnswer:
    @pytest.mark.parametrize("phrase", SELF_IMPROVEMENT_PHRASES)
    def test_returns_specific_answer_not_menu(self, phrase: str) -> None:
        reply = local_ai_response(phrase, None)
        assert reply is not None
        # The bug: capability mention → "Rug Munch Capabilities" menu.
        assert "Rug Munch Capabilities" not in reply
        assert "What would genuinely help me" in reply

    def test_answer_contains_capability_and_improvement_content(self) -> None:
        low = _SELF_IMPROVEMENT_ANSWER.lower()
        # Capabilities the bot already HAS...
        assert "wallet forensics" in low
        assert "security scans" in low
        # ...plus the concrete improvement wishlist (not just the menu).
        assert "real-time price" in low
        assert "on-chain history" in low
        assert "labeled wallet data" in low
        assert "webhooks" in low

    @pytest.mark.parametrize("phrase", MENU_PHRASES)
    def test_menu_questions_still_get_the_menu(self, phrase: str) -> None:
        # Regression: plain capability/help questions keep the command menu.
        reply = local_ai_response(phrase, None)
        assert reply is not None
        assert "Rug Munch Capabilities" in reply


class TestRuleOrder:
    def test_self_improvement_rule_precedes_capability_rule(self) -> None:
        # The capability rule is identified by its menu response; the
        # self-improvement rule must be registered first so its answer wins
        # whenever both patterns match.
        self_idx = next(i for i, (_pat, resp) in enumerate(_INTENT_RULES) if "What would genuinely help me" in resp)
        cap_idx = next(i for i, (_pat, resp) in enumerate(_INTENT_RULES) if "Rug Munch Capabilities" in resp)
        assert self_idx < cap_idx
