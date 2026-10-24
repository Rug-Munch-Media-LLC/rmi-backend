"""Fakes and fixtures for the page assistant gateway tests (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.assistant_gateway.ratelimit import SlidingWindowLimiter
from app.assistant_gateway.service import AssistantService
from app.assistant_gateway.tools import ToolResult

FINAL = {
    "choices": [{"message": {"role": "assistant", "content": "final answer"}}],
    "usage": {"total_tokens": 11},
}


class FakeProxy:
    """Scripted LiteLLM stand-in; records every payload it receives."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [FINAL])
        self.payloads: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    async def stream(self, payload: dict[str, Any]):
        self.payloads.append(payload)
        yield b'data: {"choices":[{"delta":{"content":"streamed"}}]}\n\n'
        yield b"data: [DONE]\n\n"


class FakeExecutor:
    """Records tool calls and returns canned results."""

    def __init__(self, results: dict[str, ToolResult] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results = results or {}

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        return self.results.get(name, ToolResult(ok=True, data={"ok": True}))


@pytest.fixture
def fake_proxy() -> FakeProxy:
    return FakeProxy()


@pytest.fixture
def fake_executor() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture
def service(fake_proxy: FakeProxy, fake_executor: FakeExecutor) -> AssistantService:
    return AssistantService(
        proxy=fake_proxy,  # type: ignore[arg-type]
        executor=fake_executor,
        limiter=SlidingWindowLimiter(limit=50, window=60.0),
    )


@pytest.fixture
def client(service: AssistantService) -> AsyncClient:
    from app.assistant_gateway.router import EXTRA_ROUTERS, get_service, router

    app = FastAPI()
    app.include_router(router)
    for extra in EXTRA_ROUTERS:
        app.include_router(extra)
    app.dependency_overrides[get_service] = lambda: service
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")
