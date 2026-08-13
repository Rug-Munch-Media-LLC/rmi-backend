"""Pure unit tests for app.databus.proxy_rotator (no network, no external services)."""

from __future__ import annotations

from typing import Any

import pytest

from app.databus import proxy_rotator as pr

# ── Parsing ──────────────────────────────────────────────────────────


def test_parse_proxy_list_splits_on_commas_spaces_newlines() -> None:
    raw = "1.2.3.4:8080, 5.6.7.8:9090  9.10.11.12:3128\n13.14.15.16:80"
    urls = pr.parse_proxy_list(raw, username="user", password="pass")
    assert urls == [
        "http://user:pass@1.2.3.4:8080",
        "http://user:pass@5.6.7.8:9090",
        "http://user:pass@9.10.11.12:3128",
        "http://user:pass@13.14.15.16:80",
    ]


def test_parse_proxy_list_without_credentials_yields_plain_urls() -> None:
    assert pr.parse_proxy_list("1.2.3.4:8080 5.6.7.8:9090") == [
        "http://1.2.3.4:8080",
        "http://5.6.7.8:9090",
    ]


def test_parse_proxy_list_embedded_credentials_override_shared() -> None:
    urls = pr.parse_proxy_list("alice:secret@1.2.3.4:8080", username="shared", password="pw")
    assert urls == ["http://alice:secret@1.2.3.4:8080"]


def test_parse_proxy_list_skips_invalid_entries() -> None:
    urls = pr.parse_proxy_list("not-a-proxy, 1.2.3.4:8080, :9999, 1.2.3.4:abc, ")
    assert urls == ["http://1.2.3.4:8080"]


def test_parse_proxy_list_empty_and_whitespace_only() -> None:
    assert pr.parse_proxy_list("") == []
    assert pr.parse_proxy_list("   , ,\n  ") == []


def test_build_proxy_url_preserves_explicit_scheme() -> None:
    assert pr.build_proxy_url("https://1.2.3.4:8443", "u", "p") == "https://u:p@1.2.3.4:8443"
    assert pr.build_proxy_url("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"


def test_build_proxy_url_encodes_special_characters() -> None:
    url = pr.build_proxy_url("1.2.3.4:8080", "us er", "p@ss:word")
    assert url == "http://us%20er:p%40ss%3Aword@1.2.3.4:8080"


def test_build_proxy_url_invalid() -> None:
    assert pr.build_proxy_url("") is None
    assert pr.build_proxy_url("   ") is None
    assert pr.build_proxy_url("1.2.3.4") is None  # no port
    assert pr.build_proxy_url("1.2.3.4:abc") is None  # non-numeric port


# ── Rotation ─────────────────────────────────────────────────────────


def test_rotator_round_robin_cycles() -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80 2.2.2.2:80 3.3.3.3:80")
    assert rot.enabled
    assert rot.proxy_count == 3
    seen = [rot.get_rotating_proxy() for _ in range(6)]
    assert seen == [
        "http://1.1.1.1:80",
        "http://2.2.2.2:80",
        "http://3.3.3.3:80",
        "http://1.1.1.1:80",
        "http://2.2.2.2:80",
        "http://3.3.3.3:80",
    ]


def test_rotator_mark_failed_skips_until_cooldown_expires() -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80 2.2.2.2:80", failure_cooldown=60.0)
    first = rot.get_rotating_proxy()
    assert first == "http://1.1.1.1:80"
    rot.mark_failed(first)
    # failed proxy is skipped on every rotation while on cooldown
    assert rot.get_rotating_proxy() == "http://2.2.2.2:80"
    assert rot.get_rotating_proxy() == "http://2.2.2.2:80"
    # simulate cooldown expiry -> failed proxy is eligible again
    rot._entries[0].failed_at = 0.0  # deterministic unit-test poke
    assert rot.get_rotating_proxy() == "http://1.1.1.1:80"


def test_rotator_all_proxies_on_cooldown_returns_none() -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80 2.2.2.2:80")
    rot.mark_failed(rot.get_rotating_proxy())
    rot.mark_failed(rot.get_rotating_proxy())
    assert rot.get_rotating_proxy() is None  # caller must fall back to direct


def test_rotator_next_after_failure_marks_and_rotates() -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80 2.2.2.2:80")
    first = rot.get_rotating_proxy()
    assert first == "http://1.1.1.1:80"
    nxt = rot.next_after_failure(first)
    assert nxt == "http://2.2.2.2:80"
    # both proxies down -> direct fallback signal
    rot.mark_failed(rot.get_rotating_proxy())
    assert rot.next_after_failure() is None


def test_rotator_mark_failed_unknown_proxy_is_noop() -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80")
    rot.mark_failed("http://9.9.9.9:80")  # not a known entry -> ignored
    assert rot.get_rotating_proxy() == "http://1.1.1.1:80"
    rot.mark_failed(None)  # no-op
    assert rot.get_rotating_proxy() == "http://1.1.1.1:80"


# ── Client factory ───────────────────────────────────────────────────


class _RecordingAsyncClient:
    """Stand-in for httpx.AsyncClient that captures constructor kwargs."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.fixture
def recording_client(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> _RecordingAsyncClient:
        captured.append(kwargs)
        return _RecordingAsyncClient(**kwargs)

    monkeypatch.setattr(pr.httpx, "AsyncClient", factory)
    return captured


def test_get_client_uses_rotating_proxy(recording_client: list[dict[str, Any]]) -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80 2.2.2.2:80", username="user", password="pass")
    rot.get_client()
    assert recording_client[0]["proxy"] == "http://user:pass@1.1.1.1:80"
    rot.get_client()
    assert recording_client[1]["proxy"] == "http://user:pass@2.2.2.2:80"


def test_get_client_accepts_explicit_proxy(recording_client: list[dict[str, Any]]) -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80", username="u", password="p")
    rot.get_client(proxy="http://u:p@1.1.1.1:80")
    assert recording_client[0]["proxy"] == "http://u:p@1.1.1.1:80"


def test_get_client_force_direct_even_when_enabled(
    recording_client: list[dict[str, Any]],
) -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80")
    rot.get_client(proxy=None)
    assert recording_client[0]["proxy"] is None


def test_get_client_sane_timeouts(recording_client: list[dict[str, Any]]) -> None:
    rot = pr.ProxyRotator(proxy_list="1.1.1.1:80")
    rot.get_client()
    timeout = recording_client[0]["timeout"]
    assert isinstance(timeout, pr.httpx.Timeout)
    assert timeout.connect == pr.DEFAULT_CONNECT_TIMEOUT
    assert timeout.read == pr.DEFAULT_TIMEOUT
    assert recording_client[0]["follow_redirects"] is True


# ── Disabled / env-driven behavior ───────────────────────────────────


def test_disabled_when_no_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
    recording_client: list[dict[str, Any]],
) -> None:
    monkeypatch.delenv(pr.PROXY_LIST_ENV, raising=False)
    monkeypatch.delenv(pr.PROXY_USER_ENV, raising=False)
    monkeypatch.delenv(pr.PROXY_PASS_ENV, raising=False)
    rot = pr.ProxyRotator()
    assert not rot.enabled
    assert rot.proxy_count == 0
    assert rot.get_rotating_proxy() is None
    rot.get_client()
    assert recording_client[0]["proxy"] is None  # direct client


def test_singleton_lazy_reads_env_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr.PROXY_LIST_ENV, "1.1.1.1:80 2.2.2.2:80")
    pr.reset_proxy_rotator()
    try:
        rot = pr.get_proxy_rotator()
        assert rot.enabled
        assert rot.proxy_count == 2
        assert pr.get_proxy_rotator() is rot  # cached, env not re-read
        monkeypatch.setenv(pr.PROXY_LIST_ENV, "9.9.9.9:80")
        assert pr.get_proxy_rotator() is rot  # env change does not affect instance
        assert rot.proxy_count == 2
        pr.reset_proxy_rotator()
        assert pr.get_proxy_rotator() is not rot  # fresh instance after reset
    finally:
        pr.reset_proxy_rotator()
