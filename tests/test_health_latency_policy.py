"""Latency honesty, timeout reporting and DSN composition for /health."""

from __future__ import annotations

import asyncio

from app.core.health_dsn import postgres_dsn
from app.core.health_latency import DEGRADED_MS, UNHEALTHY_MS, classify_latency


def test_five_second_store_is_not_healthy() -> None:
    out = classify_latency({"healthy": True}, UNHEALTHY_MS)
    assert out["healthy"] is False
    assert out["latency_state"] == "unhealthy"
    assert str(UNHEALTHY_MS) in out["error"]


def test_degraded_band_is_flagged_not_hidden() -> None:
    out = classify_latency({"healthy": True}, DEGRADED_MS)
    assert out["latency_state"] == "degraded"
    assert out["healthy"] is True


def test_fast_store_is_ok() -> None:
    assert classify_latency({"healthy": True}, 3) == {
        "healthy": True,
        "latency_ms": 3,
        "latency_state": "ok",
    }


def test_timeout_is_never_silent(monkeypatch) -> None:
    from app.core import health_stores

    monkeypatch.setattr(health_stores, "STORE_TIMEOUT_S", 0.05)

    async def hang() -> dict[str, object]:
        await asyncio.sleep(10)
        return {"healthy": True}

    out = asyncio.run(health_stores._check_store("redis", hang()))
    assert out["healthy"] is False
    assert out["error"]
    assert out["latency_state"] == "ok"


def test_dsn_uses_env_password_not_redaction(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "rmi")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("POSTGRES_DB", "rmi")
    dsn = postgres_dsn("rmi-postgres")
    assert "***" not in dsn
    assert "p%40ss%2Fword@rmi-postgres:5432/rmi" in dsn
