"""Tests for the adminbot engine registry (P4).

Uses dependency injection (fake Redis, echo/sleep binaries) — no
monkeypatching anywhere, per fleet standards.
"""

from __future__ import annotations

import asyncio

from app.domains.telegram.adminbot import engines


class _FakeRedis:
    """Minimal async Redis stub — plain DI, no patching."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store = dict(initial or {})
        self.closed = False

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def aclose(self) -> None:
        self.closed = True


def test_registry_ids_match_keys() -> None:
    for key, spec in engines.ENGINES.items():
        assert spec.id == key


def test_default_engine_is_registered() -> None:
    assert engines.DEFAULT_ENGINE in engines.ENGINES


def test_available_ids_includes_http_engines() -> None:
    ids = engines.available_ids()
    assert "defiant" in ids
    assert "ff711" in ids


def test_truncate_short_and_long() -> None:
    assert engines.truncate("hello") == "hello"
    long_output = "x" * (engines.MAX_OUTPUT_CHARS + 50)
    out = engines.truncate(long_output)
    assert out.endswith("(truncated)")
    assert len(out) < len(long_output)


def test_state_roundtrip_with_fake_redis() -> None:
    fake = _FakeRedis()
    assert asyncio.run(engines.set_current("hermes", redis_client=fake)) is True
    assert asyncio.run(engines.get_current(redis_client=fake)) == "hermes"


def test_set_current_rejects_unknown() -> None:
    fake = _FakeRedis()
    assert asyncio.run(engines.set_current("nope", redis_client=fake)) is False


def test_get_current_defaults_when_unset() -> None:
    fake = _FakeRedis()
    assert asyncio.run(engines.get_current(redis_client=fake)) == engines.DEFAULT_ENGINE


def test_get_current_rejects_garbage() -> None:
    fake = _FakeRedis({engines.ENGINE_STATE_KEY: "junk"})
    assert asyncio.run(engines.get_current(redis_client=fake)) == engines.DEFAULT_ENGINE


def test_run_engine_unknown_id() -> None:
    result = asyncio.run(engines.run_engine("nope", "hi"))
    assert result["ok"] is False


def test_run_cli_strips_ansi() -> None:
    spec = engines.EngineSpec("t", "t", "cli", command=("echo",))
    ok, out = asyncio.run(engines._run_cli(spec, "\x1b[31mRED\x1b[0m ok", timeout=10))
    assert ok is True
    assert "RED ok" in out


def test_run_cli_timeout() -> None:
    spec = engines.EngineSpec("t", "t", "cli", command=("sleep",))
    ok, out = asyncio.run(engines._run_cli(spec, "5", timeout=1))
    assert ok is False
    assert "timed out" in out
