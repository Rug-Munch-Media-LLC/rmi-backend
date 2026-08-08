"""Tests for scanner stability persistence (score-drift elimination, 2026-08-07).

Complements ``test_unified_scanner_stability.py`` (scanner-level cache fast
path) by exercising the persistence layer directly:

  - ``persistence._record_stability`` — append risk levels, cap the window
  - ``persistence.read_stability`` — agreement summary / graceful None
  - ``persistence._stability_key`` — key format
  - ``persist_result`` — writes the 1h Redis cache and records stability
    exactly once per completed scan (dedup via ``_stability_recorded``)

Redis is a FakeRedis stand-in; Postgres is a no-op AsyncMock.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified import persistence


class FakeRedis:
    """Minimal in-memory async Redis stand-in (get/setex only)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value
        self.setex_calls.append((key, ttl, value))

    def seed(self, key: str, value: str) -> None:
        self._data[key] = value


def _patch_redis(fake: FakeRedis):
    """``get_redis_async`` is a sync function returning an async client."""
    return patch("app.core.redis.get_redis_async", new=Mock(return_value=fake))


def _result(**overrides: object) -> ScanResult:
    defaults: dict[str, object] = {
        "token_address": "0xSTABILITY",
        "chain": "ethereum",
        "symbol": "TST",
        "name": "Stability Test",
        "safety_score": 60.0,
        "risk_level": "medium",
        "risk_flags": [],
        "green_flags": [],
        "modules_run": ["token_price"],
        "background_status": "complete",
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════
# _record_stability
# ═══════════════════════════════════════════════


class TestRecordStability:
    @pytest.mark.asyncio
    async def test_appends_risk_level_and_sets_ttl(self) -> None:
        fake = FakeRedis()
        with _patch_redis(fake):
            await persistence._record_stability(fake, "ethereum", "0xABC", "low")

        raw = fake._data.get(persistence._stability_key("ethereum", "0xABC"))
        assert json.loads(raw) == ["low"]
        assert fake.setex_calls[0][1] == persistence.STABILITY_TTL

    @pytest.mark.asyncio
    async def test_appends_to_existing_history(self) -> None:
        fake = FakeRedis()
        fake.seed(persistence._stability_key("ethereum", "0xABC"), json.dumps(["low", "low"]))
        with _patch_redis(fake):
            await persistence._record_stability(fake, "ethereum", "0xABC", "medium")

        assert json.loads(fake._data[persistence._stability_key("ethereum", "0xABC")]) == ["low", "low", "medium"]

    @pytest.mark.asyncio
    async def test_window_capped_at_max_scans(self) -> None:
        fake = FakeRedis()
        fake.seed(
            persistence._stability_key("ethereum", "0xABC"), json.dumps(["low"] * persistence.STABILITY_MAX_SCANS)
        )
        with _patch_redis(fake):
            await persistence._record_stability(fake, "ethereum", "0xABC", "high")

        levels = json.loads(fake._data[persistence._stability_key("ethereum", "0xABC")])
        assert len(levels) == persistence.STABILITY_MAX_SCANS
        assert levels[-1] == "high"  # newest kept, oldest dropped

    @pytest.mark.asyncio
    async def test_empty_risk_level_is_skipped(self) -> None:
        fake = FakeRedis()
        with _patch_redis(fake):
            await persistence._record_stability(fake, "ethereum", "0xABC", "")

        assert persistence._stability_key("ethereum", "0xSTABILITY") not in fake._data

    @pytest.mark.asyncio
    async def test_garbage_history_does_not_crash(self) -> None:
        """Corrupt JSON in the window is handled gracefully (write skipped)."""
        fake = FakeRedis()
        fake.seed(persistence._stability_key("ethereum", "0xABC"), "{not-json")
        with _patch_redis(fake):
            # Must not raise; production logs and skips the write.
            await persistence._record_stability(fake, "ethereum", "0xABC", "low")

        # The corrupt value is left untouched — no exception escaped.
        assert fake._data[persistence._stability_key("ethereum", "0xABC")] == "{not-json"


# ═══════════════════════════════════════════════
# read_stability
# ═══════════════════════════════════════════════


class TestReadStability:
    @pytest.mark.asyncio
    async def test_returns_agreement_summary(self) -> None:
        fake = FakeRedis()
        fake.seed(persistence._stability_key("ethereum", "0xABC"), json.dumps(["low", "low", "medium", "low"]))
        with _patch_redis(fake):
            summary = await persistence.read_stability("ethereum", "0xABC")

        assert summary == {"scans": 4, "agreeing": 3, "level": "low", "agree_pct": 75}

    @pytest.mark.asyncio
    async def test_no_history_returns_none(self) -> None:
        fake = FakeRedis()
        with _patch_redis(fake):
            assert await persistence.read_stability("ethereum", "0xABC") is None

    @pytest.mark.asyncio
    async def test_garbage_json_returns_none(self) -> None:
        fake = FakeRedis()
        fake.seed(persistence._stability_key("ethereum", "0xABC"), "not-json")
        with _patch_redis(fake):
            assert await persistence.read_stability("ethereum", "0xABC") is None

    @pytest.mark.asyncio
    async def test_non_list_history_returns_none(self) -> None:
        fake = FakeRedis()
        fake.seed(persistence._stability_key("ethereum", "0xABC"), json.dumps({"level": "low"}))
        with _patch_redis(fake):
            assert await persistence.read_stability("ethereum", "0xABC") is None

    @pytest.mark.asyncio
    async def test_empty_list_returns_none(self) -> None:
        fake = FakeRedis()
        fake.seed(persistence._stability_key("ethereum", "0xABC"), json.dumps([]))
        with _patch_redis(fake):
            assert await persistence.read_stability("ethereum", "0xABC") is None

    @pytest.mark.asyncio
    async def test_no_redis_returns_none(self) -> None:
        with patch("app.core.redis.get_redis_async", new=Mock(return_value=None)):
            assert await persistence.read_stability("ethereum", "0xABC") is None


# ═══════════════════════════════════════════════
# _stability_key
# ═══════════════════════════════════════════════


class TestStabilityKey:
    def test_key_format(self) -> None:
        assert persistence._stability_key("ethereum", "0xABC") == "scanner:stability:ethereum:0xABC"


# ═══════════════════════════════════════════════
# persist_result — cache write + stability dedup
# ═══════════════════════════════════════════════


class _FakeResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeSession:
    """Minimal async-context SQLAlchemy session stand-in.

    ``persist_result`` uses ``async with session:`` without an ``as`` target,
    so the context manager object itself must expose ``add``/``commit``.
    """

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: object) -> _FakeResult:
        return _FakeResult()

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class TestPersistResult:
    @pytest.mark.asyncio
    async def test_completed_scan_writes_cache_and_stability_once(self) -> None:
        """A completed scan persists the legacy dict AND records stability,
        and the dedup flag prevents double-counting on a second persist."""
        fake = FakeRedis()
        result = _result(risk_level="medium")
        with (
            _patch_redis(fake),
            patch(
                "app.domains.scanner.unified.persistence._get_async_session",
                new=AsyncMock(return_value=_FakeSession()),
            ),
            patch("app.domains.scanner.unified._legacy._to_legacy_dict", return_value={"legacy": "dict"}),
        ):
            await persistence.persist_result(result)
            await persistence.persist_result(result)

        cache_calls = [c for c in fake.setex_calls if c[0] == "sentinelscan:ethereum:0xSTABILITY"]
        assert len(cache_calls) == 2  # cache written on both persists
        stability_raw = fake._data.get(persistence._stability_key("ethereum", "0xSTABILITY"))
        # Stability recorded exactly once thanks to the _stability_recorded flag.
        assert json.loads(stability_raw) == ["medium"]

    @pytest.mark.asyncio
    async def test_running_scan_skips_stability_recording(self) -> None:
        """In-flight (background_status=running) scans don't pollute history."""
        fake = FakeRedis()
        result = _result(risk_level="medium", background_status="running")
        with (
            _patch_redis(fake),
            patch(
                "app.domains.scanner.unified.persistence._get_async_session",
                new=AsyncMock(return_value=_FakeSession()),
            ),
            patch("app.domains.scanner.unified._legacy._to_legacy_dict", return_value={"legacy": "dict"}),
        ):
            await persistence.persist_result(result)

        assert persistence._stability_key("ethereum", "0xSTABILITY") not in fake._data

    @pytest.mark.asyncio
    async def test_persist_result_never_raises_on_redis_failure(self) -> None:
        """Redis outages in the cache/stability path are swallowed."""
        fake = FakeRedis()

        async def _boom_get(key: str) -> None:
            raise ConnectionError("redis down")

        fake.get = _boom_get
        result = _result()
        with (
            _patch_redis(fake),
            patch(
                "app.domains.scanner.unified.persistence._get_async_session",
                new=AsyncMock(return_value=_FakeSession()),
            ),
            patch("app.domains.scanner.unified._legacy._to_legacy_dict", return_value={"legacy": "dict"}),
        ):
            # Should complete without raising — cache/stability are best-effort.
            await persistence.persist_result(result)
