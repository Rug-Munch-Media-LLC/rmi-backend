"""Tests for the extended GoPlus security mapping (scam-signal fields).

Verifies ``_goplus_api_scan`` surfaces the modern scam-technique flags
(transfer pausability, trading cooldown, owner-change balance, selfdestruct,
owner-renounce derivation) and that ``_map_own_security_data`` passes
through the own-engine deployer-dump / dusting signals.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.domains.scanner.modules._goplus_helpers import (
    _goplus_api_scan,
    _goplus_owner_renounced,
    _map_own_security_data,
)


class TestGoplusOwnerRenounced:
    def test_empty_owner_is_renounced(self) -> None:
        assert _goplus_owner_renounced({"owner_address": ""}) is True

    def test_zero_owner_is_renounced(self) -> None:
        assert _goplus_owner_renounced({"owner_address": "0x0000000000000000000000000000000000000000"}) is True

    def test_present_owner_is_unknown(self) -> None:
        assert _goplus_owner_renounced({"owner_address": "0xabc"}) is None

    def test_missing_field_is_renounced(self) -> None:
        assert _goplus_owner_renounced({}) is True


class TestGoplusApiScan:
    @pytest.mark.asyncio
    async def test_maps_scam_signal_flags(self) -> None:
        """The scan output must surface the modern scam-technique flags."""
        token_data = {
            "is_honeypot": "0",
            "is_proxy": "1",
            "is_mintable": "0",
            "is_blacklisted": "0",
            "is_whitelisted": "0",
            "can_take_back_ownership": "0",
            "hidden_owner": "0",
            "owner_change_balance": "1",
            "selfdestruct": "1",
            "transfer_pausable": "1",
            "trading_cooldown": "1",
            "slippage_modifiable": "1",
            "owner_address": "",
            "buy_tax": "5",
            "sell_tax": "5",
            "holder_count": "100",
            "total_supply": "1000000",
            "is_open_source": "1",
        }
        payload = {"result": {"0xabc": token_data}}
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload

        with patch("app.domains.scanner.modules._goplus_helpers.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = resp
            out = await _goplus_api_scan("1", "0xabc", "ethereum")

        assert out["result"] == "scanned"
        assert out["owner_change_balance"] is True
        assert out["selfdestruct"] is True
        assert out["transfer_pausable"] is True
        assert out["trading_cooldown"] is True
        assert out["slippage_modifiable"] is True
        assert out["owner_renounced"] is True
        # risk score accumulates selfdestruct (+30) + owner_change (+15)
        # + transfer_pausable (+10) + trading_cooldown (+5) + slippage? (no)
        assert out["risk_score"] >= 60

    @pytest.mark.asyncio
    async def test_clean_token_maps_green_flags(self) -> None:
        """A clean token maps False for every scam flag."""
        token_data = {
            "is_honeypot": "0",
            "is_proxy": "0",
            "is_mintable": "0",
            "is_blacklisted": "0",
            "is_whitelisted": "1",
            "can_take_back_ownership": "0",
            "hidden_owner": "0",
            "owner_change_balance": "0",
            "selfdestruct": "0",
            "transfer_pausable": "0",
            "trading_cooldown": "0",
            "slippage_modifiable": "0",
            "owner_address": "0xowner",
            "buy_tax": "0",
            "sell_tax": "0",
            "holder_count": "500",
            "total_supply": "1000000",
            "is_open_source": "1",
        }
        payload = {"result": {"0xabc": token_data}}
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload

        with patch("app.domains.scanner.modules._goplus_helpers.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = resp
            out = await _goplus_api_scan("1", "0xabc", "ethereum")

        for key in (
            "owner_change_balance",
            "selfdestruct",
            "transfer_pausable",
            "trading_cooldown",
            "slippage_modifiable",
        ):
            assert out[key] is False
        assert out["owner_renounced"] is None  # owner present → unknown


class TestMapOwnSecurityData:
    def test_passes_through_own_scam_signals(self) -> None:
        own = {
            "risk_score": 35,
            "is_honeypot": False,
            "is_mintable": False,
            "is_proxy": False,
            "owner_address": "0xowner",
            "buy_tax_pct": 0.0,
            "sell_tax_pct": 0.0,
            "holder_count": 10,
            "total_supply": "",
            "deployer_dump_detected": True,
            "deployer_dump_count": 3,
            "deployer_dump_value": 9000000.0,
            "dusting_detected": True,
            "dust_sender": "0xduster",
            "dust_recipients": 25,
        }
        out = _map_own_security_data(own)
        assert out["deployer_dump_detected"] is True
        assert out["deployer_dump_count"] == 3
        assert out["dusting_detected"] is True
        assert out["dust_sender"] == "0xduster"
        assert out["dust_recipients"] == 25

    def test_missing_own_scam_signals_default_safe(self) -> None:
        out = _map_own_security_data({"risk_score": 0})
        assert out["deployer_dump_detected"] is None
        assert out["deployer_dump_count"] == 0
        assert out["dusting_detected"] is None
        assert out["dust_recipients"] == 0
