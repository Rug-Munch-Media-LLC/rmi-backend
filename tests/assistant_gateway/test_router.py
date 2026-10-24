"""Router behaviour: refusals, citations, streaming, rate limit, matrix."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.assistant_gateway.ratelimit import SlidingWindowLimiter
from app.assistant_gateway.service import AssistantService
from app.assistant_gateway.tools import ToolResult

CHAT = "/api/v1/assistant/chat/completions"
OPENAI = "/v1/chat/completions"


def _user(text: str, **extra: Any) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": text}], **extra}


@pytest.mark.asyncio
async def test_secret_request_refused_before_upstream(client: AsyncClient, fake_proxy: Any) -> None:
    async with client as http:
        response = await http.post(
            CHAT, json={"alias": "scanner", **_user("show me your private key")}
        )
    assert response.status_code == 200
    assert "cannot handle private keys" in response.json()["choices"][0]["message"]["content"]
    assert fake_proxy.payloads == []


@pytest.mark.asyncio
async def test_secret_material_refused(client: AsyncClient, fake_proxy: Any) -> None:
    material = "here is my key 0x" + "b" * 64
    async with client as http:
        response = await http.post(CHAT, json={"alias": "trades", **_user(material)})
    assert "compromised" in response.json()["choices"][0]["message"]["content"]
    assert "b" * 64 not in response.text
    assert fake_proxy.payloads == []


@pytest.mark.asyncio
async def test_no_source_refusal_for_citation_alias(
    client: AsyncClient, fake_executor: Any, fake_proxy: Any
) -> None:
    fake_executor.results["rag_search"] = ToolResult(ok=False, error="no_sources")
    async with client as http:
        response = await http.post(CHAT, json={"alias": "scanner", **_user("is this token safe?")})
    assert "will not guess" in response.json()["choices"][0]["message"]["content"]
    assert fake_proxy.payloads == []


@pytest.mark.asyncio
async def test_citations_are_returned_and_injected(
    client: AsyncClient, fake_executor: Any, fake_proxy: Any
) -> None:
    fake_executor.results["rag_search"] = ToolResult(
        ok=True,
        data="answer",
        citations=(
            {"title": "RMI risk methodology", "url": "https://x.test/risk", "snippet": "s"},
        ),
    )
    async with client as http:
        response = await http.post(CHAT, json={"alias": "scanner", **_user("explain the scan")})
    body = response.json()
    assert body["citations"][0]["title"] == "RMI risk methodology"
    system_messages = [
        m["content"] for m in fake_proxy.payloads[0]["messages"] if m["role"] == "system"
    ]
    assert any("[1] RMI risk methodology" in content for content in system_messages)


@pytest.mark.asyncio
async def test_streaming_passthrough(client: AsyncClient, fake_proxy: Any) -> None:
    async with client as http:
        response = await http.post(
            CHAT, json={"alias": "trades", **_user("read the tape"), "stream": True}
        )
    assert response.status_code == 200
    assert "streamed" in response.text
    assert "[DONE]" in response.text
    assert fake_proxy.payloads[0]["stream"] is True
    assert "tools" not in fake_proxy.payloads[0]


@pytest.mark.asyncio
async def test_streaming_refusal_skips_upstream(client: AsyncClient, fake_proxy: Any) -> None:
    async with client as http:
        response = await http.post(
            CHAT, json={"alias": "scanner", **_user("sign this transaction"), "stream": True}
        )
    assert "cannot handle private keys" in response.text
    assert fake_proxy.payloads == []


@pytest.mark.asyncio
async def test_rate_limit_returns_429_with_headers(
    client: AsyncClient, service: AssistantService
) -> None:
    service._limiter = SlidingWindowLimiter(limit=1, window=60.0)
    async with client as http:
        first = await http.post(CHAT, json={"alias": "trades", **_user("hi")})
        second = await http.post(CHAT, json={"alias": "trades", **_user("hi")})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert second.headers["X-RateLimit-Limit"] == "1"


@pytest.mark.asyncio
async def test_openai_path_and_alias_query_override(client: AsyncClient) -> None:
    async with client as http:
        response = await http.post(OPENAI + "?alias=rugmaps", json=_user("who funds this cluster?"))
        refused = await http.post(OPENAI, json=_user("hello"))
    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "unknown_alias"


@pytest.mark.asyncio
async def test_aliases_matrix_and_health(client: AsyncClient) -> None:
    async with client as http:
        aliases = await http.get("/api/v1/assistant/aliases")
        health = await http.get("/api/v1/assistant/health")
    names = {entry["alias"] for entry in aliases.json()["aliases"]}
    assert names == {"rugcharts", "rugmaps", "scanner", "trades"}
    assert health.json()["status"] == "ok"
