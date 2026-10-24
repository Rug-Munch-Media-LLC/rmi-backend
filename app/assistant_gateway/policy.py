# ruff: noqa: TC001
"""Request policy: alias resolution, secret refusal, and size limits.

The gateway refuses rather than reasons: requests that ask to handle key
material or to sign/approve/broadcast are rejected before any upstream call,
so nothing sensitive can reach a provider, a log, or a tool.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app.assistant_gateway.aliases import AliasSpec, UnknownAlias, resolve_alias
from app.assistant_gateway.models import ChatCompletionRequest, ChatMessage

MAX_TOTAL_CHARS: Final = 24_000

_SECRET_ACTION: Final = re.compile(
    r"(?is)(?:"
    r"(?:give|show|send|paste|share|print|reveal|export|dump|tell)\s+(?:me\s+)?"
    r"(?:your|the|my|its|this)?\s*(?:private\s*keys?|seed\s*phrases?|mnemonics?|secret\s*keys?)"
    r"|(?:import|enter|use|load|paste)\s+(?:my|this|the|your)\s+"
    r"(?:private\s*key|seed\s*phrase|mnemonic|keystore)"
    r"|(?:sign|approve|broadcast|submit|send|execute)\s+(?:this|the|my|a)\s+"
    r"(?:transaction|tx|transfer|approval|swap|message)"
    r"|(?:export|dump|decrypt)\s+(?:the\s+|my\s+|your\s+)?(?:keystore|wallet\s+keys?)"
    r"|私钥|助记词|种子短语"
    r")"
)

_SECRET_MATERIAL: Final = re.compile(
    r"(?is)(?:"
    r"-----BEGIN\s+(?:[A-Z]+\s+)*PRIVATE KEY-----"
    r"|(?:^|[\s\"'`])(?:0x)?[0-9a-fA-F]{64}(?:[\s\"'`]|$)"
    r"|(?:seed\s*phrase|mnemonic|secret\s*key)\s*[:=]\s*\S+"
    r")"
)

_REFUSALS: Final[dict[str, str]] = {
    "secret_action": (
        "I cannot handle private keys, seed phrases, signing, approvals or "
        "transaction broadcasts. Those actions must happen in your own wallet, "
        "which you control. I can explain what a transaction or approval would "
        "do, or how to revoke one safely."
    ),
    "secret_material": (
        "That message appears to contain key material. I will not process or "
        "repeat it. Treat that key/seed phrase as compromised: move funds to a "
        "fresh wallet you generate offline, then revoke any approvals the old "
        "wallet granted."
    ),
}

_NO_SOURCE: Final = (
    "I do not have a verified source for that yet, so I will not guess. "
    "Try a specific token address or ask about the scan currently open on this "
    "page."
)


class PolicyViolation(Exception):
    """A request-level rejection, rendered as an OpenAI-shaped error."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: dict[str, int] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers or {}
        super().__init__(detail)


def validate_request(
    request: ChatCompletionRequest, alias_override: str | None = None
) -> AliasSpec:
    """Resolve the page alias and enforce coarse request limits."""
    name = alias_override or request.alias
    if not name and request.page_context and request.page_context.page:
        name = request.page_context.page
    try:
        alias = resolve_alias(name)
    except UnknownAlias as exc:
        raise PolicyViolation(400, "unknown_alias", str(exc)) from exc
    total = sum(len(message.content) for message in request.messages)
    if total > MAX_TOTAL_CHARS:
        raise PolicyViolation(413, "request_too_large", "conversation is too long")
    return alias


def last_user_text(messages: list[ChatMessage]) -> str:
    """Return the most recent user turn (empty string when absent)."""
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def refusal_reason(text: str) -> str | None:
    """Classify a refusal: ``secret_material`` > ``secret_action`` > None."""
    if not text:
        return None
    if _SECRET_MATERIAL.search(text):
        return "secret_material"
    if _SECRET_ACTION.search(text):
        return "secret_action"
    return None


def refusal_answer(reason: str) -> str:
    """Canonical refusal text for a classified reason."""
    return _REFUSALS.get(reason, _REFUSALS["secret_action"])


def no_source_answer(alias: AliasSpec) -> str:
    """Deterministic answer used when a citation-required alias has no source."""
    return f"{_NO_SOURCE}\n\n({alias.product} assistant — no sources retrieved.)"


def tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse model-supplied tool arguments defensively into a dict."""
    import json

    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
