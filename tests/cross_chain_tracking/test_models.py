"""Tests for cross_chain_tracking models — Pydantic v2 validation."""

from __future__ import annotations

from app.cross_chain_tracking.models import (
    CrossChainMatch,
    CrossChainProfile,
    DeployerEvent,
    DeployerSummary,
    MultiChainDeployer,
)


class TestDeployerEvent:
    def test_minimal(self) -> None:
        ev = DeployerEvent(deployer="0xabc", token_address="0xdef", chain="ethereum")
        assert ev.deployer == "0xabc"
        assert ev.token_address == "0xdef"
        assert ev.chain == "ethereum"
        assert ev.type == "token"  # default

    def test_full(self) -> None:
        ev = DeployerEvent(
            deployer="0xabc",
            token_address="0xdef",
            chain="bsc",
            block_number=12345,
            block_timestamp="2025-06-01T00:00:00Z",
            tx_hash="0xdead",
            type="factory",
            factory="0xfactory",
        )
        assert ev.factory == "0xfactory"
        assert ev.type == "factory"


class TestMultiChainDeployer:
    def test_minimal(self) -> None:
        mcd = MultiChainDeployer(
            deployer_address="0xabc",
            chains=["ethereum", "bsc"],
            chain_count=2,
        )
        assert mcd.deployer_address == "0xabc"
        assert mcd.chain_count == 2
        assert mcd.confidence == 0.95  # default

    def test_custom_confidence(self) -> None:
        mcd = MultiChainDeployer(
            deployer_address="0xabc",
            chains=["ethereum", "solana"],
            chain_count=2,
            confidence=0.80,
            evidence="Mixed EVM/non-EVM deployer",
        )
        assert mcd.confidence == 0.80
        assert "Mixed" in mcd.evidence


class TestCrossChainMatch:
    def test_minimal(self) -> None:
        match = CrossChainMatch(
            address="0xabc",
            chain="bsc",
            confidence=0.95,
            method="same_deployer",
        )
        assert match.address == "0xabc"
        assert match.confidence == 0.95

    def test_defaults(self) -> None:
        match = CrossChainMatch(address="0xabc", chain="bsc", confidence=0.9, method="bridge_mapping")
        assert match.method == "bridge_mapping"
        assert match.evidence == ""  # default


class TestCrossChainProfile:
    def test_minimal(self) -> None:
        profile = CrossChainProfile(
            entity_id="ent_test123",
            primary_address="0xabc",
            primary_chain="ethereum",
        )
        assert profile.total_addresses == 0
        assert profile.total_chains == 0
        assert profile.chains == []

    def test_with_addresses(self) -> None:
        addresses = [
            CrossChainMatch(address="0xabc", chain="ethereum", confidence=1.0, method="same_deployer"),
            CrossChainMatch(address="0xabc", chain="bsc", confidence=0.95, method="same_deployer"),
        ]
        profile = CrossChainProfile(
            entity_id="ent_test456",
            primary_address="0xabc",
            primary_chain="ethereum",
            addresses=addresses,
            total_addresses=2,
            total_chains=2,
            chains=["ethereum", "bsc"],
        )
        assert len(profile.addresses) == 2
        assert profile.total_chains == 2

    def test_serialization(self) -> None:
        profile = CrossChainProfile(
            entity_id="ent_test789",
            primary_address="0xabc",
            primary_chain="ethereum",
            risk_score=25.0,
            label="High-risk deployer",
        )
        data = profile.model_dump(mode="json")
        assert data["entity_id"] == "ent_test789"
        assert data["risk_score"] == 25.0
        assert data["label"] == "High-risk deployer"


class TestDeployerSummary:
    def test_minimal(self) -> None:
        ds = DeployerSummary(deployer="0xabc", chain="ethereum")
        assert ds.deployment_count == 0
        assert ds.token_types == []
