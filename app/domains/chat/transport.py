"""One HTTP call to one OpenAI-compatible chat endpoint."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.domains.chat.providers import ProviderSpec

PERMANENT_STATUSES = {400, 401, 402, 403, 404, 410, 422}
TRANSIENT_STATUSES = {408, 409, 425, 429}
LOCAL_KEY = "not-needed"


class ProviderError(Exception):
    """A single provider attempt failed. ``retryable`` drives the one retry."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def _extract_text(payload: dict) -> tuple[str, dict]:
    """Return (text, usage). Reasoning models may answer only in ``reasoning``."""
    choices = payload.get("choices") or [{}]
    message = choices[0].get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        text = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
    usage = payload.get("usage") or {}
    return text, {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


async def request_provider(
    spec: ProviderSpec, messages: list[dict], max_tokens: int
) -> tuple[str, dict]:
    """POST one completion. Raises ``ProviderError`` — never returns junk."""
    api_key = os.getenv(spec.key_env, "") if spec.key_env else ""
    if spec.key_env and not api_key:
        raise ProviderError(f"missing env {spec.key_env}", retryable=False)

    headers = {"Content-Type": "application/json"}
    if spec.key_env:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": spec.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    try:
        async with httpx.AsyncClient(timeout=spec.timeout_s) as client:
            response = await client.post(spec.url, json=body, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProviderError(f"{type(exc).__name__}: {exc}", retryable=True) from exc

    if response.status_code in PERMANENT_STATUSES:
        raise ProviderError(
            f"HTTP {response.status_code}: {response.text[:120]}",
            retryable=False,
            status=response.status_code,
        )
    if response.status_code in TRANSIENT_STATUSES or response.status_code >= 500:
        raise ProviderError(
            f"HTTP {response.status_code}: {response.text[:120]}",
            retryable=True,
            status=response.status_code,
        )
    if response.status_code != 200:
        raise ProviderError(
            f"HTTP {response.status_code}: {response.text[:120]}",
            retryable=False,
            status=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("invalid JSON from provider", retryable=True) from exc

    text, usage = _extract_text(payload)
    if not text:
        raise ProviderError("empty completion (no content, no reasoning)", retryable=False)
    return text, usage
