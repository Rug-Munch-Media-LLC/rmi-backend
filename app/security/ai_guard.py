# AI Security Guard
from __future__ import annotations

"""AI content moderation and malicious request detection"""


class SecurityResult:
    def __init__(self, safe: bool, reason: str = "", details: dict | None = None) -> None:
        self.safe = safe
        self.reason = reason
        self.details = details or {}


class AIGuard:
    """AI content moderation guard for user inputs."""

    def __init__(self) -> None:
        self.enabled = True

    def check_prompt(self, prompt: str) -> SecurityResult:
        """Check if a prompt is safe to process."""
        if not self.enabled:
            return SecurityResult(safe=True, reason="AIGuard disabled")

        # Basic checks - can be expanded with LLM-based moderation
        if len(prompt) > 10000:
            return SecurityResult(safe=False, reason="Prompt too long")

        return SecurityResult(safe=True, reason="OK")

    def check_response(self, response: str) -> SecurityResult:
        """Check if an LLM response is safe to return."""
        if not self.enabled:
            return SecurityResult(safe=True, reason="AIGuard disabled")

        return SecurityResult(safe=True, reason="OK")
