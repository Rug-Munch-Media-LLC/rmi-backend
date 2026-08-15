"""Tests for the pooled keep-alive AsyncClient in ``tools/_client.py``.

Proves the pooling contract:
  (a) ``get_shared_client`` returns the SAME instance per timeout bucket
      (connections stay alive across calls — no per-call TLS handshake);
  (b) distinct timeout buckets get distinct clients (per-call timeout
      semantics preserved);
  (c) a stale client created under a different faked ``httpx.AsyncClient``
      class is re-created (test files install their own class fakes);
  (d) ``close_client`` closes and drops every pooled client (fail-open);
  (e) the http_* helpers still route through the faked AsyncClient and
      record calls (no per-request ``timeout`` kwarg → fake-compatible).

No live network — ``httpx.AsyncClient`` is faked at the ``_client`` module
boundary, mirroring ``tests/domains/test_rugmunchbot_tools.py``.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.tools import _client


class _FakeAsyncClient:
    """Class-fake AsyncClient — records requests, never touches the network."""

    _responses: ClassVar[dict[str, Any]] = {}
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "GET", "url": url})
        return self._respond(url)

    async def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "POST", "url": url})
        return self._respond(url)

    def _respond(self, url: str) -> Any:
        for prefix, body in _FakeAsyncClient._responses.items():
            if prefix in url:

                class _Resp:
                    status_code = 200

                    def json(self, _body: Any = body) -> Any:
                        return _body

                return _Resp()

        class _Resp404:
            status_code = 404

            def json(self) -> Any:
                return {"error": "not found"}

        return _Resp404()


@pytest.fixture(autouse=True)
def _fake_http_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the httpx boundary for every test; reset pooled-client state."""
    _FakeAsyncClient._responses = {}
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)
    _client._shared_clients.clear()
    yield
    _client._shared_clients.clear()


class TestSharedClient:
    def test_same_instance_per_timeout_bucket(self) -> None:
        a = _client.get_shared_client(8.0)
        b = _client.get_shared_client(8.0)
        assert a is b  # keep-alive: same pooled client reused

    def test_distinct_buckets_get_distinct_clients(self) -> None:
        a = _client.get_shared_client(8.0)
        b = _client.get_shared_client(30.0)
        assert a is not b

    def test_recreates_when_faked_class_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # First call creates a client under the autouse fake.
        first = _client.get_shared_client(8.0)
        assert isinstance(first, _FakeAsyncClient)

        class _OtherFake:  # a different test-file-style fake class
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

        monkeypatch.setattr(_client.httpx, "AsyncClient", _OtherFake)
        second = _client.get_shared_client(8.0)
        assert isinstance(second, _OtherFake)
        assert second is not first

    async def test_close_client_drops_and_recreates(self) -> None:
        first = _client.get_shared_client(8.0)
        await _client.close_client()
        assert _client._shared_clients == {}
        second = _client.get_shared_client(8.0)
        assert second is not first


class TestHelpersThroughPooledClient:
    async def test_http_get_json_uses_pooled_client_and_records_calls(self) -> None:
        _FakeAsyncClient._responses = {"api.example.com": {"ok": True}}
        body = await _client.http_get_json("https://api.example.com/v1/data", timeout=8.0)
        assert body == {"ok": True}
        assert len(_FakeAsyncClient.calls) == 1
        assert _FakeAsyncClient.calls[0]["method"] == "GET"
        # The same pooled instance served the call.
        assert isinstance(_client.get_shared_client(8.0), _FakeAsyncClient)

    async def test_http_post_json_uses_pooled_client(self) -> None:
        _FakeAsyncClient._responses = {"api.example.com": {"created": True}}
        body = await _client.http_post_json("https://api.example.com/v1/create", json={"a": 1}, timeout=8.0)
        assert body == {"created": True}
        assert _FakeAsyncClient.calls[-1]["method"] == "POST"

    async def test_http_get_json_never_passes_timeout_to_request(self) -> None:
        # The fake AsyncClient.get has NO timeout kwarg — the pooled client's
        # own timeout (per bucket) must carry it, keeping fakes compatible.
        _FakeAsyncClient._responses = {"api.example.com": {"ok": True}}
        body = await _client.http_get_json("https://api.example.com/v1/x", timeout=30.0)
        assert body == {"ok": True}
