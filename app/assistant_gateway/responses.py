# ruff: noqa: TC001
"""OpenAI-compatible response and SSE shaping."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.assistant_gateway.aliases import AliasSpec
from app.assistant_gateway.models import Citation


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def completion_body(
    alias: AliasSpec,
    answer: str,
    usage: dict[str, int] | None = None,
    citations: list[Citation] | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """A ``chat.completion`` object, plus the non-standard ``citations`` array."""
    body: dict[str, Any] = {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": alias.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage or {},
    }
    if citations:
        body["citations"] = [citation.model_dump() for citation in citations]
    return body


def models_body() -> dict[str, Any]:
    """``GET /v1/models`` — aliases are the only selectable models."""
    from app.assistant_gateway.aliases import ALIASES

    return {
        "object": "list",
        "data": [
            {"id": spec.name, "object": "model", "owned_by": "rugmunch"}
            for spec in ALIASES.values()
        ],
    }


def error_body(code: str, message: str) -> dict[str, Any]:
    """OpenAI-shaped error envelope."""
    return {"error": {"message": message, "type": "assistant_gateway_error", "code": code}}


def sse(payload: dict[str, Any] | str) -> bytes:
    """One SSE ``data:`` frame."""
    if isinstance(payload, str):
        return f"data: {payload}\n\n".encode()
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def sse_citations(citations: list[Citation]) -> bytes:
    """A named SSE event carrying citations (ignored by OpenAI SDK parsers)."""
    data = json.dumps([citation.model_dump() for citation in citations], separators=(",", ":"))
    return f"event: citations\ndata: {data}\n\n".encode()


def sse_text(alias: AliasSpec, text: str) -> bytes:
    """Synthesise a single-content-chunk completion for local refusals."""
    chunk = {
        "id": _completion_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": alias.model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}],
    }
    return sse(chunk) + sse("[DONE]")
