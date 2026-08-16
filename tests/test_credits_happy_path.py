"""Happy-path tests for the /credits (admin) user journey.

Covers ``app/domains/telegram/rugmunchbot/openrouter_credit``:

  * ``fetch_openrouter_credit`` — a mocked OpenRouter ``/key`` response is
    parsed into a credit summary dict (usage / limit / remaining / free tier).
  * ``format_credit_summary`` — renders the credit block for /credits and
    /admin (never the key).
  * fail-open — an HTTP error degrades to ``ok=False`` instead of raising.

The OpenRouter API key is faked at the ``app.llm_config`` boundary and
``httpx.AsyncClient`` is faked — no network I/O.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot import openrouter_credit as oc


class _FakeCreditClient:
    """httpx.AsyncClient class fake recording GETs (configurable response)."""

    _status: ClassVar[int] = 200
    _payload: ClassVar[Any] = {"data": {"usage": 1.23, "limit": 10.0, "limit_remaining": 8.77, "is_free_tier": False}}
    calls: ClassVar[list[tuple[str, dict | None]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeCreditClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, headers: dict | None = None) -> Any:
        _FakeCreditClient.calls.append((url, headers))

        class _Resp:
            status_code = _FakeCreditClient._status

            def json(self) -> Any:
                return _FakeCreditClient._payload

        return _Resp()


@pytest.fixture(autouse=True)
def _fake_httpx_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeCreditClient._status = 200
    _FakeCreditClient._payload = {
        "data": {"usage": 1.23, "limit": 10.0, "limit_remaining": 8.77, "is_free_tier": False}
    }
    _FakeCreditClient.calls = []
    monkeypatch.setattr(oc.httpx, "AsyncClient", _FakeCreditClient)
    monkeypatch.setattr("app.llm_config.OPENROUTER_BACKUP_API_KEY", "test-openrouter-key")


# ── fetch_openrouter_credit: /key response → credit summary ───────────────


async def test_fetch_openrouter_credit_parses_key_response() -> None:
    data = await oc.fetch_openrouter_credit()

    assert data["ok"] is True
    assert data["usage"] == 1.23
    assert data["limit"] == 10.0
    assert data["limit_remaining"] == 8.77
    assert data["is_free_tier"] is False
    assert data["error"] is None

    # The call went to the real OpenRouter /key endpoint with auth headers.
    url, headers = _FakeCreditClient.calls[-1]
    assert url == oc.OPENROUTER_KEY_ENDPOINT
    assert headers is not None
    assert headers["Authorization"] == "Bearer test-openrouter-key"


async def test_fetch_openrouter_credit_uncapped_key_reports_none_limit() -> None:
    _FakeCreditClient._payload = {"data": {"usage": 2.5, "limit": None, "limit_remaining": None, "is_free_tier": True}}
    data = await oc.fetch_openrouter_credit()
    assert data["ok"] is True
    assert data["limit"] is None
    assert data["limit_remaining"] is None
    assert data["is_free_tier"] is True


async def test_fetch_openrouter_credit_computes_remaining_when_missing() -> None:
    # OpenRouter may omit limit_remaining; the module computes limit - usage.
    _FakeCreditClient._payload = {"data": {"usage": 3.0, "limit": 10.0, "is_free_tier": False}}
    data = await oc.fetch_openrouter_credit()
    assert data["limit_remaining"] == 7.0


async def test_fetch_openrouter_credit_fail_open_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.llm_config.OPENROUTER_BACKUP_API_KEY", "test-openrouter-key")
    _FakeCreditClient._status = 401
    data = await oc.fetch_openrouter_credit()
    assert data["ok"] is False
    assert data["error"] == "http_401"


# ── format_credit_summary: /credits rendering ─────────────────────────────


def test_format_credit_summary_renders_usage_limit_remaining() -> None:
    text = oc.format_credit_summary(
        {"ok": True, "usage": 1.23, "limit": 10.0, "limit_remaining": 8.77, "is_free_tier": False, "error": None}
    )
    assert "OpenRouter credits" in text
    assert "Usage: <b>$1.23</b>" in text
    assert "Remaining: <b>$8.77</b> / $10.00" in text
    assert "free tier" not in text
    # The API key is never rendered.
    assert "test-openrouter-key" not in text


def test_format_credit_summary_free_tier_note() -> None:
    text = oc.format_credit_summary(
        {"ok": True, "usage": 0.5, "limit": 1.0, "limit_remaining": 0.5, "is_free_tier": True, "error": None}
    )
    assert "(free tier)" in text


def test_format_credit_summary_unlimited_limit() -> None:
    text = oc.format_credit_summary(
        {"ok": True, "usage": 2.5, "limit": None, "limit_remaining": None, "is_free_tier": False, "error": None}
    )
    assert "Limit: <b>unlimited</b>" in text


def test_format_credit_summary_fail_open_renders_error_line() -> None:
    text = oc.format_credit_summary({"ok": False, "error": "http_401", "usage": None, "limit": None})
    assert "unavailable" in text
    assert "http_401" in text
