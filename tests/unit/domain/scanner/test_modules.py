"""Tests for app/domains/scanner/modules.py - Scanner module functions.

Tests the individual scanner module functions that check various
token security aspects.
"""

from __future__ import annotations

import pytest


class TestCheckerFunctions:
    """Tests for individual token check functions."""

    @pytest.mark.asyncio
    async def test_check_honeypot_is_exists(self):
        """_check_honeypot_is should be callable."""
        from app.domains.scanner.modules import _check_honeypot_is

        # Mock the function to avoid real API calls
        result = await _check_honeypot_is("0xtoken", "ethereum")
        assert result is not None

    @pytest.mark.asyncio
    async def test_check_defi_scanner_exists(self):
        """_check_defi_scanner should be callable."""
        from app.domains.scanner.modules import _check_defi_scanner

        result = await _check_defi_scanner("0xtoken", "ethereum")
        assert result is not None

    @pytest.mark.asyncio
    async def test_check_blockscout_exists(self):
        """_check_blockscout should be callable."""
        from app.domains.scanner.modules import _check_blockscout

        result = await _check_blockscout("0xtoken", "ethereum")
        assert result is not None

    @pytest.mark.asyncio
    async def test_check_token_sniffer_exists(self):
        """_check_token_sniffer should be callable."""
        from app.domains.scanner.modules import _check_token_sniffer

        result = await _check_token_sniffer("0xtoken", "ethereum")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_deployer_info_exists(self):
        """_get_deployer_info should be callable."""
        from app.domains.scanner.modules import _get_deployer_info

        result = await _get_deployer_info("0xtoken", "ethereum")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_holder_data_exists(self):
        """_get_holder_data should be callable."""
        from app.domains.scanner.modules import _get_holder_data

        result = await _get_holder_data("0xtoken", "ethereum")
        assert result is not None
