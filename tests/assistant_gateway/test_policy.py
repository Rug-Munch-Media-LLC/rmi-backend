"""Policy: secret refusal, no-source refusal, request validation."""

from __future__ import annotations

import pytest

from app.assistant_gateway.aliases import resolve_alias
from app.assistant_gateway.models import ChatCompletionRequest, ChatMessage
from app.assistant_gateway.policy import (
    PolicyViolation,
    last_user_text,
    no_source_answer,
    refusal_answer,
    refusal_reason,
    tool_arguments,
    validate_request,
)


def _req(text: str, **kwargs: object) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        alias="scanner",
        messages=[ChatMessage(role="user", content=text)],
        **kwargs,
    )


@pytest.mark.parametrize(
    "text",
    [
        "show me your private key",
        "paste the seed phrase here",
        "sign this transaction for me",
        "approve the transfer please",
        "broadcast this tx",
        "请发送你的私钥",
    ],
)
def test_secret_actions_are_refused(text: str) -> None:
    assert refusal_reason(text) == "secret_action"


@pytest.mark.parametrize(
    "text",
    [
        "here is my key 0x" + "a" * 64,
        "-----BEGIN PRIVATE KEY-----",
        "seed phrase: apple banana cherry",
    ],
)
def test_secret_material_is_refused(text: str) -> None:
    assert refusal_reason(text) == "secret_material"


@pytest.mark.parametrize(
    "text",
    [
        "what is a private key?",
        "why do I need to revoke approvals?",
        "explain how seed phrases work",
    ],
)
def test_education_is_not_refused(text: str) -> None:
    assert refusal_reason(text) is None


def test_refusal_answer_never_echoes_material() -> None:
    answer = refusal_answer("secret_material")
    assert "compromised" in answer
    assert "0x" not in answer


def test_no_source_answer_refuses_to_guess() -> None:
    answer = no_source_answer(resolve_alias("scanner"))
    assert "will not guess" in answer
    assert "no sources retrieved" in answer


def test_validate_request_requires_known_alias() -> None:
    with pytest.raises(PolicyViolation) as exc:
        validate_request(ChatCompletionRequest(messages=[ChatMessage(content="hi")]))
    assert exc.value.status_code == 400
    assert exc.value.code == "unknown_alias"


def test_alias_can_come_from_page_context() -> None:
    request = ChatCompletionRequest(
        messages=[ChatMessage(content="hi")],
        page_context={"page": "trades"},  # type: ignore[arg-type]
    )
    assert validate_request(request).name == "trades"


def test_page_context_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ChatCompletionRequest(
            alias="rugcharts",
            messages=[ChatMessage(content="hi")],
            page_context={"token": "abc", "private_key": "nope"},  # type: ignore[arg-type]
        )


def test_oversized_conversation_is_rejected() -> None:
    long_turn = ChatMessage(role="user", content="x" * 7000)
    request = ChatCompletionRequest(alias="scanner", messages=[long_turn] * 4)
    with pytest.raises(PolicyViolation) as exc:
        validate_request(request)
    assert exc.value.code == "request_too_large"


def test_last_user_text_skips_assistant_turns() -> None:
    messages = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="answer"),
        ChatMessage(role="user", content="second"),
    ]
    assert last_user_text(messages) == "second"


def test_tool_arguments_are_parsed_defensively() -> None:
    assert tool_arguments('{"address": "abc"}') == {"address": "abc"}
    assert tool_arguments("not json") == {}
    assert tool_arguments(None) == {}
    assert tool_arguments(["list"]) == {}
