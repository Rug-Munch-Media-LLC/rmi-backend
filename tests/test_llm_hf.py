"""Tests for the Hugging Face Inference API chat fallback (app/llm_config.py).

Covers:
  - get_hf_config() shape + env overrides
  - chat_with_hf_fallback success path (think tags stripped)
  - fail-open on 401/402/429/5xx and on transport errors
  - anonymous tier still attempts a call when no key is set
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app import llm_config


@pytest.fixture
def hf_env(monkeypatch):
    """Expose a test HF key so config resolution is deterministic."""
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf_test_token")
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    return None


class _FakeClient:
    """Minimal async httpx client stand-in with a configurable .post()."""

    def __init__(self, post) -> None:
        self._post = post

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url, headers=None, json=None, **kwargs):
        return await self._post(url, headers=headers, json=json)


def _patch_client(post) -> patch:
    return patch("httpx.AsyncClient", return_value=_FakeClient(post))


def _ok_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions"),
    )


class TestHfConfig:
    def test_defaults(self, hf_env) -> None:
        cfg = llm_config.get_hf_config()
        assert cfg["model"] == "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
        assert cfg["base_url"] == "https://router.huggingface.co/v1/chat/completions"
        assert cfg["api_key"] == "hf_test_token"
        assert cfg["max_tokens"] > 0

    def test_env_overrides(self, monkeypatch) -> None:
        monkeypatch.setenv("RMI_HF_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
        monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf_other")
        cfg = llm_config.get_hf_config()
        assert cfg["model"] == "Qwen/Qwen2.5-Coder-7B-Instruct"
        assert cfg["api_key"] == "hf_other"

    def test_legacy_token_env_fallback(self, monkeypatch) -> None:
        monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_legacy")
        assert llm_config.get_hf_config()["api_key"] == "hf_legacy"


class TestHfChatFallback:
    async def test_success_strips_think_tags(self, hf_env) -> None:
        async def _post(url, headers=None, json=None):
            return _ok_response("<think>reasoning…</think>Yes, single-holder supply is a red flag.")

        with _patch_client(_post):
            out = await llm_config.chat_with_hf_fallback(
                [{"role": "user", "content": "Is single-holder supply a red flag?"}],
                max_tokens=64,
            )
        assert out == "Yes, single-holder supply is a red flag."

    @pytest.mark.parametrize("status", [401, 402, 429, 503])
    async def test_fails_open_on_http_error(self, hf_env, status: int) -> None:
        async def _post(url, headers=None, json=None):
            return httpx.Response(status, text="err", request=httpx.Request("POST", "https://router.huggingface.co"))

        with _patch_client(_post):
            out = await llm_config.chat_with_hf_fallback([{"role": "user", "content": "hi"}])
        assert out == ""

    async def test_fails_open_on_transport_error(self, hf_env) -> None:
        async def _post(url, headers=None, json=None):
            raise httpx.ConnectError("no route to host")

        with _patch_client(_post):
            out = await llm_config.chat_with_hf_fallback([{"role": "user", "content": "hi"}])
        assert out == ""

    async def test_empty_key_returns_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        out = await llm_config.chat_with_hf_fallback([{"role": "user", "content": "hi"}])
        assert out == ""

    async def test_anonymous_still_posts_without_auth_header(self, monkeypatch) -> None:
        monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        captured: dict = {}

        async def _post(url, headers=None, json=None):
            captured["headers"] = headers
            return _ok_response("anon ok")

        with _patch_client(_post):
            out = await llm_config.chat_with_hf_fallback([{"role": "user", "content": "hi"}])
        assert out == "anon ok"
        assert "Authorization" not in (captured["headers"] or {})


class TestHfAnalysisEndpoint:
    """POST /api/v1/ai/hf — bounded + fail-open community-chat ANALYSIS endpoint."""

    @staticmethod
    def _client():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routers.ai_stream import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    async def test_missing_key_returns_explicit_error(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        with self._client() as client:
            resp = client.post("/api/v1/ai/hf", json={"prompt": "detailed token analysis", "task": "complex"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == ""
        assert "not set" in body["error"]
        assert body["provider"] == "huggingface"

    async def test_provider_failure_fails_open(self, hf_env):
        from unittest.mock import AsyncMock, patch

        with (
            patch("app.routers.ai_stream.llm_config.chat_with_hf_fallback", AsyncMock(return_value="")),
            self._client() as client,
        ):
            resp = client.post("/api/v1/ai/hf", json={"prompt": "analysis", "task": "complex"})
        body = resp.json()
        assert body["response"] == ""
        assert "failed" in body["error"]
        assert body["provider"] == "huggingface"

    async def test_success_returns_answer(self, hf_env):
        from unittest.mock import AsyncMock, patch

        with (
            patch(
                "app.routers.ai_stream.llm_config.chat_with_hf_fallback",
                AsyncMock(return_value="Deployer-controlled supply is a top rug flag."),
            ),
            self._client() as client,
        ):
            resp = client.post("/api/v1/ai/hf", json={"prompt": "analysis", "task": "complex"})
        body = resp.json()
        assert body["response"] == "Deployer-controlled supply is a top rug flag."
        assert body["model"] == llm_config.HF_MODEL
