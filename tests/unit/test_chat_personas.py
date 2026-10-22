"""Brand chat personas: registry shape, selection routing, prompt budget.

No network and no ``app.config``: ``personas`` is stdlib-only, and the
dispatcher parity check AST-parses ``chat_tools/dispatcher.py`` instead of
importing it (that import instantiates ``Settings`` and needs real secrets).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domains.chat.personas import (
    DATA_TOOLS,
    DEFAULT_PERSONA,
    PERSONAS,
    TOOL_KEYS,
    build_messages,
    get_persona,
    persona_for_surface,
    public_personas,
    select_persona,
)

PROMPT_BUDGET_TOKENS = 4096
REQUIRED_KEYS = {
    "name",
    "system_prompt",
    "temperature",
    "max_words",
    "tools_allowed",
    "refusal_text",
}
DISPATCHER = Path(__file__).resolve().parents[2] / "app" / "domains" / "chat_tools" / "dispatcher.py"


def estimate_tokens(text: str) -> int:
    """cl100k_base count; chars/4 fallback keeps this test runnable offline."""
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return len(text) // 4


def _dispatcher_intents() -> set[str]:
    """Executor keys from the dispatcher module, read without importing it."""
    tree = ast.parse(DISPATCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == "_EXECUTORS" and isinstance(node.value, ast.Dict):
            return {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    raise AssertionError("_EXECUTORS not found in chat_tools/dispatcher.py")


@pytest.mark.parametrize("key", sorted(PERSONAS))
def test_persona_has_required_fields_and_guardrails(key: str) -> None:
    persona = PERSONAS[key]
    assert set(persona) >= REQUIRED_KEYS, f"{key} is missing {REQUIRED_KEYS - set(persona)}"
    assert persona["max_words"] == 180
    assert 0.0 <= persona["temperature"] <= 0.4
    prompt = persona["system_prompt"]
    assert "EVIDENCE RULES" in prompt
    assert "180 words" in prompt
    assert "[E1]" in prompt
    assert "[web: domain]" in prompt
    assert persona["refusal_text"]


@pytest.mark.parametrize("key", sorted(PERSONAS))
def test_prompt_fits_4k_budget(key: str) -> None:
    tokens = estimate_tokens(PERSONAS[key]["system_prompt"])
    print(f"{key}: {tokens} tokens")  # visible with pytest -s
    assert tokens < PROMPT_BUDGET_TOKENS


@pytest.mark.parametrize("key", sorted(PERSONAS))
def test_tools_allowed_are_real(key: str) -> None:
    allowed = set(PERSONAS[key]["tools_allowed"])
    assert allowed <= TOOL_KEYS, f"{key} allows unknown tools {allowed - TOOL_KEYS}"


def test_data_tools_match_dispatcher() -> None:
    assert set(DATA_TOOLS) == _dispatcher_intents()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("is $PEPE a honeypot or just a rug?", "security"),
        ("someone drained my wallet through an approval, revoke?", "security"),
        ("latest crypto news today", "news"),
        ("what happened with the ETF decision", "news"),
        ("what is the price and liquidity of $WIF", "desk"),
        ("rsi and resistance on eth", "desk"),
        ("hello", DEFAULT_PERSONA),
    ],
)
def test_selection_routes(message: str, expected: str) -> None:
    assert select_persona(message, surface="telegram") == expected


def test_tie_prefers_security_over_desk() -> None:
    # "scam" (security) + "price" (desk) score 1 each: safety wins the tie.
    assert select_persona("price looks fake, is this a scam?") == "security"


def test_explicit_command_beats_score() -> None:
    assert select_persona("/desk is this a scam?") == "desk"
    assert select_persona("/news@rugmunchbot price action today") == "news"


def test_unknown_surface_and_persona_raise() -> None:
    with pytest.raises(ValueError, match="unknown chat surface"):
        persona_for_surface("carrier-pigeon")
    with pytest.raises(ValueError, match="unknown persona"):
        get_persona("marketing")


def test_build_messages_numbers_evidence_and_stamps_date() -> None:
    messages = build_messages("desk", "price?", evidence=("price 0.00042", "liq 12k"), today="2026-10-23")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"].endswith("Current date: 2026-10-23.")
    assert "[E1] price 0.00042" in messages[1]["content"]
    assert "[E2] liq 12k" in messages[1]["content"]
    assert messages[1]["content"].endswith("QUESTION\nprice?")


def test_build_messages_without_evidence_says_so() -> None:
    messages = build_messages("news", "did X happen?")
    assert "(no evidence passages were retrieved)" in messages[1]["content"]


def test_public_personas_leaks_no_prompt_text() -> None:
    rows = public_personas()
    assert {row["key"] for row in rows} == set(PERSONAS)
    assert all(set(row) == {"key", "name", "tagline"} for row in rows)
    assert "EVIDENCE RULES" not in str(rows)
