"""Unit tests for app/domains/scanners/liquidity_verifier.py.

Tests LiquidityVerifier.verify_lock() against mocked API responses for the
four supported locker platforms (UNCX, Team Finance, PinkLock, Mudra),
Solana LP-burn verification, edge cases (invalid address, unsupported
chain, all platforms unavailable), and graceful handling of API failures.

Every external API call (Etherscan, Moralis, Helius, DexScreener) is
mocked — no network access in these tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domains.scanners.liquidity_verifier import (
    LiquidityVerifier,
    LockStatus,
)

# Known locker addresses from app/chain_registry (lowercased by the registry).
UNCX_LOCKER = "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2"
TEAM_FINANCE_LOCKER = "0xe2fE530C047f2d85298b07D9333C05737f1435fB"
PINKLOCK_LOCKER = "0x7ee058420e5937496f5a2096f04caa7721cf70cc"
MUDRA_LOCKER = "0x2D8E3580C6E428d4EaA069198D0B76ae0f98e43a"


class TestVerifyLockPlatforms:
    """verify_lock() recognition of each supported locker platform."""

    @pytest.mark.asyncio
    async def test_verify_lock_uncx_ethereum(self) -> None:
        """A UNCX holder on Ethereum should be recognized and fully locked."""
        verifier = LiquidityVerifier()
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(return_value=[{"address": UNCX_LOCKER, "percentage": 95.0, "label": ""}]),
        ):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.status == LockStatus.LOCKED
        assert report.total_locked_percentage >= 95
        assert report.lock_details[0].platform == "UNCX Network"
        assert report.lock_details[0].is_legit is True
        assert report.is_legitimate_locker is True
        assert report.fake_lock_detected is False

    @pytest.mark.asyncio
    async def test_verify_lock_team_finance_ethereum(self) -> None:
        """A Team Finance holder on Ethereum should be recognized."""
        verifier = LiquidityVerifier()
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(return_value=[{"address": TEAM_FINANCE_LOCKER, "percentage": 90.0, "label": ""}]),
        ):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.status == LockStatus.LOCKED
        assert report.lock_details[0].platform == "Team Finance"
        assert report.lock_details[0].is_legit is True

    @pytest.mark.asyncio
    async def test_verify_lock_pinklock_ethereum(self) -> None:
        """A PinkLock holder on Ethereum should be recognized."""
        verifier = LiquidityVerifier()
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(return_value=[{"address": PINKLOCK_LOCKER, "percentage": 100.0, "label": ""}]),
        ):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.status == LockStatus.LOCKED
        assert report.lock_details[0].platform == "PinkLock"
        assert report.lock_details[0].is_legit is True

    @pytest.mark.asyncio
    async def test_verify_lock_mudra_bsc(self) -> None:
        """A Mudra holder on BSC should be recognized (BSC-only locker)."""
        verifier = LiquidityVerifier()
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(return_value=[{"address": MUDRA_LOCKER, "percentage": 98.0, "label": ""}]),
        ):
            report = await verifier.verify_lock("0xtoken", "bsc")
        assert report.status == LockStatus.LOCKED
        assert report.lock_details[0].platform == "Mudra"
        assert report.total_locked_percentage >= 95


class TestVerifyLockScenarios:
    """verify_lock() behavior for split locks, fake locks, and stablecoins."""

    @pytest.mark.asyncio
    async def test_verify_lock_detects_split_lock(self) -> None:
        """80/20 lock split across two platforms should be flagged as split."""
        verifier = LiquidityVerifier()
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(
                return_value=[
                    {"address": UNCX_LOCKER, "percentage": 80.0, "label": ""},
                    {"address": PINKLOCK_LOCKER, "percentage": 20.0, "label": ""},
                ]
            ),
        ):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.has_split_lock is True
        assert report.total_locked_percentage == 100.0
        assert len(report.lock_details) == 2

    @pytest.mark.asyncio
    async def test_verify_lock_detects_fake_lock(self) -> None:
        """An unverified contract labeled 'lock' should be flagged as fake."""
        verifier = LiquidityVerifier()
        fake_addr = "0x1234567890123456789012345678901234567890"
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(return_value=[{"address": fake_addr, "percentage": 95.0, "label": "lock"}]),
        ):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.fake_lock_detected is True
        assert report.lock_details[0].is_legit is False
        assert "UNVERIFIED" in report.lock_details[0].platform
        assert report.is_legitimate_locker is False

    @pytest.mark.asyncio
    async def test_verify_lock_skips_stablecoin(self) -> None:
        """Stablecoins should be skipped with a LOW risk report, no API calls."""
        verifier = LiquidityVerifier()
        with patch.object(verifier, "_get_lp_holders", AsyncMock()) as mocked:
            report = await verifier.verify_lock("0xusdc", "ethereum", symbol="USDC")
        mocked.assert_not_awaited()
        assert report.status == LockStatus.LOCKED
        assert report.risk_score == 0
        assert report.risk_level == "LOW"
        assert any("not applicable" in w.lower() for w in report.warnings)

    @pytest.mark.asyncio
    async def test_verify_lock_solana_lp_burn(self) -> None:
        """Solana tokens with burned LP should be reported as BURNED."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_check_solana_lp_burn", AsyncMock(return_value=True)),
            patch.object(verifier, "_detect_pumpfun", AsyncMock(return_value=False)),
        ):
            report = await verifier.verify_lock("SolMintAddr", "solana")
        assert report.status == LockStatus.BURNED
        assert report.lp_burned is True
        assert report.total_locked_percentage == 100.0
        assert report.risk_level == "LOW"


class TestVerifyLockEdgeCases:
    """verify_lock() edge cases: unavailable sources, bad input, bad chains."""

    @pytest.mark.asyncio
    async def test_verify_lock_all_platforms_unavailable(self) -> None:
        """When no holder data is available the report should be UNLOCKED/high risk."""
        verifier = LiquidityVerifier()
        with patch.object(verifier, "_get_lp_holders", AsyncMock(return_value=[])):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.status == LockStatus.UNLOCKED
        assert report.total_locked_percentage == 0.0
        assert report.risk_score >= 40
        assert report.risk_level in ("HIGH", "CRITICAL")

    @pytest.mark.asyncio
    async def test_verify_lock_unsupported_chain(self) -> None:
        """Unsupported chains should still return a report instead of raising."""
        verifier = LiquidityVerifier()
        with patch.object(verifier, "_get_lp_holders", AsyncMock(return_value=[])):
            report = await verifier.verify_lock("0xtoken", "cosmos")
        assert report.chain == "cosmos"
        assert report.status in (LockStatus.UNLOCKED, LockStatus.PARTIAL)

    @pytest.mark.asyncio
    async def test_verify_lock_invalid_address_does_not_crash(self) -> None:
        """Empty/invalid token addresses should not raise exceptions."""
        verifier = LiquidityVerifier()
        with patch.object(verifier, "_get_lp_holders", AsyncMock(return_value=[])):
            report = await verifier.verify_lock("", "ethereum")
        assert isinstance(report.total_locked_percentage, float)

    def test_is_known_locker_unknown_address(self) -> None:
        """Unknown locker addresses should return None (not a known platform)."""
        verifier = LiquidityVerifier()
        assert verifier.is_known_locker("0xdeadbeef", "ethereum") is None
        assert verifier.is_known_locker("0x1234567890123456789012345678901234567890", "bsc") is None

    def test_is_dead_address_evm_and_solana(self) -> None:
        """Burn/dead addresses should be recognized on EVM and Solana."""
        verifier = LiquidityVerifier()
        assert verifier.is_dead_address("0x000000000000000000000000000000000000dEaD", "ethereum") is True
        assert verifier.is_dead_address("0x0000000000000000000000000000000000000000", "ethereum") is True
        assert verifier.is_dead_address("11111111111111111111111111111111", "solana") is True
        assert verifier.is_dead_address("0x1234567890123456789012345678901234567890", "ethereum") is False


class TestAPIFailureHandling:
    """Graceful degradation when upstream APIs fail or rate-limit."""

    @pytest.mark.asyncio
    async def test_etherscan_get_http_error_returns_none(self) -> None:
        """Etherscan HTTP errors should return None rather than raising."""
        verifier = LiquidityVerifier()
        with (
            patch.dict(
                "app.domains.scanners.liquidity_verifier.ETHERSCAN_NETWORKS",
                {"ethereum": {"url": "https://api.example.invalid/api", "key": "test-key"}},
            ),
            patch.object(verifier.client, "get", AsyncMock(side_effect=httpx.HTTPError("boom"))),
        ):
            result = await verifier._etherscan_get("ethereum", {"module": "account", "action": "balance"})
        assert result is None

    @pytest.mark.asyncio
    async def test_etherscan_get_rate_limited_returns_none(self) -> None:
        """Etherscan 429 responses should be handled gracefully."""
        verifier = LiquidityVerifier()

        class RateLimitedResponse:
            status_code = 429

            def json(self) -> dict:
                return {}

        with (
            patch.dict(
                "app.domains.scanners.liquidity_verifier.ETHERSCAN_NETWORKS",
                {"ethereum": {"url": "https://api.example.invalid/api", "key": "test-key"}},
            ),
            patch.object(verifier.client, "get", AsyncMock(return_value=RateLimitedResponse())),
        ):
            result = await verifier._etherscan_get("ethereum", {"module": "account", "action": "balance"})
        assert result is None

    @pytest.mark.asyncio
    async def test_moralis_get_http_error_returns_none(self) -> None:
        """Moralis failures should return None rather than raising."""
        verifier = LiquidityVerifier()
        with (
            patch("app.domains.scanners.liquidity_verifier.MORALIS_API_KEY", "test-key"),
            patch.object(verifier.client, "get", AsyncMock(side_effect=httpx.HTTPError("boom"))),
        ):
            result = await verifier._moralis_get("https://deep-index.moralis.io/api/v2.2/erc20/x/owners")
        assert result is None

    @pytest.mark.asyncio
    async def test_moralis_get_missing_key_returns_none(self) -> None:
        """Missing Moralis key should short-circuit without network calls."""
        verifier = LiquidityVerifier()
        with (
            patch("app.domains.scanners.liquidity_verifier.MORALIS_API_KEY", ""),
            patch.object(verifier.client, "get", AsyncMock()) as mocked,
        ):
            result = await verifier._moralis_get("https://deep-index.moralis.io/api/v2.2/erc20/x/owners")
        mocked.assert_not_awaited()
        assert result is None

    @pytest.mark.asyncio
    async def test_helius_rpc_http_error_returns_none(self) -> None:
        """Helius RPC failures should return None rather than raising."""
        verifier = LiquidityVerifier()
        with (
            patch("app.domains.scanners.liquidity_verifier.HELIUS_API_KEY", "test-key"),
            patch.object(verifier.client, "post", AsyncMock(side_effect=httpx.HTTPError("boom"))),
        ):
            result = await verifier._helius_rpc("getTokenAccountsByOwner", [])
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__])
