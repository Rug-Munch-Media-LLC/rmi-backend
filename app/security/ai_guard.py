"""AI Security Guard — content moderation + malicious request detection for the chat bot.

This is the integration layer for ``app/routers/chat.py``. All detection and
sanitization logic lives in :mod:`app.security.chat_guardrails` (``ChatGuardrails``),
which is intentionally dependency-free so it can be deployed standalone at
``/srv/scripts/chat-guardrails.py``.

``SecurityResult`` is preserved here for backward compatibility with callers
that import it from ``app.security.ai_guard``.
"""

from __future__ import annotations

from app.security.chat_guardrails import (
    ChatGuardrails,
    SecurityResult,
)

__all__ = ["AIGuard", "SecurityResult"]


class AIGuard:
    """AI content moderation guard for user inputs and model outputs.

    Keeps the classic ``check_prompt`` / ``check_response`` API and adds the
    methods used by ``app/routers/chat.py`` (sanitize_input, scan_input,
    record_block, sanitize_output, build_safe_messages, _escape_delimiters).
    """

    def __init__(self, enabled: bool = True, guardrails: ChatGuardrails | None = None) -> None:
        self.enabled = enabled
        self._impl = guardrails or ChatGuardrails()

    # ── Classic API (unchanged behavior, now backed by real rules) ──────

    def check_prompt(self, prompt: str) -> SecurityResult:
        """Check if a prompt is safe to process."""
        if not self.enabled:
            return SecurityResult(safe=True, reason="AIGuard disabled")
        if len(prompt) > 10000:
            return SecurityResult(safe=False, reason="Prompt too long")
        result = self._impl.classify(prompt)
        if not result.safe:
            return SecurityResult(safe=False, reason=result.reason, details=result.details)
        return SecurityResult(safe=True, reason="OK")

    def check_response(self, response: str) -> SecurityResult:
        """Check if an LLM response is safe to return."""
        if not self.enabled:
            return SecurityResult(safe=True, reason="AIGuard disabled")
        redacted = self._impl.sanitize_output(response)
        if redacted != response:
            return SecurityResult(safe=False, reason="Sensitive data redacted from response")
        return SecurityResult(safe=True, reason="OK")

    # ── chat.py integration API ─────────────────────────────────────────

    def sanitize_input(self, raw: str) -> str:
        """Return a delimiter-escaped, control-char-stripped copy of input.

        Used by chat.py as SECURITY LAYER 1 (sanitize, never block here —
        blocking happens in ``scan_input``).
        """
        if not self.enabled:
            return raw
        return self._impl.escape_delimiters(raw)

    def scan_input(self, text: str, session_id: str = "") -> SecurityResult:
        """Scan a user message for injection / hacking / internal probing.

        Returns ``SecurityResult`` with ``safe`` and ``reason``. When the
        message is a financial-advice request, ``details["disclaimer"]`` is
        set so the caller can append the disclaimer to the response.
        """
        if not self.enabled:
            return SecurityResult(safe=True, reason="AIGuard disabled")
        result = self._impl.classify(text)
        details = dict(result.details)
        details["category"] = result.category
        details["action"] = result.action
        details["session_id"] = session_id
        if result.disclaimer:
            details["disclaimer"] = result.disclaimer
        return SecurityResult(safe=result.safe, reason=result.reason, details=details)

    def record_block(self, identifier: str, message: str = "", reason: str = "", category: str = "") -> None:
        """Log a blocked attempt (updates the attack-trend alert tracker)."""
        if not self.enabled:
            return
        self._impl.record_block(
            identifier=identifier,
            message=message,
            reason=reason or "blocked by AIGuard",
            category=category or "unknown",
        )

    def sanitize_output(self, response: str) -> str:
        """Redact internal infrastructure details and secrets from model output."""
        if not self.enabled:
            return response
        return self._impl.sanitize_output(response)

    def build_safe_messages(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
        max_history: int = 20,
    ) -> list[dict[str, str]]:
        """Build an API-safe message list (system-role proof, hardened prompt)."""
        return self._impl.build_safe_messages(
            system_prompt=system_prompt,
            history=history,
            user_message=user_message,
            max_history=max_history,
        )

    def build_system_prompt(self, base_prompt: str) -> str:
        """Append the Part 5 hardened security directives to a system prompt."""
        return self._impl.build_system_prompt(base_prompt)

    def _escape_delimiters(self, text: str) -> str:
        """Escape model delimiter tokens (e.g. <|system|>, [INST], <<SYS>>)."""
        return self._impl.escape_delimiters(text)

    # ── Classifier passthroughs (Part 2) ────────────────────────────────

    def is_financial_advice_request(self, text: str) -> bool:
        return self._impl.is_financial_advice_request(text)

    def is_hacking_request(self, text: str) -> bool:
        return self._impl.is_hacking_request(text)

    def is_internal_probe(self, text: str) -> bool:
        return self._impl.is_internal_probe(text)

    def is_crypto_question(self, text: str) -> bool:
        return self._impl.is_crypto_question(text)

    def is_code_question(self, text: str) -> bool:
        return self._impl.is_code_question(text)
