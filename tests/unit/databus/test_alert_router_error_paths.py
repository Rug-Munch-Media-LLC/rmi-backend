"""Error-path tests for the webhook alert router (``app/databus/alert_router.py``).

The router's contract is **fail-open**: the inbound webhook handler must
always get a decision dict back — never a 500 — even when the payload is
malformed, the event type is unknown, the event is a duplicate, the wallet
is throttled, or Redis itself is down. Complements
``tests/unit/databus/test_alert_router.py`` with the hostile-payload cases.
All offline: Redis, labels and the alert pipeline are mocked.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

import pytest

from app.databus.alert_router import (
    _assess_importance,
    _classify_event,
    _helius_amount_usd,
    _helius_token_summary,
    _throttle_wallet,
    parse_helius_event,
    route_webhook_alert,
)

# ── Parsing helpers with hostile inputs ────────────────────────────────────


class TestAmountParsing:
    def test_garbage_value_usd_falls_back_to_zero(self) -> None:
        """A non-numeric valueUsd degrades to 0.0 (fail-open), never raises."""
        assert _helius_amount_usd({"valueUsd": "not-a-number"}) == 0.0
        assert _helius_amount_usd({"valueUsd": None}) == 0.0

    def test_empty_payload_amount_is_zero(self) -> None:
        assert _helius_amount_usd({}) == 0.0

    def test_bad_native_transfer_rows_are_skipped(self) -> None:
        """Hostile nativeTransfers rows (non-dict / non-numeric / null
        amounts) must not crash the amount estimator — bad rows are skipped
        and the valid row still counts."""
        payload = {
            "nativeTransfers": [
                "bad",  # non-dict row — skipped in-place
                None,  # null row — skipped in-place
                {"amount": "not-int"},
                {"amount": None},
                {"amount": 2_000_000_000},
            ]
        }
        # 2 SOL at SOL_PRICE_USD — the valid row still counts.
        assert _helius_amount_usd(payload) == pytest.approx(300.0)

    def test_non_dict_token_rows_fail_open_to_empty_summary(self) -> None:
        """Hostile tokenTransfers rows (non-dict rows / non-dict tokenAmount /
        garbage uiAmount) yield an empty summary — never raises."""
        payload = {
            "tokenTransfers": [
                "bad",
                None,
                {"tokenAmount": {"uiAmount": "x"}},
                {"tokenAmount": None},
                {"tokenAmount": "garbage"},
            ]
        }
        assert _helius_token_summary(payload) == {}

    def test_assess_importance_garbage_amounts_never_raises(self) -> None:
        """Garbage amount fields fail open to 0 — importance stays bounded."""
        assert 0.0 <= _assess_importance({"amount_usd": "x", "token": {"ui_amount": "y"}}, "") <= 1.0

    def test_assess_importance_non_dict_parsed_never_raises(self) -> None:
        assert _assess_importance(None, "") == 0.0
        assert _assess_importance(["not", "a", "dict"], "") == 0.0


class TestClassification:
    def test_unknown_event_type_classifies_unknown(self) -> None:
        assert _classify_event({"type": "BOGUS"}, "", {}) == "unknown"

    def test_unknown_event_type_without_signal(self) -> None:
        assert _classify_event({"type": "TRANSFER"}, "", {}) == "transfer"

    def test_assess_importance_empty_parsed_never_raises(self) -> None:
        assert 0.0 <= _assess_importance({}, "") <= 1.0


# ── parse_helius_event: payloads with no usable signal ─────────────────────


class TestParseHeliusErrors:
    def test_no_wallet_returns_none(self) -> None:
        assert parse_helius_event({"type": "TRANSFER"}) is None
        assert parse_helius_event({}) is None

    def test_garbage_description_is_stringified(self) -> None:
        """A non-str description (e.g. an int from a proxy layer) is coerced
        to str — never crashes the parse."""
        parsed = parse_helius_event({"feePayer": "ABC", "description": 123, "type": "TRANSFER"})
        assert parsed is not None
        assert parsed["description"] == "123"


# ── route_webhook_alert: the fail-open decision contract ───────────────────


class TestRouteWebhookErrorPaths:
    @pytest.mark.asyncio
    async def test_none_payload_returns_decision_never_500(self) -> None:
        """A None payload (bad JSON body) yields a skip decision dict."""
        with patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push:
            result = await route_webhook_alert("helius", None, "evt:null")
        assert result["decision"] in ("skip", "unparseable")
        assert "event_type" in result
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_native_transfers_fail_open(self) -> None:
        """Non-dict nativeTransfers rows must not 500 the webhook handler."""
        payload = {"feePayer": "ABC", "nativeTransfers": ["bad", None], "type": "TRANSFER"}
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:garbage-native")
        assert result["decision"] == "skip"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_garbage_token_amount_fail_open(self) -> None:
        """A tokenAmount with a non-numeric uiAmount must not 500."""
        payload = {"feePayer": "ABC", "type": "SWAP", "tokenTransfers": [{"tokenAmount": {"uiAmount": "x"}}]}
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:garbage-amount")
        assert result["decision"] == "skip"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_event_type_skipped(self) -> None:
        """Unknown event type + small amount → skip, no alert pushed."""
        payload = {"feePayer": "ABC", "type": "BOGUS", "description": "weird"}
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:unknown")
        assert result["decision"] == "skip"
        assert result["event_type"] == "unknown"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_event_returns_decision_dict(self) -> None:
        """Replay/duplicate events are dropped with a decision dict."""
        payload = {"feePayer": "ABC", "type": "TRANSFER"}
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="duplicate"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:dup")
        assert result["decision"] == "duplicate"
        assert result["throttled_wallet"] == "ABC"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_throttled_event_returns_decision_dict(self) -> None:
        """Cooldown-throttled wallets are dropped with a decision dict."""
        payload = {"feePayer": "ABC", "nativeTransfers": [{"amount": 2_000_000_000}], "type": "TRANSFER"}
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="throttled"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:throttled")
        assert result["decision"] == "throttled"
        push.assert_not_awaited()


# ── Throttle: Redis down fails open ────────────────────────────────────────


class TestThrottleRedisDown:
    def test_redis_connection_error_fails_open_to_ok(self) -> None:
        """A dead Redis must never raise — throttle returns "ok" (dedupe
        happens elsewhere) so the webhook handler keeps answering."""

        def _boom() -> None:
            raise ConnectionError("redis down")

        with patch("app.databus.alert_router._redis", side_effect=_boom):
            assert _throttle_wallet("helius", "WALLET", "evt:1") == "ok"

    def test_redis_timeout_fails_open_to_ok(self) -> None:
        def _boom() -> None:
            raise TimeoutError("redis timeout")

        with patch("app.databus.alert_router._redis", side_effect=_boom):
            assert _throttle_wallet("helius", "WALLET", "evt:1") == "ok"
