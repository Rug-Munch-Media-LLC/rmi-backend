"""Tests for app/circuit_breaker.py - Redis-backed circuit breaker for external API calls.

Tests the core circuit breaker logic: check_circuit, record_success, record_failure.
Mocks Redis to avoid external dependency.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.circuit_breaker import check_circuit, record_failure, record_success


class TestCheckCircuit:
    """Tests for check_circuit - determines if circuit is open/closed."""

    @patch("app.circuit_breaker._get_redis")
    def test_returns_false_when_redis_unavailable(self, mock_get_redis):
        """When Redis is down, circuit should fail open (return False = allow call)."""
        mock_get_redis.side_effect = Exception("Redis down")
        result = check_circuit("test_service")
        assert result is False

    @patch("app.circuit_breaker._get_redis")
    def test_returns_false_when_closed(self, mock_get_redis):
        """CLOSED state means the circuit is OK - return False (don't block)."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "CLOSED"
        mock_get_redis.return_value = mock_redis

        result = check_circuit("healthy_service")

        assert result is False
        mock_redis.get.assert_any_call("circuit:healthy_service:state")

    @patch("app.circuit_breaker._get_redis")
    def test_returns_true_when_open(self, mock_get_redis):
        """OPEN state means the circuit is tripped - return True (block call)."""
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: {
            "circuit:open_service:state": "OPEN",
            "circuit:open_service:opened_at": str(time.time() - 10),  # 10s ago
        }.get(k)
        mock_get_redis.return_value = mock_redis

        result = check_circuit("open_service")

        assert result is True

    @patch("app.circuit_breaker._get_redis")
    def test_transitions_to_half_open_after_cooldown(self, mock_get_redis):
        """After 60s cooldown, OPEN should transition to HALF_OPEN."""
        mock_redis = MagicMock()
        far_past = time.time() - 120  # 120s ago - past the 60s cooldown
        mock_redis.get.side_effect = lambda k: {
            "circuit:cooldown_service:state": "OPEN",
            "circuit:cooldown_service:opened_at": str(far_past),
        }.get(k)
        mock_get_redis.return_value = mock_redis

        result = check_circuit("cooldown_service")

        # Should be False (allow through) and set HALF_OPEN
        assert result is False
        mock_redis.set.assert_called_with("circuit:cooldown_service:state", "HALF_OPEN")

    @patch("app.circuit_breaker._get_redis")
    def test_handles_half_open_state(self, mock_get_redis):
        """HALF_OPEN state should return False (allow a probe call)."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "HALF_OPEN"
        mock_get_redis.return_value = mock_redis

        result = check_circuit("probing_service")

        assert result is False


class TestRecordFailure:
    """Tests for record_failure - tracks failures and trips circuit."""

    @patch("app.circuit_breaker._get_redis")
    def test_increments_failure_count(self, mock_get_redis):
        """record_failure should increment the failure counter."""
        mock_redis = MagicMock()
        mock_redis.incr.return_value = 1
        mock_get_redis.return_value = mock_redis

        record_failure("test_service")

        mock_redis.incr.assert_called_with("circuit:test_service:failures")

    @patch("app.circuit_breaker._get_redis")
    def test_trips_circuit_at_5_failures(self, mock_get_redis):
        """After 5 failures, the circuit should be set to OPEN."""
        mock_redis = MagicMock()
        mock_redis.incr.return_value = 5
        mock_get_redis.return_value = mock_redis

        with patch("app.circuit_breaker.time") as mock_time:
            mock_time.time.return_value = 12345.0
            record_failure("trippy_service")

        mock_redis.set.assert_any_call("circuit:trippy_service:state", "OPEN")
        mock_redis.set.assert_any_call("circuit:trippy_service:opened_at", "12345.0")

    @patch("app.circuit_breaker._get_redis")
    def test_does_not_trip_before_5_failures(self, mock_get_redis):
        """With <5 failures, the circuit should remain CLOSED."""
        mock_redis = MagicMock()
        mock_redis.incr.return_value = 3
        mock_get_redis.return_value = mock_redis

        record_failure("stable_service")

        # Should NOT set state to OPEN
        calls = [c for c in mock_redis.set.call_args_list if c[0][0].endswith(":state")]
        assert len(calls) == 0

    @patch("app.circuit_breaker._get_redis")
    def test_handles_redis_error_gracefully(self, mock_get_redis):
        """Should not raise when Redis is unavailable."""
        mock_get_redis.side_effect = Exception("Redis down")
        # Should not raise
        record_failure("down_service")


class TestRecordSuccess:
    """Tests for record_success - resets circuit state."""

    @patch("app.circuit_breaker._get_redis")
    def test_closes_circuit_on_success(self, mock_get_redis):
        """record_success should set state back to CLOSED and clear failures."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        record_success("recovered_service")

        mock_redis.set.assert_called_with("circuit:recovered_service:state", "CLOSED")
        mock_redis.delete.assert_called_with("circuit:recovered_service:failures")

    @patch("app.circuit_breaker._get_redis")
    def test_handles_redis_error_on_success(self, mock_get_redis):
        """Should not raise when Redis is unavailable during success recording."""
        mock_get_redis.side_effect = Exception("Redis down")
        record_success("down_service")


class TestGetRedis:
    """Tests for the internal _get_redis helper."""

    def test_creates_redis_client_with_env_config(self):
        """_get_redis should use env vars for connection."""
        from app.circuit_breaker import _get_redis

        with patch.dict("os.environ", {"REDIS_HOST": "myredis", "REDIS_PORT": "6380"}):
            with patch("app.circuit_breaker.redis.Redis") as mock_redis_class:
                _get_redis()

                mock_redis_class.assert_called_once()
                _, kwargs = mock_redis_class.call_args
                assert kwargs["host"] == "myredis"
                assert kwargs["port"] == 6380
