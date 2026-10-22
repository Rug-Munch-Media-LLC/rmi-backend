"""Unit tests for the brand-chat model chain — no network, no API keys."""

from __future__ import annotations

import pytest

from app.domains.chat import providers
from app.domains.chat.breaker import BREAKER, CircuitBreaker
from app.domains.chat.model_chain import complete
from app.domains.chat.providers import ProviderSpec, resolve_chain
from app.domains.chat.retrieval_fallback import retrieval_only_answer
from app.domains.chat.transport import ProviderError

MESSAGES = [{"role": "user", "content": "what is ETH?"}]
OK = ("Answer text", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})


# ── ordering / fallback ────────────────────────────────────────────────
async def test_falls_through_in_documented_order(monkeypatch):
    chain = resolve_chain()
    assert [s.provider for s in chain][:3] == ["openrouter", "openrouter", "nvidia"]
    assert chain[-1].provider == "local"
    failing = {chain[0].key, chain[1].key}
    recorder = Recorder(failing)
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)

    result = await complete(MESSAGES, max_tokens=64)

    assert result["provider"] == chain[2].provider
    assert result["model"] == chain[2].model
    assert result["fallback_used"] is True
    assert result["text"] == OK[0]
    assert result["cost_usd"] == 0.0
    assert [e["provider"] for e in result["errors"]] == ["openrouter", "openrouter"]
    assert recorder.calls == [chain[0].key, chain[1].key, chain[2].key]


async def test_transient_error_retries_once_then_moves_on(monkeypatch):
    chain = resolve_chain()
    recorder = Recorder({chain[0].key}, ProviderError("HTTP 503", retryable=True))
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)

    result = await complete(MESSAGES)

    assert recorder.calls[:3] == [chain[0].key, chain[0].key, chain[1].key]
    assert result["provider"] == chain[1].provider
    assert result["attempts"] == 3


async def test_permanent_error_is_not_retried(monkeypatch):
    chain = resolve_chain()
    recorder = Recorder({chain[0].key}, ProviderError("HTTP 401", retryable=False))
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)

    await complete(MESSAGES)

    assert recorder.calls.count(chain[0].key) == 1


async def test_three_failures_open_the_breaker_and_skip_the_lane(monkeypatch):
    chain = resolve_chain()
    recorder = Recorder({chain[0].key}, ProviderError("HTTP 500", retryable=False))
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)

    for _ in range(3):
        await complete(MESSAGES)
    # 3 separate compositions each fail lane 0 once -> breaker opens
    assert BREAKER.state(chain[0].key) == "open"
    recorder.calls.clear()
    await complete(MESSAGES)
    assert chain[0].key not in recorder.calls


# ── retrieval-only last resort ─────────────────────────────────────────
async def test_never_raises_and_ends_at_retrieval_only(monkeypatch):
    chain = resolve_chain()
    recorder = Recorder({spec.key for spec in chain}, ProviderError("HTTP 500", retryable=False))
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)

    result = await complete(
        MESSAGES,
        retrieval=[{"text": "ETH is a token.", "source": "internal/kb", "url": "https://x/y"}],
    )

    assert result["provider"] == "retrieval_only"
    assert result["fallback_used"] is True
    assert result["degraded"] is True
    assert result["tokens"]["total_tokens"] == 0
    assert "ETH is a token." in result["text"]
    assert "internal/kb" in result["text"]
    assert "https://x/y" in result["text"]
    assert len(result["errors"]) >= len(chain)


async def test_no_retrieval_supplied_defers_to_caller(monkeypatch):
    """No evidence passed in -> empty text, so the chat layer renders its own."""
    recorder = Recorder({spec.key for spec in resolve_chain()})
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)
    result = await complete(MESSAGES)
    assert result["provider"] == "retrieval_only"
    assert result["text"] == ""


async def test_chain_budget_skips_remaining_lanes(monkeypatch):
    calls: list[str] = []

    async def slow(spec, messages, max_tokens):
        calls.append(spec.key)
        raise ProviderError("HTTP 500", retryable=True)

    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", slow)
    result = await complete(MESSAGES, timeout_s=0.0)
    assert calls == []
    assert result["provider"] == "retrieval_only"
    assert any("budget exhausted" in e["error"] for e in result["errors"])


async def test_timeout_s_kwarg_is_accepted(monkeypatch):
    """ports.py calls complete(messages, timeout_s=...) — must not TypeError."""
    recorder = Recorder(set())
    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", recorder)
    result = await complete(MESSAGES, timeout_s=30.0)
    assert result["provider"] == resolve_chain()[0].provider


async def test_unexpected_exception_does_not_escape(monkeypatch):
    async def boom(spec, messages, max_tokens):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr("app.domains.chat.model_chain.request_provider", boom)
    result = await complete(MESSAGES)
    assert result["provider"] == "retrieval_only"
    assert "socket exploded" in str(result["errors"])


def test_retrieval_only_is_deterministic_and_handles_missing_sources():
    items = [{"text": "a\n b", "source": "kb"}]
    assert retrieval_only_answer(items) == retrieval_only_answer(items)
    assert "No matching sources" in retrieval_only_answer(None)


# ── cost guard ─────────────────────────────────────────────────────────
def test_paid_lane_refused_unless_opted_in(monkeypatch):
    paid = ProviderSpec(
        provider="paid",
        model="deepseek/deepseek-flash",
        url="https://example.invalid/v1/chat/completions",
        key_env="PAID_KEY",
        free=False,
    )
    monkeypatch.delenv("CHAT_ALLOW_PAID", raising=False)
    monkeypatch.setattr(providers, "CHAIN", (paid,))
    assert resolve_chain() == ()
    assert "paid model refused" in providers.refusal_reasons()[0]

    monkeypatch.setenv("CHAT_ALLOW_PAID", "1")
    assert resolve_chain() == (paid,)
