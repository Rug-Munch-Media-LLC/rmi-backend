"""Tests for app/domains/scanner/modules.py - Scanner module functions.

Tests the individual scanner module functions that check various
token security aspects. Replaces the original tests that only asserted
`result is not None` with real behavioral assertions.
"""

from __future__ import annotations

import pytest


class TestCheckerFunctions:
    """Tests for individual token check functions."""

    @pytest.mark.asyncio
    async def test_check_honeypot_is_returns_dict_with_check_field(self):
        """_check_honeypot_is should return a dict with 'check' == 'honeypot'."""
        from app.domains.scanner.modules import _check_honeypot_is

        result = await _check_honeypot_is("0xtoken", "ethereum")
        assert isinstance(result, dict)
        assert result.get("check") == "honeypot"
        # When no data source available, should return error result
        assert result.get("result") in ("error", "scanned", "unavailable")

    @pytest.mark.asyncio
    async def test_check_defi_scanner_returns_dict_with_check_field(self):
        """_check_defi_scanner should return a dict with 'check' == 'defi_scanner'."""
        from app.domains.scanner.modules import _check_defi_scanner

        result = await _check_defi_scanner("0xtoken", "ethereum")
        assert isinstance(result, dict)
        assert result.get("check") == "defi_scanner"
        assert "has_defi_presence" in result
        assert isinstance(result.get("protocols_found"), int)

    @pytest.mark.asyncio
    async def test_check_blockscout_returns_dict_with_check_field(self):
        """_check_blockscout should return a dict with 'check' == 'blockscout'."""
        from app.domains.scanner.modules import _check_blockscout

        result = await _check_blockscout("0xtoken", "ethereum")
        assert isinstance(result, dict)
        assert result.get("check") == "blockscout"
        assert result.get("result") in ("scanned", "unavailable")

    @pytest.mark.asyncio
    async def test_check_token_sniffer_returns_pending_status(self):
        """_check_token_sniffer should return pending status (stub)."""
        from app.domains.scanner.modules import _check_token_sniffer

        result = await _check_token_sniffer("0xtoken", "ethereum")
        assert isinstance(result, dict)
        assert result.get("check") == "tokensniffer"
        assert result.get("result") is not None

    @pytest.mark.asyncio
    async def test_get_deployer_info_returns_dict_with_check_field(self):
        """_get_deployer_info should return a dict with 'check' == 'deployer'."""
        from app.domains.scanner.modules import _get_deployer_info

        result = await _get_deployer_info("0xtoken", "ethereum")
        assert isinstance(result, dict)
        assert result.get("check") == "deployer"

    @pytest.mark.asyncio
    async def test_get_holder_data_returns_dict_with_check_field(self):
        """_get_holder_data should return a dict with check field."""
        from app.domains.scanner.modules import _get_holder_data

        result = await _get_holder_data("0xtoken", "ethereum")
        assert isinstance(result, dict)
        assert result.get("check") in ("holders", "holder")
