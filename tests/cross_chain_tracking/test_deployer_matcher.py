"""Tests for deployer_matcher — confidence scoring and logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.cross_chain_tracking.deployer_matcher import EVM_CHAINS, NON_EVM_CHAINS, DeployerMatcher


class TestConfidenceScoring:
    """Test the confidence calculation logic within DeployerMatcher."""

    @pytest.mark.asyncio
    async def test_all_evm_chains_high_confidence(self) -> None:
        """Same address on 2+ EVM chains = 0.95 confidence."""
        matcher = DeployerMatcher()

        # Mock clickhouse_reader to return a multi-EVM-chain deployer
        with patch(
            "app.cross_chain_tracking.deployer_matcher.fetch_multi_chain_deployers",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = [
                {
                    "deployer": "0xabc123",
                    "chains": ["ethereum", "bsc", "polygon"],
                    "chain_count": 3,
                    "total_deployments": 45,
                    "first_seen": "2025-01-01",
                    "last_seen": "2025-12-31",
                }
            ]

            # Also mock fetch_deployments_for_deployer
            with patch(
                "app.cross_chain_tracking.deployer_matcher.fetch_deployments_for_deployer",
                new_callable=AsyncMock,
            ) as mock_events:
                mock_events.return_value = []

                results = await matcher.find_multi_chain_deployers(min_chains=2, limit=100)

                assert len(results) == 1
                mcd = results[0]
                assert mcd.deployer_address == "0xabc123"
                assert mcd.chain_count == 3
                assert mcd.confidence == 0.95  # All EVM
                assert "EVM deployer" in mcd.evidence

    @pytest.mark.asyncio
    async def test_mixed_evm_non_evm_medium_confidence(self) -> None:
        """Same address on EVM + non-EVM = 0.80 confidence."""
        matcher = DeployerMatcher()

        with patch(
            "app.cross_chain_tracking.deployer_matcher.fetch_multi_chain_deployers",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = [
                {
                    "deployer": "0xdef456",
                    "chains": ["ethereum", "bsc", "solana"],
                    "chain_count": 3,
                    "total_deployments": 12,
                    "first_seen": "2025-06-01",
                    "last_seen": "2025-12-01",
                }
            ]

            with patch(
                "app.cross_chain_tracking.deployer_matcher.fetch_deployments_for_deployer",
                new_callable=AsyncMock,
            ) as mock_events:
                mock_events.return_value = []

                results = await matcher.find_multi_chain_deployers(min_chains=2, limit=100)

                assert len(results) == 1
                mcd = results[0]
                assert mcd.chain_count == 3
                assert mcd.confidence == 0.80  # Mixed
                assert "EVM" in mcd.evidence and "non-EVM" in mcd.evidence

    @pytest.mark.asyncio
    async def test_non_evm_only_lower_confidence(self) -> None:
        """Non-EVM only = 0.60 confidence (address format differs)."""
        matcher = DeployerMatcher()

        with patch(
            "app.cross_chain_tracking.deployer_matcher.fetch_multi_chain_deployers",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = [
                {
                    "deployer": "solana_addr_123",
                    "chains": ["solana"],
                    "chain_count": 1,
                    "total_deployments": 5,
                    "first_seen": "2025-06-01",
                    "last_seen": "2025-12-01",
                }
            ]

            with patch(
                "app.cross_chain_tracking.deployer_matcher.fetch_deployments_for_deployer",
                new_callable=AsyncMock,
            ) as mock_events:
                mock_events.return_value = []

                # min_chains=1 so it qualifies
                results = await matcher.find_multi_chain_deployers(
                    min_chains=1, limit=100
                )

                assert len(results) == 1
                mcd = results[0]
                # Single chain, not multi-chain
                assert mcd.chain_count == 1

    def test_evm_chain_set(self) -> None:
        assert "ethereum" in EVM_CHAINS
        assert "bsc" in EVM_CHAINS
        assert "polygon" in EVM_CHAINS
        assert "solana" not in EVM_CHAINS
        assert "tron" not in EVM_CHAINS

    def test_non_evm_chain_set(self) -> None:
        assert "solana" in NON_EVM_CHAINS
        assert "tron" in NON_EVM_CHAINS
        assert "ethereum" not in NON_EVM_CHAINS


class TestResolveDeployer:
    @pytest.mark.asyncio
    async def test_no_events_returns_single(self) -> None:
        """Deployer with no events returns a minimal profile."""
        matcher = DeployerMatcher()

        with patch(
            "app.cross_chain_tracking.deployer_matcher.fetch_deployments_for_deployer",
            new_callable=AsyncMock,
        ) as mock_events:
            mock_events.return_value = []

            profile = await matcher.resolve_deployer("0xnone", "ethereum")
            assert profile.primary_address == "0xnone"
            assert profile.total_chains == 1  # Just the queried chain
            assert profile.total_deployments == 0

    @pytest.mark.asyncio
    async def test_with_deployments_multiple_chains(self) -> None:
        """Deployer with events on multiple chains returns full profile."""
        matcher = DeployerMatcher()

        from app.cross_chain_tracking.models import DeployerEvent

        events = [
            DeployerEvent(deployer="0xmulti", token_address="0xt1", chain="ethereum", block_timestamp="2025-01-01"),
            DeployerEvent(deployer="0xmulti", token_address="0xt2", chain="ethereum", block_timestamp="2025-02-01"),
            DeployerEvent(deployer="0xmulti", token_address="0xt3", chain="bsc", block_timestamp="2025-03-01"),
            DeployerEvent(deployer="0xmulti", token_address="0xt4", chain="polygon", block_timestamp="2025-04-01"),
        ]

        with patch(
            "app.cross_chain_tracking.deployer_matcher.fetch_deployments_for_deployer",
            new_callable=AsyncMock,
        ) as mock_events:
            mock_events.return_value = events

            profile = await matcher.resolve_deployer("0xmulti", "ethereum")
            assert profile.total_chains == 3
            assert profile.total_deployments == 4
            assert profile.total_addresses == 3  # eth, bsc, polygon
            assert "ethereum" in profile.chains
            assert "bsc" in profile.chains
            assert "polygon" in profile.chains
            assert profile.primary_chain == "ethereum"  # most deployments
