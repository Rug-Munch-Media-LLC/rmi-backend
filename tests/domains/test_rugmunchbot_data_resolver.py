"""Tests for the own-data-first data resolver (rugmunchbot data_resolver).

Verifies the resolve_with_fallback contract: own-data primary is tried first,
external fallback runs only on failure/timeout/invalid payload, and
DATA_SOURCE usage is tracked for reliance reporting. No network calls are
made — all primary/fallback functions are fakes.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domains.telegram.rugmunchbot.data_resolver import (
    get_source_stats,
    resolve_with_fallback,
)


class TestResolveWithFallback:
    @pytest.mark.asyncio
    async def test_primary_wins_when_own_data_available(self) -> None:
        calls: list[str] = []

        async def primary() -> dict:
            calls.append("primary")
            return {"price_usd": 1.23}

        async def fallback() -> dict:
            calls.append("fallback")
            return {"price_usd": 9.99}

        result = await resolve_with_fallback("t_test", primary, fallback)

        assert result == {"price_usd": 1.23}
        assert calls == ["primary"]  # fallback never called

    @pytest.mark.asyncio
    async def test_fallback_on_primary_exception(self) -> None:
        calls: list[str] = []

        async def primary() -> dict:
            calls.append("primary")
            raise ConnectionError("backend down")

        async def fallback() -> dict:
            calls.append("fallback")
            return {"price_usd": 9.99}

        result = await resolve_with_fallback("t_error", primary, fallback)

        assert result == {"price_usd": 9.99}
        assert calls == ["primary", "fallback"]

    @pytest.mark.asyncio
    async def test_fallback_on_primary_timeout(self) -> None:
        calls: list[str] = []

        async def primary() -> dict:
            calls.append("primary")
            await asyncio.sleep(0.5)
            return {"price_usd": 1.23}

        async def fallback() -> dict:
            calls.append("fallback")
            return {"price_usd": 9.99}

        result = await resolve_with_fallback("t_timeout", primary, fallback, timeout_secs=0.05)

        assert result == {"price_usd": 9.99}
        assert calls == ["primary", "fallback"]

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_payload(self) -> None:
        calls: list[str] = []

        async def primary() -> dict:
            calls.append("primary")
            return {"price_usd": 0}  # invalid: price is 0

        async def fallback() -> dict:
            calls.append("fallback")
            return {"price_usd": 9.99}

        result = await resolve_with_fallback(
            "t_invalid",
            primary,
            fallback,
            is_valid=lambda d: bool(d.get("price_usd")),
        )

        assert result == {"price_usd": 9.99}
        assert calls == ["primary", "fallback"]

    @pytest.mark.asyncio
    async def test_raises_when_both_sources_fail(self) -> None:
        async def primary() -> dict:
            raise ConnectionError("own down")

        async def fallback() -> dict:
            raise TimeoutError("external down")

        with pytest.raises(TimeoutError):
            await resolve_with_fallback("t_both", primary, fallback)

    @pytest.mark.asyncio
    async def test_source_stats_track_reliance(self) -> None:
        async def ok() -> dict:
            return {"value": 1}

        async def bad() -> dict:
            raise ConnectionError("down")

        await resolve_with_fallback("t_stats", ok, bad)
        await resolve_with_fallback("t_stats", bad, ok)

        stats = get_source_stats()
        assert stats["t_stats"]["own_data"] == 1
        assert stats["t_stats"]["fallback"] == 1
