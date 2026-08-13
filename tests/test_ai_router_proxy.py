"""Tests for ai_router's residential-proxy wiring (PURE — rotator is mocked).

Covers the gmgn_provider-style proxy→direct fallback added to the chat
completion path:

- the client is built through the rotator when it is enabled and the
  destination is public (proxy-first), and direct when disabled or when the
  destination is a private/loopback host (local llama.cpp);
- a 429/403/5xx response or a transport error on the proxied egress marks the
  proxy failed (cooldown) and retries the request once against a direct
  client.

No network and no database: the rotator and every httpx client are fakes.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai_router import _build_ai_client, _post_chat

PROXY_URL = "http://proxy.example:8080"


class _FakeResp:
    """Minimal ``httpx.Response`` stand-in with an ``elapsed`` timer."""

    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = ""
        self.elapsed = type("_Elapsed", (), {"total_seconds": lambda self: 0.123})()

    def json(self) -> dict[str, Any]:
        return self._body


def _ok_body(content: str = "hello from model") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "choices": [{"message": {"content": content}}],
        "model": "test-model",
        "usage": {"total_tokens": 5},
    }


class _FakeClient:
    """Scripted client: returns queued responses, records posts/close state."""

    def __init__(self, proxy: str | None = None, responses: list[_FakeResp] | None = None) -> None:
        self.proxy = proxy
        self._responses = list(responses or [])
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> _FakeResp:
        self.posts.append((url, kwargs))
        if self._responses:
            return self._responses.pop(0)
        return _FakeResp(200, _ok_body())

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class _FakeRotator:
    """Records every client build and proxy failure (never touches the network)."""

    def __init__(self, enabled: bool, proxy_url: str | None = PROXY_URL) -> None:
        self.enabled = enabled
        self.proxy_url = proxy_url
        self.client_calls: list[str | None] = []
        self.failed: list[str | None] = []
        self._direct = _FakeClient(proxy=None)
        self._proxied = _FakeClient(proxy=proxy_url)

    def get_rotating_proxy(self) -> str | None:
        return self.proxy_url

    def get_client(self, *, proxy: object = None) -> _FakeClient:
        resolved: str | None = None if proxy is None else str(proxy)
        self.client_calls.append(resolved)
        return self._direct if resolved is None else self._proxied

    def mark_failed(self, proxy: str | None) -> None:
        self.failed.append(proxy)


# ── _build_ai_client: proxy vs direct selection ─────────────────────────────


def test_build_ai_client_routes_through_rotating_proxy_when_enabled_and_public() -> None:
    rotator = _FakeRotator(enabled=True)
    client, proxy_url = _build_ai_client(rotator, "https://openrouter.ai/api/v1/chat/completions")
    assert proxy_url == PROXY_URL
    assert rotator.client_calls == [PROXY_URL]
    assert client.proxy == PROXY_URL


def test_build_ai_client_direct_when_disabled() -> None:
    rotator = _FakeRotator(enabled=False)
    client, proxy_url = _build_ai_client(rotator, "https://openrouter.ai/api/v1/chat/completions")
    assert proxy_url is None
    assert rotator.client_calls == [None]
    assert client.proxy is None


def test_build_ai_client_skips_proxy_for_private_llama_host() -> None:
    rotator = _FakeRotator(enabled=True)
    client, proxy_url = _build_ai_client(rotator, "http://172.22.0.1:8085/v1/chat/completions")
    assert proxy_url is None
    assert rotator.client_calls == [None]
    assert client.proxy is None


# ── chat_completion wiring (rotator mocked, no network) ─────────────────────


@pytest.mark.asyncio
async def test_chat_completion_builds_client_via_rotator_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai_router

    rotator = _FakeRotator(enabled=True)
    monkeypatch.setattr(app.ai_router, "get_proxy_rotator", lambda: rotator)
    monkeypatch.setattr(app.ai_router, "LLAMA_HOST", "openrouter.ai")  # public host → proxy tier engages

    result = await app.ai_router.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        model="qwen-chat",
    )
    assert result["content"] == "hello from model"
    assert rotator.client_calls[0] == PROXY_URL
    assert rotator.failed == []


@pytest.mark.asyncio
async def test_chat_completion_direct_when_rotator_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai_router

    rotator = _FakeRotator(enabled=False)
    monkeypatch.setattr(app.ai_router, "get_proxy_rotator", lambda: rotator)
    monkeypatch.setattr(app.ai_router, "LLAMA_HOST", "openrouter.ai")

    result = await app.ai_router.chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        model="qwen-chat",
    )
    assert result["content"] == "hello from model"
    assert rotator.client_calls[0] is None
    assert rotator.failed == []


# ── _post_chat: proxy→direct retry on failure ───────────────────────────────


@pytest.mark.asyncio
async def test_post_chat_429_marks_proxy_failed_and_retries_direct() -> None:
    rotator = _FakeRotator(enabled=True)
    rotator._proxied._responses = [_FakeResp(429)]
    rotator._direct._responses = [_FakeResp(200, _ok_body(content="direct fallback"))]

    result = await _post_chat(
        rotator._proxied,
        "https://openrouter.ai/api/v1/chat/completions",
        "deepseek-v4-flash",
        [{"role": "user", "content": "hi"}],
        0.7,
        100,
        provider="test",
        proxy_url=PROXY_URL,
        rotator=rotator,
    )
    assert result is not None
    assert result["content"] == "direct fallback"
    assert rotator.failed == [PROXY_URL]
    assert rotator.client_calls == [None]  # only the direct retry client was built
    assert rotator._direct.closed is True


@pytest.mark.asyncio
async def test_post_chat_proxy_transport_error_marks_failed_and_retries_direct() -> None:
    rotator = _FakeRotator(enabled=True)

    class _RaisingClient(_FakeClient):
        async def post(self, url: str, **kwargs: Any) -> _FakeResp:
            raise httpx.ConnectError("proxy unreachable", request=None)

    rotator._proxied = _RaisingClient(proxy=PROXY_URL)
    rotator._direct._responses = [_FakeResp(200, _ok_body(content="direct ok"))]

    result = await _post_chat(
        rotator._proxied,
        "https://openrouter.ai/api/v1/chat/completions",
        "deepseek-v4-flash",
        [{"role": "user", "content": "hi"}],
        0.7,
        100,
        provider="test",
        proxy_url=PROXY_URL,
        rotator=rotator,
    )
    assert result is not None
    assert result["content"] == "direct ok"
    assert rotator.failed == [PROXY_URL]
    assert rotator._direct.closed is True


@pytest.mark.asyncio
async def test_post_chat_direct_passthrough_no_proxy_logic() -> None:
    rotator = _FakeRotator(enabled=False)
    result = await _post_chat(
        rotator._direct,
        "http://172.22.0.1:8085/v1/chat/completions",
        "qwen-chat",
        [{"role": "user", "content": "hi"}],
        0.7,
        100,
        provider="llama-direct",
    )
    assert result is not None
    assert result["content"] == "hello from model"
    assert rotator.failed == []
    assert len(rotator._direct.posts) == 1
