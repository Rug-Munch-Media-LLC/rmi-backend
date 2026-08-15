"""Tests for the multi-account OpenCode Zen router (rugmunchbot/zen_router.py).

Covers: LRU account selection, per-account residential proxies, 429/403/5xx/
timeout failover with cooldown, exhaust → raise, env parsing (ZEN_ACCOUNTS
JSON + ZEN<n>_* flat vars), and the ``_llm.py`` wiring (router path vs legacy
single-account path).

``respx`` mocks the httpx transport (it intercepts clients even when a
``proxy=`` is passed, so failover logic is exercised with real AsyncClient
construction). ``respx`` is a dev-only dependency — see [REVIEW] note.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest
import respx
from httpx import Response

import app.domains.telegram.rugmunchbot.handlers.chat_ai._llm as _llm
from app.domains.telegram.rugmunchbot.zen_router import (
    ZenAccount,
    ZenAccountConfigError,
    ZenRouter,
    ZenRouterError,
    ZenRouterExhausted,
    accounts_from_env,
    build_router,
)

ZEN1_URL = "https://zen1.test/v1/chat/completions"
ZEN2_URL = "https://zen2.test/v1/chat/completions"
ZEN3_URL = "https://zen3.test/v1/chat/completions"

OK_PAYLOAD = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}


def _accounts(n: int = 2) -> list[ZenAccount]:
    urls = [ZEN1_URL, ZEN2_URL, ZEN3_URL]
    return [
        ZenAccount(
            base_url=urls[i],
            api_key=f"sk-test-{i + 1}",
            proxy=f"http://user:pass@proxy-{i + 1}.example:8080",
            name=f"zen{i + 1}",
        )
        for i in range(n)
    ]


class _FakeClock:
    """Injectable monotonic clock for cooldown tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _SpyClient:
    """httpx.AsyncClient spy recording kwargs (proxy wiring) with a canned 200."""

    instances: ClassVar[list[dict[str, Any]]] = []
    posted: ClassVar[list[tuple[str, dict[str, str] | None, dict[str, Any]]]] = []

    def __init__(self, **kwargs: Any) -> None:
        _SpyClient.instances.append(kwargs)

    async def __aenter__(self) -> _SpyClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> Any:
        _SpyClient.posted.append((url, headers, json or {}))

        class _Resp:
            status_code = 200

            def json(self) -> Any:
                return OK_PAYLOAD

        return _Resp()


# ── failover: 429 / 403 / 5xx / network error ─────────────────────────────


async def test_429_fails_over_to_next_account() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        a1 = mock.post(ZEN1_URL).mock(return_value=Response(429))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(2))
        result = await router.chat_completion({"model": "m", "messages": []}, timeout=10)
    assert result == OK_PAYLOAD
    assert a1.called
    assert a2.called
    # Account 1 is now cooling; account 2 is not.
    assert router.cooling_accounts() == ["zen1"]


async def test_all_429_raises_exhausted_and_cools_every_account() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(return_value=Response(429))
        mock.post(ZEN2_URL).mock(return_value=Response(429))
        mock.post(ZEN3_URL).mock(return_value=Response(429))
        router = ZenRouter(_accounts(3), max_retries=2)
        with pytest.raises(ZenRouterExhausted):
            await router.chat_completion({"model": "m"}, timeout=10)
    assert sorted(router.cooling_accounts()) == ["zen1", "zen2", "zen3"]


async def test_all_429_with_default_max_retries_raises() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(return_value=Response(429))
        mock.post(ZEN2_URL).mock(return_value=Response(429))
        router = ZenRouter(_accounts(2))
        with pytest.raises(ZenRouterExhausted):
            await router.chat_completion({"model": "m"}, timeout=10)


async def test_403_fails_over_to_next_account() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(return_value=Response(403))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(2))
        result = await router.chat_completion({"model": "m"}, timeout=10)
    assert result == OK_PAYLOAD
    assert a2.called


async def test_5xx_fails_over_to_next_account() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(return_value=Response(502))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(2))
        result = await router.chat_completion({"model": "m"}, timeout=10)
    assert result == OK_PAYLOAD
    assert a2.called


async def test_connect_error_fails_over_to_next_account() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(side_effect=httpx.ConnectError("proxy down"))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(2))
        result = await router.chat_completion({"model": "m"}, timeout=10)
    assert result == OK_PAYLOAD
    assert a2.called
    assert router.cooling_accounts() == ["zen1"]


async def test_non_retryable_4xx_raises_without_retry() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(return_value=Response(400))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(2))
        with pytest.raises(ZenRouterError):
            await router.chat_completion({"model": "m"}, timeout=10)
    # A 400 is a malformed-request error — retrying another account is useless.
    assert not a2.called


async def test_invalid_json_response_raises() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        mock.post(ZEN1_URL).mock(return_value=Response(200, content=b"not-json"))
        router = ZenRouter(_accounts(1))
        with pytest.raises(ZenRouterError):
            await router.chat_completion({"model": "m"}, timeout=10)


# ── LRU selection + cooldown (fake clock) ─────────────────────────────────


async def test_lru_rotation_round_robins_all_accounts() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        a1 = mock.post(ZEN1_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        a3 = mock.post(ZEN3_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(3))
        for _ in range(3):
            await router.chat_completion({"model": "m"}, timeout=10)
        first_round = (a1.call_count, a2.call_count, a3.call_count)
        await router.chat_completion({"model": "m"}, timeout=10)
    assert first_round == (1, 1, 1)  # one request per account
    assert (a1.call_count, a2.call_count, a3.call_count) == (2, 1, 1)  # back to zen1


async def test_cooling_account_is_skipped_and_recovers_after_cooldown() -> None:
    clock = _FakeClock()
    async with respx.mock(assert_all_called=False) as mock:
        a1 = mock.post(ZEN1_URL)
        a1.side_effect = [Response(429), Response(200, json=OK_PAYLOAD)]
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        a3 = mock.post(ZEN3_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(3), cooldown_s=60.0, clock=clock)

        # Request 1: zen1 429s → fails over to zen2 (zen1 now cooling).
        await router.chat_completion({"model": "m"}, timeout=10)
        assert a1.call_count == 1
        assert a2.call_count == 1
        assert router.cooling_accounts() == ["zen1"]

        # Request 2 (clock frozen): zen1 still cooling → zen3 is next (LRU).
        await router.chat_completion({"model": "m"}, timeout=10)
        assert a1.call_count == 1
        assert a3.call_count == 1

        # After 61s zen1 cools off → picked again (LRU) and succeeds.
        clock.now += 61.0
        await router.chat_completion({"model": "m"}, timeout=10)
        assert a1.call_count == 2
    assert router.cooling_accounts() == []


async def test_max_retries_zero_only_tries_one_account() -> None:
    async with respx.mock(assert_all_called=False) as mock:
        a1 = mock.post(ZEN1_URL).mock(return_value=Response(429))
        a2 = mock.post(ZEN2_URL).mock(return_value=Response(200, json=OK_PAYLOAD))
        router = ZenRouter(_accounts(2), max_retries=0)
        with pytest.raises(ZenRouterExhausted):
            await router.chat_completion({"model": "m"}, timeout=10)
    assert a1.called
    assert not a2.called


async def test_all_accounts_cooling_raises_immediately() -> None:
    clock = _FakeClock()
    router = ZenRouter(_accounts(1), clock=clock)
    router._mark_cooling(router._states[0])  # direct state poke for the test
    with pytest.raises(ZenRouterExhausted):
        await router.chat_completion({"model": "m"}, timeout=10)


# ── proxy + auth wiring ───────────────────────────────────────────────────


async def test_each_account_uses_its_own_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _SpyClient.instances = []
    _SpyClient.posted = []
    monkeypatch.setattr("app.domains.telegram.rugmunchbot.zen_router.httpx.AsyncClient", _SpyClient)
    router = ZenRouter(_accounts(2))
    await router.chat_completion({"model": "m"}, timeout=10)
    await router.chat_completion({"model": "m"}, timeout=10)
    proxies = [inst.get("proxy") for inst in _SpyClient.instances]
    assert proxies == [
        "http://user:pass@proxy-1.example:8080",
        "http://user:pass@proxy-2.example:8080",
    ]
    # Each account authenticates itself with its own key.
    assert _SpyClient.posted[0][1] == {"Authorization": "Bearer sk-test-1"}
    assert _SpyClient.posted[1][1] == {"Authorization": "Bearer sk-test-2"}


async def test_no_auth_header_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _SpyClient.instances = []
    _SpyClient.posted = []
    monkeypatch.setattr("app.domains.telegram.rugmunchbot.zen_router.httpx.AsyncClient", _SpyClient)
    router = ZenRouter([ZenAccount(base_url=ZEN1_URL, proxy="http://user:pass@proxy-1.example:8080")])
    await router.chat_completion({"model": "m"}, timeout=10)
    assert _SpyClient.posted[0][1] is None  # no Authorization header without api_key


# ── env parsing ───────────────────────────────────────────────────────────


def test_accounts_from_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ZEN_ACCOUNTS",
        '[{"name":"a","base_url":"https://a.test/v1/chat/completions","api_key":"sk-a",'
        '"proxy":"http://u:p@1.2.3.4:8080"},{"name":"b","base_url":"https://b.test/v1/chat/completions"}]',
    )
    accounts = accounts_from_env()
    assert accounts is not None
    assert len(accounts) == 2
    assert accounts[0].name == "a"
    assert accounts[0].api_key == "sk-a"
    assert accounts[0].proxy == "http://u:p@1.2.3.4:8080"
    assert accounts[1].api_key is None
    assert accounts[1].proxy is None
    router = build_router()
    assert router is not None
    assert router.account_count == 2


def test_accounts_from_env_numbered_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEN_ACCOUNTS", raising=False)
    monkeypatch.setenv("ZEN1_URL", ZEN1_URL)
    monkeypatch.setenv("ZEN1_KEY", "sk-1")
    monkeypatch.setenv("ZEN1_PROXY", "http://u:p@1.1.1.1:8080")
    monkeypatch.setenv("ZEN2_URL", ZEN2_URL)
    monkeypatch.setenv("ZEN_PROXY_URL", "http://shared:p@9.9.9.9:8080")
    accounts = accounts_from_env()
    assert accounts is not None
    assert len(accounts) == 2
    assert accounts[0].name == "zen-1"
    assert accounts[0].api_key == "sk-1"
    assert accounts[0].proxy == "http://u:p@1.1.1.1:8080"
    assert accounts[1].proxy == "http://shared:p@9.9.9.9:8080"  # shared fallback


def test_accounts_from_env_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEN_ACCOUNTS", raising=False)
    for i in range(1, 4):
        monkeypatch.delenv(f"ZEN{i}_URL", raising=False)
        monkeypatch.delenv(f"ZEN{i}_KEY", raising=False)
        monkeypatch.delenv(f"ZEN{i}_PROXY", raising=False)
    monkeypatch.delenv("ZEN_PROXY_URL", raising=False)
    assert accounts_from_env() is None
    assert build_router() is None


def test_accounts_from_env_empty_json_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN_ACCOUNTS", "   ")
    assert accounts_from_env() is None


def test_accounts_from_env_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN_ACCOUNTS", "not-json")
    with pytest.raises(ZenAccountConfigError):
        accounts_from_env()


def test_accounts_from_env_empty_array_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN_ACCOUNTS", "[]")
    with pytest.raises(ZenAccountConfigError):
        accounts_from_env()


def test_accounts_from_env_missing_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN_ACCOUNTS", '[{"api_key":"sk-a"}]')
    with pytest.raises(ZenAccountConfigError):
        accounts_from_env()


def test_router_requires_at_least_one_account() -> None:
    with pytest.raises(ZenAccountConfigError):
        ZenRouter([])


# ── _llm.py wiring ────────────────────────────────────────────────────────


async def test_llm_zen_uses_router_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ZEN_ACCOUNTS",
        '[{"name":"wired","base_url":"https://wired.test/v1/chat/completions","api_key":"sk-wired"}]',
    )
    monkeypatch.setattr(_llm, "_ZEN_ROUTER", None)
    monkeypatch.setattr(_llm, "_ZEN_ROUTER_TRIED", False)
    async with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://wired.test/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={"choices": [{"message": {"content": "grounded"}}], "usage": {"total_tokens": 42}},
            )
        )
        result = await _llm._call_zen_llm("q", 1, sources=[{"type": "web", "query": "honeypot"}])
    assert result is not None
    assert result["provider"] == "zen"
    assert result["message"] == "grounded"
    assert result["tokens"] == 42
    assert result["rag_count"] == 1
    assert route.called


async def test_llm_zen_router_exhausted_falls_back_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ZEN_ACCOUNTS",
        '[{"name":"wired","base_url":"https://wired.test/v1/chat/completions","api_key":"sk-wired"}]',
    )
    monkeypatch.setattr(_llm, "_ZEN_ROUTER", None)
    monkeypatch.setattr(_llm, "_ZEN_ROUTER_TRIED", False)
    async with respx.mock(assert_all_called=False) as mock:
        mock.post("https://wired.test/v1/chat/completions").mock(return_value=Response(429))
        assert await _llm._call_zen_llm("q", 1) is None


async def test_llm_zen_legacy_single_account_path_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the router to "not configured" regardless of the host env, so the
    # legacy egress-proxy path is exercised deterministically.
    monkeypatch.setattr(_llm, "_ZEN_ROUTER", None)
    monkeypatch.setattr(_llm, "_ZEN_ROUTER_TRIED", True)
    async with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://127.0.0.1:18081/zen/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={"choices": [{"message": {"content": "legacy"}}], "usage": {"total_tokens": 7}},
            )
        )
        result = await _llm._call_zen_llm("q", 1)
    assert result is not None
    assert result["provider"] == "zen"
    assert result["message"] == "legacy"
    assert route.called
