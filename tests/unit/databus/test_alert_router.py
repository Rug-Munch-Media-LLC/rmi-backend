"""Tests for the intelligent webhook alert router (databus).

Covers Helius payload parsing, Arkham-style wallet label enrichment,
importance calibration, route_news gating, per-wallet throttling and the
fail-open contract. All tests are offline — Redis, labels and the alert
pipeline are mocked/pure.
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
    _format_alert,
    _wallet_label,
    parse_helius_event,
    route_webhook_alert,
)

# ── Fixtures ────────────────────────────────────────────────────────────


def _helius_payload(**overrides: object) -> dict:
    """Realistic Helius enhanced-webhook body (native SOL transfer)."""
    payload: dict = {
        "accountData": [],
        "feePayer": "4NDS2vZPcQRQv3RvMZeX5QK2Ds1gVEZXxHwQqZsQ1Vkk",
        "nativeTransfers": [
            {
                "amount": 2_000_000_000,
                "fromUserAccount": "4NDS2vZPcQRQv3RvMZeX5QK2Ds1gVEZXxHwQqZsQ1Vkk",
                "toUserAccount": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
            }
        ],
        "signature": "5K4t9q2mM7vXr8nZ1pQw3eR6yT0uI2oA4sD6fG8hJ1kL3nM5",
        "timestamp": 1755400000,
        "type": "TRANSFER",
        "description": "Wallet transferred 2 SOL to another wallet",
    }
    payload.update(overrides)
    return payload


def _helius_token_payload(**overrides: object) -> dict:
    """Helius payload with a large token transfer (USDC-like)."""
    payload: dict = {
        "feePayer": "4NDS2vZPcQRQv3RvMZeX5QK2Ds1gVEZXxHwQqZsQ1Vkk",
        "tokenTransfers": [
            {
                "fromUserAccount": "4NDS2vZPcQRQv3RvMZeX5QK2Ds1gVEZXxHwQqZsQ1Vkk",
                "toUserAccount": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
                "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "USDC",
                "tokenAmount": {"amount": "1200000000", "decimals": 6, "uiAmount": 1200.0},
            }
        ],
        "signature": "tok_sig_0001",
        "timestamp": 1755400001,
        "type": "TRANSFERCHECKED",
        "description": "Wallet transferred 1200 USDC to another wallet",
    }
    payload.update(overrides)
    return payload


# ── Parsing ─────────────────────────────────────────────────────────────


class TestParseHelius:
    def test_parses_native_transfer(self) -> None:
        parsed = parse_helius_event(_helius_payload())
        assert parsed is not None
        assert parsed["wallet"] == "4NDS2vZPcQRQv3RvMZeX5QK2Ds1gVEZXxHwQqZsQ1Vkk"
        # 2_000_000_000 lamports = 2 SOL at SOL_PRICE_USD (150)
        assert parsed["amount_usd"] == pytest.approx(300.0)
        assert parsed["event_type"] == "transfer"
        assert parsed["signature"].startswith("5K4t9q2")

    def test_parses_token_transfer(self) -> None:
        parsed = parse_helius_event(_helius_token_payload())
        assert parsed is not None
        assert parsed["token"]["symbol"] == "USDC"
        assert parsed["token"]["ui_amount"] == 1200.0
        assert parsed["event_type"] == "token_transfer"
        assert parsed["counterparty"] == "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

    def test_classifies_mint_freeze(self) -> None:
        parsed = parse_helius_event(
            _helius_token_payload(
                type="MINT",
                description="Mint authority initialized on token",
            )
        )
        assert parsed is not None
        assert parsed["event_type"] == "mint_freeze"

    def test_classifies_lp_change(self) -> None:
        parsed = parse_helius_event(
            _helius_token_payload(
                type="SWAP",
                description="Raydium pool liquidity removed by LP",
            )
        )
        assert parsed is not None
        assert parsed["event_type"] == "lp_change"

    def test_empty_payload_is_none(self) -> None:
        assert parse_helius_event({}) is None
        assert parse_helius_event({"type": "TRANSFER"}) is None  # no wallet


# ── Arkham-style labels ─────────────────────────────────────────────────


class TestWalletLabels:
    def test_exchange_label_from_registry(self) -> None:
        # Binance hot wallet from the local entity registry dataset
        label, entity_type = _wallet_label("0xBE0eB53FC46b790099138e3d32C721856d41e865")
        assert entity_type == "cex_hot_wallet"
        assert "binance" in label.lower()

    def test_unknown_wallet_shortened(self) -> None:
        label, entity_type = _wallet_label("4NDS2vZPcQRQv3RvMZeX5QK2Ds1gVEZXxHwQqZsQ1Vkk")
        assert entity_type == "unknown"
        assert "…" in label or len(label) <= 14

    def test_empty_wallet(self) -> None:
        assert _wallet_label("") == ("", "")

    def test_format_alert_readable(self) -> None:
        # Binance hot wallet is the MOVING party (Arkham-style attribution)
        parsed = parse_helius_event(
            _helius_token_payload(
                valueUsd=1_200_000.0,
                feePayer="0xBE0eB53FC46b790099138e3d32C721856d41e865",
                tokenTransfers=[
                    {
                        "fromUserAccount": "0xBE0eB53FC46b790099138e3d32C721856d41e865",
                        "toUserAccount": "0xdead0000000000000000000000000000000000",
                        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                        "symbol": "USDC",
                        "tokenAmount": {"amount": "1200000000", "decimals": 6, "uiAmount": 1200.0},
                    }
                ],
            )
        )
        assert parsed is not None
        from_label, from_type = _wallet_label(parsed["wallet"])
        to_label, _ = _wallet_label(parsed["counterparty"])
        title, description = _format_alert(parsed, from_label, to_label)
        # Arkham-style: readable entity, not a bare address
        assert from_type == "cex_hot_wallet"
        assert "binance" in title.lower()
        assert "1,200,000 USDC" in title
        assert "→" in description  # from → to attribution preserved


# ── Importance calibration ──────────────────────────────────────────────


class TestImportance:
    def test_whale_million_clears_main_gate(self) -> None:
        parsed = {
            "event_type": "whale_move",
            "amount_usd": 1_200_000.0,
            "token": {"ui_amount": 1200.0, "symbol": "USDC"},
        }
        assert _assess_importance(parsed, "unknown") >= 0.7

    def test_large_transfer_news_band(self) -> None:
        parsed = {"event_type": "token_transfer", "amount_usd": 50_000.0, "token": {"ui_amount": 50.0}}
        imp = _assess_importance(parsed, "unknown")
        assert 0.4 <= imp < 0.7

    def test_small_transfer_below_gate(self) -> None:
        parsed = {"event_type": "transfer", "amount_usd": 50.0, "token": {}}
        assert _assess_importance(parsed, "unknown") < 0.4

    def test_scam_entity_boost(self) -> None:
        base = {"event_type": "mint_freeze", "amount_usd": 1_000.0, "token": {"ui_amount": 1.0}}
        normal = _assess_importance(base, "unknown")
        flagged = _assess_importance(base, "known_scam")
        assert flagged > normal


# ── Routing (offline: Redis throttle + alert pipeline mocked) ───────────


class TestRouteWebhookAlert:
    @pytest.mark.asyncio
    async def test_smoke_meaningful_event_produces_alert(self) -> None:
        payload = _helius_token_payload(valueUsd=1_200_000.0)
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok") as thr,
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:test:1")
        assert result["decision"] == "main"
        assert result["event_type"] == "token_transfer"
        assert result["importance"] >= 0.7
        assert push.await_count == 1
        kwargs = push.await_args.kwargs
        assert kwargs["severity"] == "critical"
        assert kwargs["metadata"]["route"] == "main"
        assert kwargs["metadata"]["escalate"] is True
        thr.assert_called_once_with("helius", payload["feePayer"], "evt:test:1")

    @pytest.mark.asyncio
    async def test_small_event_skipped_no_alert(self) -> None:
        payload = _helius_payload()  # ~$300 native transfer
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:test:2")
        assert result["decision"] == "skip"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_per_wallet_throttle_blocks(self) -> None:
        payload = _helius_token_payload(valueUsd=1_200_000.0)
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="throttled"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", payload, "evt:test:3")
        assert result["decision"] == "throttled"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unparseable_payload(self) -> None:
        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok"),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", {"type": "TRANSFER"}, "evt:test:4")
        assert result["decision"] == "unparseable"
        push.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fail_open_on_router_error(self) -> None:
        with (
            patch("app.databus.alert_router.parse_helius_event", side_effect=RuntimeError("boom")),
            patch("app.alert_pipeline.push_alert", new_callable=AsyncMock) as push,
        ):
            result = await route_webhook_alert("helius", _helius_payload(), "evt:test:5")
        # Fail-open: a router error must never raise out of the webhook handler
        assert result["decision"] == "skip"
        push.assert_not_awaited()
