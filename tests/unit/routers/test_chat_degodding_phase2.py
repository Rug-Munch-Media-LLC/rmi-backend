"""Characterization tests for the phase-2 de-godding of ``app.routers.chat``.

Pins the observable behavior of the pieces extracted from
``app/routers/chat/__init__.py`` into focused submodules
(``_chat_models`` / ``_response_cache`` / ``_persistence`` — plus
``market_fetch``, which moved DOWN to ``app.domains.chat_tools.market_fetch``
for the import-boundary contract and is re-exported from the chat package):

- every name stays importable from ``app.routers.chat`` (shim contract —
  same pattern as phase 1, enforced for phase-1 names by
  ``tests/test_chat_degodging.py``);
- extracted state (the response cache dict) is the SAME object through both
  import paths, so tests clearing ``chat_mod._chat_cache`` keep working;
- behavior: ChatRequest token-budget default, cache TTL roundtrip + eviction,
  ``_save_message`` persistence contract, ``_fetch_market_context``
  fail-open contract.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from typing import Any

import httpx
import pytest

import app.routers.chat as chat_mod
from app.routers.chat import (
    CHAT_MAX_TOKENS_DEFAULT,
    ChatRequest,
    StreamRequest,
    _cache_get,
    _cache_set,
    _chat_cache,
    _fetch_market_context,
    _save_message,
)

# ── Shim contract: names remain on the package root ──────────────────


def test_phase2_surface_intact() -> None:
    for name in (
        "ChatRequest",
        "StreamRequest",
        "CHAT_MAX_TOKENS_DEFAULT",
        "_resolve_chat_max_tokens_default",
        "_CHAT_MAX_TOKENS_BASE",
        "_CHAT_MAX_TOKENS_MIN",
        "_CHAT_MAX_TOKENS_MAX",
        "_chat_cache",
        "_cache_get",
        "_cache_set",
        "_save_message",
        "_fetch_market_context",
        "_get_client",
        "COINGECKO_API",
    ):
        assert hasattr(chat_mod, name), f"app.routers.chat lost '{name}'"


def test_cache_dict_identity_across_import_paths() -> None:
    """Tests clear ``chat_mod._chat_cache`` directly — the object must be
    identical no matter which module path it is imported from."""
    defining_module = sys.modules[_cache_get.__module__]
    assert defining_module._chat_cache is _chat_cache
    assert chat_mod._chat_cache is _chat_cache


# ── ChatRequest token budget ──────────────────────────────────────────


def test_chat_request_defaults() -> None:
    req = ChatRequest(message="hello")
    assert req.max_tokens == 320
    assert req.tier == "T2"
    assert req.temperature == 0.7
    assert req.model is None
    assert req.history is None
    assert req.context_override is None
    assert CHAT_MAX_TOKENS_DEFAULT == 320


def test_chat_request_bounds() -> None:
    with pytest.raises(ValueError, match="at least 1 character"):
        ChatRequest(message="")
    with pytest.raises(ValueError, match="at most 2000 characters"):
        ChatRequest(message="x" * 2001)
    with pytest.raises(ValueError, match="greater than or equal to 64"):
        ChatRequest(message="hi", max_tokens=32)  # below ge=64
    with pytest.raises(ValueError, match="String should match pattern"):
        ChatRequest(message="hi", tier="T9")
    assert StreamRequest(message="hi").max_tokens == 512


def test_resolve_max_tokens_env_override(monkeypatch) -> None:
    resolve = chat_mod._resolve_chat_max_tokens_default
    assert resolve("1024") == 1024
    assert resolve("16") == chat_mod._CHAT_MAX_TOKENS_MIN  # clamped low
    assert resolve("999999") == chat_mod._CHAT_MAX_TOKENS_MAX  # clamped high
    assert resolve("garbage") == chat_mod._CHAT_MAX_TOKENS_BASE
    assert resolve(None) == chat_mod._CHAT_MAX_TOKENS_BASE  # no env set here
    monkeypatch.setenv("RMI_CHAT_MAX_TOKENS", "900")
    assert resolve() == 900


# ── Response cache: TTL roundtrip + eviction ──────────────────────────


def test_cache_roundtrip_and_expiry() -> None:
    _chat_cache.clear()
    assert _cache_get("k1") is None
    _cache_set("k1", "answer one", ttl=60)
    assert _cache_get("k1") == "answer one"
    # Force expiry by rewriting the stored timestamp.
    _chat_cache["k1"] = (time.monotonic() - 1, "answer one")
    assert _cache_get("k1") is None
    assert "k1" not in _chat_cache  # expired entries are evicted on read


def test_cache_evicts_oldest_beyond_cap() -> None:
    _chat_cache.clear()
    cap = chat_mod._CACHE_MAX_ENTRIES
    for i in range(cap + 3):
        _cache_set(f"k{i}", f"v{i}", ttl=60 + i)
    assert len(_chat_cache) == cap
    # Oldest (earliest expiry) are gone; newest survive.
    assert _cache_get(f"k{cap + 2}") == f"v{cap + 2}"
    _chat_cache.clear()


# ── Persistence: _save_message contract ───────────────────────────────


def _make_db_factory(db_file):
    def _get_db() -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chat_messages ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL,"
            "content TEXT NOT NULL, metadata TEXT DEFAULT '{}',"
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        return conn

    return _get_db


def test_save_message_writes_row(monkeypatch, tmp_path) -> None:
    # Patch on the module that DEFINES _save_message (its globals resolve
    # _get_db at call time) so the characterization holds pre/post split.
    defining = sys.modules[_save_message.__module__]
    monkeypatch.setattr(defining, "_get_db", _make_db_factory(tmp_path / "chat.db"))

    msg_id = _save_message("user-x", "user", "hello world", {"k": "v"})
    assert msg_id.startswith("msg-")
    assert len(msg_id) == len("msg-") + 10

    conn = sqlite3.connect(str(tmp_path / "chat.db"))
    row = conn.execute("SELECT user_id, role, content, metadata FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
    conn.close()
    assert row == ("user-x", "user", "hello world", '{"k": "v"}')


def test_save_message_defaults_metadata_to_empty_object(monkeypatch, tmp_path) -> None:
    defining = sys.modules[_save_message.__module__]
    monkeypatch.setattr(defining, "_get_db", _make_db_factory(tmp_path / "chat2.db"))

    msg_id = _save_message("user-y", "assistant", "hi")
    conn = sqlite3.connect(str(tmp_path / "chat2.db"))
    (metadata,) = conn.execute("SELECT metadata FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
    conn.close()
    assert metadata == "{}"


# ── Market context fetch: fail-open contract ──────────────────────────


@pytest.mark.asyncio
async def test_fetch_market_context_fail_open(monkeypatch) -> None:
    """Any HTTP failure must yield {} (context is an enhancement, not a gate).

    Patched on the module that DEFINES the function so the characterization
    holds before and after the extraction.
    """
    defining = sys.modules[_fetch_market_context.__module__]

    class _BrokenClient:
        async def get(self, *args: Any, **kwargs: Any) -> None:
            raise httpx.ConnectError("boom")

    async def _broken_client() -> _BrokenClient:
        return _BrokenClient()

    monkeypatch.setattr(defining, "_get_client", _broken_client)
    assert await _fetch_market_context() == {}


@pytest.mark.asyncio
async def test_fetch_market_context_shapes_response(monkeypatch) -> None:
    defining = sys.modules[_fetch_market_context.__module__]

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "bitcoin": {"usd": 67000.0, "usd_24h_change": 1.2},
                "ethereum": {"usd": 3500.0, "usd_24h_change": -0.5},
                "solana": {"usd": 150.0, "usd_24h_change": 3.1},
            }

    class _Client:
        async def get(self, *args: Any, **kwargs: Any) -> _Resp:
            return _Resp()

    async def _client() -> _Client:
        return _Client()

    monkeypatch.setattr(defining, "_get_client", _client)
    ctx = await _fetch_market_context()
    assert ctx == {
        "btc_price": 67000.0,
        "btc_change_24h": 1.2,
        "eth_price": 3500.0,
        "eth_change_24h": -0.5,
        "sol_price": 150.0,
        "sol_change_24h": 3.1,
    }
