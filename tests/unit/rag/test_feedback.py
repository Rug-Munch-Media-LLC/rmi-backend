"""Unit tests for app/rag/feedback.py — RAG feedback loop (Redis-backed).

Covers record/get/counts/recent feedback with get_redis monkeypatched
(no real Redis). Also covers the invalid-query-id guard, degraded mode
(redis None), and exception paths.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import app.rag.feedback as feedback_mod
from app.rag.feedback import (
    FEEDBACK_COUNTS_KEY,
    FEEDBACK_PREFIX,
    get_feedback,
    get_feedback_counts,
    get_recent_feedback,
    record_feedback,
    record_implicit_helpful,
    record_implicit_unhelpful,
)


class TestRecordFeedback:
    @pytest.mark.asyncio
    async def test_invalid_query_id_returns_error(self) -> None:
        result = await record_feedback("", True)
        assert result["status"] == "error"
        assert result["recorded"] is False

        result = await record_feedback(None, True)  # type: ignore[arg-type]
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_degraded(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        result = await record_feedback("q1", True)
        assert result["status"] == "degraded"
        assert result["recorded"] is False

    @pytest.mark.asyncio
    async def test_successful_helpful_record(self, monkeypatch) -> None:
        fake = MagicMock()
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        result = await record_feedback("q1", True, source="explicit_rating", metadata={"token": "0xabc"})
        assert result["status"] == "ok"
        assert result["recorded"] is True
        assert result["query_id"] == "q1"
        # Verify Redis interactions
        fake.setex.assert_called_once()
        key = fake.setex.call_args[0][0]
        assert key == f"{FEEDBACK_PREFIX}q1"
        stored = json.loads(fake.setex.call_args[0][2])
        assert stored["was_helpful"] is True
        assert stored["source"] == "explicit_rating"
        assert stored["metadata"] == {"token": "0xabc"}
        fake.hincrby.assert_any_call(FEEDBACK_COUNTS_KEY, "total", 1)
        fake.hincrby.assert_any_call(FEEDBACK_COUNTS_KEY, "helpful", 1)

    @pytest.mark.asyncio
    async def test_unhelpful_increments_unhelpful(self, monkeypatch) -> None:
        fake = MagicMock()
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        await record_feedback("q2", False)
        fake.hincrby.assert_any_call(FEEDBACK_COUNTS_KEY, "unhelpful", 1)

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.setex.side_effect = ConnectionError("redis down")
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        result = await record_feedback("q3", True)
        assert result["status"] == "error"
        assert result["recorded"] is False


class TestGetFeedback:
    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        assert await get_feedback("q1") is None

    @pytest.mark.asyncio
    async def test_missing_record_returns_none(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get.return_value = None
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        assert await get_feedback("q1") is None

    @pytest.mark.asyncio
    async def test_returns_parsed_record(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get.return_value = json.dumps({"query_id": "q1", "was_helpful": True})
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        record = await get_feedback("q1")
        assert record == {"query_id": "q1", "was_helpful": True}
        fake.get.assert_called_once_with(f"{FEEDBACK_PREFIX}q1")


class TestGetFeedbackCounts:
    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_zeros(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        counts = await get_feedback_counts()
        assert counts == {"total": 0, "helpful": 0, "unhelpful": 0, "helpfulness_ratio": 0.0}

    @pytest.mark.asyncio
    async def test_counts_and_ratio(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.hgetall.return_value = {b"total": b"10", b"helpful": b"7", b"unhelpful": b"3"}
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        counts = await get_feedback_counts()
        assert counts["total"] == 10
        assert counts["helpful"] == 7
        assert counts["unhelpful"] == 3
        assert counts["helpfulness_ratio"] == 0.7

    @pytest.mark.asyncio
    async def test_bytes_and_str_keys_both_supported(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.hgetall.return_value = {"total": "5", "helpful": "2", "unhelpful": "3"}
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        counts = await get_feedback_counts()
        assert counts["total"] == 5
        assert counts["helpfulness_ratio"] == 0.4


class TestGetRecentFeedback:
    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        assert await get_recent_feedback() == []

    @pytest.mark.asyncio
    async def test_scans_and_sorts_by_ts(self, monkeypatch) -> None:
        fake = MagicMock()
        # First scan returns cursor 0 with two keys
        fake.scan.side_effect = [(0, [b"rag:feedback:q1", b"rag:feedback:q2"])]
        fake.get.side_effect = [
            json.dumps({"query_id": "q1", "ts": 100.0}),
            json.dumps({"query_id": "q2", "ts": 200.0}),
        ]
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        records = await get_recent_feedback(limit=10)
        assert [r["query_id"] for r in records] == ["q2", "q1"]

    @pytest.mark.asyncio
    async def test_bad_json_skipped(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.scan.side_effect = [(0, [b"rag:feedback:q1"])]
        fake.get.side_effect = ["{not json"]
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        assert await get_recent_feedback() == []

    @pytest.mark.asyncio
    async def test_limit_respected(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.scan.side_effect = [
            (0, [f"rag:feedback:q{i}".encode() for i in range(5)]),
        ]
        fake.get.side_effect = [json.dumps({"query_id": f"q{i}", "ts": float(i)}) for i in range(5)]
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        records = await get_recent_feedback(limit=2)
        assert len(records) == 2


class TestImplicitHelpers:
    @pytest.mark.asyncio
    async def test_implicit_helpful(self, monkeypatch) -> None:
        fake = MagicMock()
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        result = await record_implicit_helpful("q1")
        assert result["status"] == "ok"
        stored = json.loads(fake.setex.call_args[0][2])
        assert stored["was_helpful"] is True
        assert stored["source"] == "implicit_click"

    @pytest.mark.asyncio
    async def test_implicit_unhelpful(self, monkeypatch) -> None:
        fake = MagicMock()
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        result = await record_implicit_unhelpful("q1")
        assert result["status"] == "ok"
        stored = json.loads(fake.setex.call_args[0][2])
        assert stored["was_helpful"] is False
        assert stored["source"] == "implicit_dismiss"
