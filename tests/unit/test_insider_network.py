"""
Tests for insider_network.py — Insider Web Mapper.
"""

from unittest.mock import patch

import pytest

from app.insider_network import (
    InsiderCluster,
    InsiderLink,
    InsiderNetworkAnalyzer,
    InsiderNetworkReport,
    _compute_cluster_risk,
    _detect_co_deployer,
    _detect_co_trading,
    _detect_shared_funding,
    _detect_value_transfers,
    _truncate_address,
    analyze_insider_network,
    format_insider_network_report,
    is_valid_address,
)

# ── Address Validation Tests ──────────────────────────────────────


def test_is_valid_address_solana():
    """Valid Solana address (base58, 32-44 chars)."""
    assert is_valid_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") is True


def test_is_valid_address_evm():
    """Valid EVM address (0x + 40 hex chars)."""
    assert is_valid_address("0xdAC17F958D2ee523a2206206994597C13D831ec7") is True


def test_is_valid_address_empty():
    """Empty string is invalid."""
    assert is_valid_address("") is False


def test_is_valid_address_none():
    """None is invalid."""
    assert is_valid_address(None) is False


def test_is_valid_address_invalid():
    """Garbage string is invalid."""
    assert is_valid_address("not-a-valid-address") is False


def test_is_valid_address_eip55_checksum():
    """Mixed-case EIP-55 compliant address passes."""
    assert is_valid_address("0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B") is True


def test_is_valid_address_eip55_lowercase():
    """All-lowercase EVM address is valid."""
    assert is_valid_address("0xdac17f958d2ee523a2206206994597c13d831ec7") is True


def test_is_valid_address_solana_valid():
    """Valid Solana address passes."""
    assert is_valid_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") is True


# ── _truncate_address Tests ──────────────────────────────────────


def test_truncate_long_address():
    """Long address gets truncated."""
    addr = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    result = _truncate_address(addr)
    assert result == "0xdAC1...1ec7"
    assert len(result) == 13


def test_truncate_short_address():
    """Short address is not truncated."""
    assert _truncate_address("0x1234") == "0x1234"


# ── InsiderLink Tests ────────────────────────────────────────────


def test_insider_link_defaults():
    """InsiderLink defaults are set correctly."""
    link = InsiderLink(
        source_wallet="0xaaa",
        target_wallet="0xbbb",
        relationship_type="shared_funding",
    )
    assert link.strength == 0.0
    assert link.evidence == []
    assert link.first_seen == ""
    assert link.last_seen == ""


def test_insider_link_with_values():
    """InsiderLink with all fields set."""
    link = InsiderLink(
        source_wallet="0xaaa",
        target_wallet="0xbbb",
        relationship_type="co_trading",
        strength=0.75,
        evidence=["Traded same token"],
        first_seen="2024-01-01",
        last_seen="2024-06-01",
    )
    assert link.strength == 0.75
    assert "Traded same token" in link.evidence


# ── InsiderCluster Tests ──────────────────────────────────────────


def test_insider_cluster_defaults():
    """InsiderCluster defaults are set correctly."""
    cluster = InsiderCluster(cluster_id="INSIDER-0001")
    assert cluster.wallets == []
    assert cluster.projects_involved == []
    assert cluster.risk_score == 0.0
    assert cluster.member_count == 0


def test_insider_cluster_member_count():
    """Member count reflects wallet list."""
    cluster = InsiderCluster(
        cluster_id="INSIDER-0001",
        wallets=["0xaaa", "0xbbb", "0xccc"],
        member_count=3,
    )
    assert cluster.member_count == 3


# ── InsiderNetworkReport Tests ────────────────────────────────────


def test_report_defaults():
    """InsiderNetworkReport defaults are correct."""
    report = InsiderNetworkReport(target_wallet="0xaaa")
    assert report.target_wallet == "0xaaa"
    assert report.clusters == []
    assert report.total_connected_wallets == 0
    assert report.total_clusters == 0
    assert report.highest_risk_score == 0.0


# ── Relationship Detection Tests ──────────────────────────────────


def test_detect_shared_funding():
    """Detect wallets sharing a common funder."""
    wallets = ["0xaaa", "0xbbb", "0xccc"]
    tx_data = {
        "0xaaa": [
            {
                "from": "0xfunder1",
                "to": "0xaaa",
                "value": "1000000000000000000",
            }
        ],
        "0xbbb": [
            {
                "from": "0xfunder1",
                "to": "0xbbb",
                "value": "500000000000000000",
            }
        ],
        "0xccc": [
            {
                "from": "0xfunder2",
                "to": "0xccc",
                "value": "300000000000000000",
            }
        ],
    }
    links = _detect_shared_funding(wallets, tx_data)
    assert len(links) >= 1
    link_found = False
    for link in links:
        if (
            link.source_wallet in ("0xaaa", "0xbbb")
            and link.target_wallet in ("0xaaa", "0xbbb")
            and link.source_wallet != link.target_wallet
        ):
            link_found = True
            break
    assert link_found, "Expected link between 0xaaa and 0xbbb (shared funder)"


def test_detect_co_trading():
    """Detect wallets trading the same tokens."""
    wallets = ["0xaaa", "0xbbb", "0xddd"]
    tx_data = {
        "0xaaa": [{"contractAddress": "0xtoken1", "from": "0xaaa", "to": "0xeee"}],
        "0xbbb": [{"contractAddress": "0xtoken1", "from": "0xbbb", "to": "0xfff"}],
        "0xddd": [{"contractAddress": "0xtoken2", "from": "0xddd", "to": "0xggg"}],
    }
    links = _detect_co_trading(wallets, tx_data)
    assert len(links) >= 1


def test_detect_value_transfers():
    """Detect direct value transfers between cluster wallets."""
    wallets = ["0xaaa", "0xbbb", "0xccc"]
    tx_data = {
        "0xaaa": [
            {
                "from": "0xaaa",
                "to": "0xbbb",
                "value": "10000000000000000000",
                "hash": "0xh1",
            }
        ],
        "0xbbb": [
            {
                "from": "0xbbb",
                "to": "0xccc",
                "value": "5000000000000000000",
                "hash": "0xh2",
            }
        ],
        "0xccc": [],
    }
    links = _detect_value_transfers(wallets, tx_data)
    assert len(links) >= 1
    pair_set = {(link.source_wallet, link.target_wallet) for link in links}
    assert ("0xaaa", "0xbbb") in pair_set or ("0xbbb", "0xaaa") in pair_set


def test_detect_co_deployer():
    """Detect wallets created by the same deployer."""
    wallets = ["0xaaa", "0xbbb", "0xccc"]
    tx_data = {
        "0xaaa": [
            {
                "from": "0xdeployer1",
                "to": "0xaaa",
                "value": "0",
                "hash": "0xh1",
            }
        ],
        "0xbbb": [
            {
                "from": "0xdeployer1",
                "to": "0xbbb",
                "value": "0",
                "hash": "0xh2",
            }
        ],
        "0xccc": [
            {
                "from": "0xdeployer2",
                "to": "0xccc",
                "value": "0",
                "hash": "0xh3",
            }
        ],
    }
    links = _detect_co_deployer(wallets, tx_data)
    assert len(links) >= 1
    link_found = False
    for link in links:
        if (
            link.source_wallet in ("0xaaa", "0xbbb")
            and link.target_wallet in ("0xaaa", "0xbbb")
            and link.source_wallet != link.target_wallet
        ):
            link_found = True
            break
    assert link_found, "Expected link between 0xaaa and 0xbbb (shared deployer)"


# ── Cluster Risk Computation Tests ────────────────────────────────


def test_compute_cluster_risk_no_links():
    """Empty links = zero risk."""
    cluster = InsiderCluster(cluster_id="INSIDER-0001", wallets=["0xaaa", "0xbbb"])
    risk = _compute_cluster_risk(cluster, [])
    assert risk == 0.0


def test_compute_cluster_risk_dense():
    """Dense links produce higher risk."""
    cluster = InsiderCluster(
        cluster_id="INSIDER-0002",
        wallets=["0xaaa", "0xbbb", "0xccc"],
    )
    links = [
        InsiderLink("0xaaa", "0xbbb", "co_deploy", 0.6),
        InsiderLink("0xaaa", "0xccc", "co_deploy", 0.6),
        InsiderLink("0xbbb", "0xccc", "value_transfer", 0.8),
    ]
    risk = _compute_cluster_risk(cluster, links)
    assert risk > 30.0


def test_compute_cluster_risk_single():
    """Single link produces minimal risk."""
    cluster = InsiderCluster(
        cluster_id="INSIDER-0003",
        wallets=["0xaaa", "0xbbb"],
    )
    links = [
        InsiderLink("0xaaa", "0xbbb", "co_trading", 0.4),
    ]
    risk = _compute_cluster_risk(cluster, links)
    assert risk > 0.0
    assert risk < 50.0


# ── Formatting Tests ──────────────────────────────────────────────


def test_format_report_no_clusters():
    """Report with no clusters shows clean message."""
    report = InsiderNetworkReport(target_wallet="0xabc123...def456")
    formatted = format_insider_network_report(report)
    assert "No insider networks detected" in formatted
    assert "INSIDER NETWORK ANALYSIS REPORT" in formatted


def test_format_report_with_clusters():
    """Report with clusters includes cluster details."""
    report = InsiderNetworkReport(target_wallet="0xabc123...def456")
    cluster = InsiderCluster(
        cluster_id="INSIDER-0001",
        wallets=["0xaaa", "0xbbb", "0xccc"],
        member_count=3,
        risk_score=65.0,
        projects_involved=["ProjectA", "ProjectB"],
        risk_factors=["High coordination density"],
    )
    cluster.top_relationships.append(
        {
            "from": "0xaaa",
            "to": "0xbbb",
            "type": "shared_funding",
            "strength": 0.8,
        }
    )
    report.clusters.append(cluster)
    report.total_clusters = 1
    report.total_connected_wallets = 3
    report.highest_risk_score = 65.0

    formatted = format_insider_network_report(report)
    assert "INSIDER-0001" in formatted
    assert "65" in formatted
    assert "ProjectA" in formatted or "ProjectB" in formatted


# ── Integration Tests (mocked) ────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_insider_network_invalid_address():
    """Invalid address raises ValueError."""
    with pytest.raises(ValueError, match="Invalid wallet address"):
        await analyze_insider_network("not-valid")


@pytest.mark.asyncio
async def test_analyze_insider_network_empty_result():
    """Even with mocked empty responses, returns a valid report."""
    with (
        patch("app.insider_network._fetch_wallet_transactions", return_value=[]),
        patch("app.insider_network._fetch_token_transfers", return_value=[]),
    ):
        report = await analyze_insider_network(
            "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            chains=["ethereum"],
        )
        assert isinstance(report, InsiderNetworkReport)
        assert report.target_wallet == "0xdAC17F958D2ee523a2206206994597C13D831ec7"
        assert report.total_connected_wallets == 0
        assert report.total_clusters == 0


# ── InsiderNetworkAnalyzer Tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_analyzer_class():
    """InsiderNetworkAnalyzer wrapper works."""
    with patch("app.insider_network.analyze_insider_network") as mock_fn:
        mock_report = InsiderNetworkReport(target_wallet="0xtest", total_connected_wallets=5)
        mock_fn.return_value = mock_report

        analyzer = InsiderNetworkAnalyzer()
        result = await analyzer.analyze("0xtest")
        assert result.total_connected_wallets == 5
        mock_fn.assert_called_once()


def test_analyzer_format_report():
    """Static format method works."""
    report = InsiderNetworkReport(target_wallet="0xabc")
    result = InsiderNetworkAnalyzer.format_report(report)
    assert "INSIDER NETWORK ANALYSIS REPORT" in result
