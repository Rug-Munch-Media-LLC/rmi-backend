# ruff: noqa: TC001
"""Bounded server-side tool loop for the page assistant.

The loop re-checks the alias grant on every model-issued call (the model is not
trusted), executes at most four calls per turn, and forces a tool-free final
turn so an answer is always produced within ``max_steps``.
"""

from __future__ import annotations

import json
from typing import Any

from app.assistant_gateway.aliases import AliasSpec
from app.assistant_gateway.builder import upstream_payload
from app.assistant_gateway.models import ChatCompletionRequest
from app.assistant_gateway.policy import tool_arguments
from app.assistant_gateway.proxy import LiteLLMProxy
from app.assistant_gateway.tools import ToolExecutor, is_allowed

STEP_EXHAUSTED = (
    "I could not complete that within the allowed tool steps. "
    "Ask a narrower question about the current page."
)

MAX_CALLS_PER_TURN = 4
MAX_TOOL_CONTENT = 6000


def _first_message(body: dict[str, Any]) -> dict[str, Any]:
    """Extract ``choices[0].message`` defensively."""
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    return message if isinstance(message, dict) else {}


async def _tool_message(
    executor: ToolExecutor, alias: AliasSpec, call: dict[str, Any]
) -> dict[str, Any]:
    """Execute one model tool call, or record the denial, as a ``tool`` turn."""
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    if not is_allowed(alias, name):
        content = json.dumps({"error": "tool_not_granted"})
    else:
        result = await executor.run(name, tool_arguments(function.get("arguments")))
        content = json.dumps(
            {"ok": result.ok, "data": result.data, "error": result.error}, default=str
        )[:MAX_TOOL_CONTENT]
    return {
        "role": "tool",
        "tool_call_id": str(call.get("id") or ""),
        "name": name,
        "content": content,
    }


async def run_tool_loop(
    proxy: LiteLLMProxy,
    executor: ToolExecutor,
    alias: AliasSpec,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    request: ChatCompletionRequest,
) -> tuple[str, dict[str, int]]:
    """Run at most ``max_steps`` upstream turns and return (answer, usage)."""
    usage: dict[str, int] = {}
    for step in range(request.max_steps):
        payload = upstream_payload(
            alias,
            messages,
            tools if step < request.max_steps - 1 else [],
            stream=False,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        body = await proxy.complete(payload)
        usage = body.get("usage") or usage
        message = _first_message(body)
        calls = message.get("tool_calls") or []
        content = str(message.get("content") or "")
        if not calls:
            return content or STEP_EXHAUSTED, usage
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls[:MAX_CALLS_PER_TURN]:
            messages.append(await _tool_message(executor, alias, call))
    return STEP_EXHAUSTED, usage
