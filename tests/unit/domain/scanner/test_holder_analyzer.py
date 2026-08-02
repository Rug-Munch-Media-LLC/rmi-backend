"""Unit tests for app/domains/scanners/holder_analyzer.py.

Tests holder fetching (DexScreener/Birdeye/Solscan), Herfindahl-Hirschman
Index (HHI) calculation, risk classification, fake diversification
clustering (funding + creation time), empty holder handling, and the full
analyze() pipeline. Every external API call is mocked.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domains.scanners.holder_analyzer import (
    HHIReport,
    HolderAnalyzer,
    HolderInfo,
)

HOUR_MS = 3600_000


def _holder(address: str, balance: float, percentage: float, **kwargs) -> HolderInfo:
    """Build a HolderInfo with sensible defaults for tests."""
    return HolderInfo(address=address, balance=balance, percentage=percentage, **kwargs)


class TestCalculateHHI:
    """HHI calculation for concentration measurement."""

    def test_calculate_hhi_single_holder_is_10000(self) -> None:
        """A single holder owning everything should give HHI 10000."""
        analyzer = HolderAnalyzer()
        holders = [_holder("0xaaa", 1000, 100.0)]
        assert analyzer.calculate_hhi(holders) == 10000.0

    def test_calculate_hhi_equal_distribution(self) -> None:
        """Two equal holders should give HHI 5000 (monopoly threshold)."""
        analyzer = HolderAnalyzer()
        holders = [_holder("0xaaa", 100, 50.0), _holder("0xbbb", 100, 50.0)]
        assert analyzer.calculate_hhi(holders) == 5000.0

    def test_calculate_hhi_well_distributed_is_low(self) -> None:
        """100 equal holders should give an HHI near the low end."""
        analyzer = HolderAnalyzer()
        holders = [_holder(f"0x{i:040x}", 1, 1.0) for i in range(100)]
        hhi = analyzer.calculate_hhi(holders)
        assert hhi < 1500

    def test_calculate_hhi_empty_holders_is_10000(self) -> None:
        """No holder data should be treated as maximum concentration."""
        analyzer = HolderAnalyzer()
        assert analyzer.calculate_hhi([]) == 10000.0

    def test_calculate_hhi_zero_supply_is_10000(self) -> None:
        """Zero total supply should be treated as maximum concentration."""
        analyzer = HolderAnalyzer()
        holders = [_holder("0xaaa", 0, 0.0), _holder("0xbbb", 0, 0.0)]
        assert analyzer.calculate_hhi(holders) == 10000.0

    def test_calculate_hhi_uses_balance_not_percentage(self) -> None:
        """HHI is computed from raw balances, not pre-computed percentages."""
        analyzer = HolderAnalyzer()
        holders = [_holder("0xaaa", 900, 10.0), _holder("0xbbb", 100, 90.0)]
        hhi = analyzer.calculate_hhi(holders)
        # 0.9^2 + 0.1^2 = 0.82 -> 8200
        assert hhi == 8200.0


class TestClassifyHHI:
    """Risk-level classification of HHI scores."""

    @pytest.mark.parametrize(
        ("hhi", "expected"),
        [
            (0.0, "low"),
            (1499.99, "low"),
            (1500.0, "moderate"),
            (2999.99, "moderate"),
            (3000.0, "high"),
            (4999.99, "high"),
            (5000.0, "critical"),
            (10000.0, "critical"),
        ],
    )
    def test_classify_hhi_boundaries(self, hhi: float, expected: str) -> None:
        """Boundary values should map to the documented risk buckets."""
        analyzer = HolderAnalyzer()
        assert analyzer.classify_hhi(hhi) == expected


class TestFakeDiversification:
    """Clustering of wallets that appear independent but are not."""

    def test_detect_funding_cluster(self) -> None:
        """Wallets funded from the same source should cluster together."""
        analyzer = HolderAnalyzer()
        holders = [
            _holder("0xaaa", 100, 30.0, funding_source="0xfund"),
            _holder("0xbbb", 100, 30.0, funding_source="0xfund"),
            _holder("0xccc", 100, 40.0, funding_source="0xother"),
        ]
        clusters = analyzer.detect_fake_diversification(holders)
        funding_clusters = [c for c in clusters if c.cluster_type == "funding"]
        assert len(funding_clusters) == 1
        assert set(funding_clusters[0].wallets) == {"0xaaa", "0xbbb"}
        assert funding_clusters[0].combined_percentage == 60.0
        assert funding_clusters[0].confidence > 0.5

    def test_detect_single_funded_wallet_no_cluster(self) -> None:
        """A lone wallet with a funding source should not form a cluster."""
        analyzer = HolderAnalyzer()
        holders = [_holder("0xaaa", 100, 100.0, funding_source="0xfund")]
        assert analyzer.detect_fake_diversification(holders) == []

    def test_detect_creation_time_cluster(self) -> None:
        """3+ wallets created within one hour should cluster (creation)."""
        analyzer = HolderAnalyzer()
        base = 1_700_000_000_000
        holders = [
            _holder("0xaaa", 100, 30.0, first_tx_timestamp=base),
            _holder("0xbbb", 100, 30.0, first_tx_timestamp=base + 10 * 60 * 1000),
            _holder("0xccc", 100, 30.0, first_tx_timestamp=base + 20 * 60 * 1000),
            _holder("0xddd", 100, 10.0, first_tx_timestamp=base + 48 * HOUR_MS),
        ]
        clusters = analyzer.detect_fake_diversification(holders)
        creation_clusters = [c for c in clusters if c.cluster_type == "creation"]
        assert len(creation_clusters) == 1
        assert set(creation_clusters[0].wallets) == {"0xaaa", "0xbbb", "0xccc"}
        assert creation_clusters[0].confidence == 0.7

    def test_detect_creation_time_requires_three(self) -> None:
        """Two wallets created together should NOT form a creation cluster."""
        analyzer = HolderAnalyzer()
        base = 1_700_000_000_000
        holders = [
            _holder("0xaaa", 100, 50.0, first_tx_timestamp=base),
            _holder("0xbbb", 100, 50.0, first_tx_timestamp=base + 60_000),
        ]
        assert analyzer.detect_fake_diversification(holders) == []

    def test_merge_overlapping_funding_and_creation(self) -> None:
        """Overlapping funding + creation clusters should merge into one."""
        analyzer = HolderAnalyzer()
        base = 1_700_000_000_000
        holders = [
            _holder("0xaaa", 100, 30.0, funding_source="0xfund", first_tx_timestamp=base),
            _holder("0xbbb", 100, 30.0, funding_source="0xfund", first_tx_timestamp=base + 60_000),
            _holder("0xccc", 100, 30.0, funding_source="0xother", first_tx_timestamp=base + 120_000),
        ]
        clusters = analyzer.detect_fake_diversification(holders)
        # Funding cluster {aaa,bbb} + creation cluster {aaa,bbb,ccc} share aaa/bbb -> merged
        assert len(clusters) == 1
        assert set(clusters[0].wallets) == {"0xaaa", "0xbbb", "0xccc"}


class TestFetchHolderData:
    """Holder aggregation from Solscan/Birdeye/DexScreener (all mocked)."""

    @pytest.mark.asyncio
    async def test_fetch_holder_data_solana_solscan(self) -> None:
        """Solana path should map Solscan holder rows into HolderInfo objects."""
        analyzer = HolderAnalyzer()
        raw_holders = [
            {
                "address": "SolAddr1",
                "amount": 100,
                "pct": 10.0,
                "block_time": 1_700_000_000_000,
                "funding_source": "0xfunder",
                "is_contract": False,
            },
            {"address": "SolAddr2", "amount": 900, "pct": 90.0},
        ]
        with (
            patch.object(analyzer, "_fetch_solscan_holders", AsyncMock(return_value=raw_holders)),
            patch.object(analyzer, "_fetch_birdeye_overview", AsyncMock(return_value={})),
            patch("app.free_solscan_client.is_known_exchange", return_value=None),
        ):
            holders = await analyzer._fetch_holder_data("SolMint", "solana")
        assert len(holders) == 2
        assert holders[0].address == "SolAddr1"
        assert holders[0].percentage == 10.0
        assert holders[0].funding_source == "0xfunder"
        assert holders[1].percentage == 90.0

    @pytest.mark.asyncio
    async def test_fetch_holder_data_solana_birdeye_fallback(self) -> None:
        """When Solscan is empty, Birdeye holderDistribution should be used."""
        analyzer = HolderAnalyzer()
        birdeye_data = {
            "totalSupply": 1000,
            "holderDistribution": [
                {"address": "SolAddrA", "amount": 400},
                {"address": "SolAddrB", "amount": 600},
            ],
        }
        with (
            patch.object(analyzer, "_fetch_solscan_holders", AsyncMock(return_value=[])),
            patch.object(analyzer, "_fetch_birdeye_overview", AsyncMock(return_value=birdeye_data)),
            patch("app.free_solscan_client.is_known_exchange", return_value=None),
        ):
            holders = await analyzer._fetch_holder_data("SolMint", "solana")
        assert len(holders) == 2
        percentages = {h.address: h.percentage for h in holders}
        assert percentages["SolAddrA"] == pytest.approx(40.0)
        assert percentages["SolAddrB"] == pytest.approx(60.0)

    @pytest.mark.asyncio
    async def test_fetch_holder_data_evm_birdeye(self) -> None:
        """EVM path should use Birdeye distribution when DexScreener has none."""
        analyzer = HolderAnalyzer()
        birdeye_data = {
            "totalSupply": 2000,
            "holderDistribution": [
                {"address": "0xAAA", "amount": 1500},
                {"address": "0xBBB", "amount": 500},
            ],
        }
        with (
            patch.object(analyzer, "_fetch_dexscreener_token", AsyncMock(return_value=None)),
            patch.object(analyzer, "_fetch_birdeye_overview", AsyncMock(return_value=birdeye_data)),
        ):
            holders = await analyzer._fetch_holder_data("0xtoken", "ethereum")
        assert len(holders) == 2
        percentages = {h.address: h.percentage for h in holders}
        assert percentages["0xAAA"] == pytest.approx(75.0)
        assert percentages["0xBBB"] == pytest.approx(25.0)

    @pytest.mark.asyncio
    async def test_fetch_holder_data_empty(self) -> None:
        """All sources returning nothing should yield an empty holder list."""
        analyzer = HolderAnalyzer()
        with (
            patch.object(analyzer, "_fetch_solscan_holders", AsyncMock(return_value=[])),
            patch.object(analyzer, "_fetch_birdeye_overview", AsyncMock(return_value=None)),
            patch("app.free_solscan_client.is_known_exchange", return_value=None),
        ):
            holders = await analyzer._fetch_holder_data("SolMint", "solana")
        assert holders == []

    @pytest.mark.asyncio
    async def test_fetch_dexscreener_failure_graceful(self) -> None:
        """DexScreener HTTP failures should return None, not raise."""
        analyzer = HolderAnalyzer()
        with patch.object(analyzer._http, "get", AsyncMock(side_effect=httpx.HTTPError("network down"))):
            result = await analyzer._fetch_dexscreener_token("0xtoken")
        assert result is None


class TestAnalyze:
    """Full analyze() pipeline with mocked data sources."""

    @pytest.mark.asyncio
    async def test_analyze_empty_holders(self) -> None:
        """No holder data should produce a critical HHI report with a warning."""
        analyzer = HolderAnalyzer()
        with patch.object(analyzer, "_fetch_holder_data", AsyncMock(return_value=[])):
            report = await analyzer.analyze("0xtoken", "ethereum")
        assert isinstance(report, HHIReport)
        assert report.total_holders == 0
        assert report.hhi == 10000.0
        assert report.risk_level == "critical"
        assert any("No holder data" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_analyze_concentrated_holders(self) -> None:
        """A 90/10 split should yield critical HHI and 100% top-10 share."""
        analyzer = HolderAnalyzer()
        holders = [_holder("0xaaa", 900, 90.0), _holder("0xbbb", 100, 10.0)]
        with patch.object(analyzer, "_fetch_holder_data", AsyncMock(return_value=holders)):
            report = await analyzer.analyze("0xtoken", "ethereum")
        assert report.hhi == 8200.0
        assert report.risk_level == "critical"
        assert report.top_10_percentage == 100.0
        assert report.top_20_percentage == 100.0
        assert report.top_50_percentage == 100.0
        assert any("CRITICAL" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_analyze_exchange_funded_percentage(self) -> None:
        """Exchange-funded wallets should be aggregated into the report."""
        analyzer = HolderAnalyzer()
        holders = [
            _holder("0xaaa", 100, 60.0, is_exchange=True),
            _holder("0xbbb", 100, 40.0),
        ]
        with patch.object(analyzer, "_fetch_holder_data", AsyncMock(return_value=holders)):
            report = await analyzer.analyze("0xtoken", "ethereum")
        assert report.exchange_funded_percentage == 60.0

    @pytest.mark.asyncio
    async def test_analyze_new_wallets_percentage(self) -> None:
        """Wallets created within the last 7 days should be flagged as new."""
        analyzer = HolderAnalyzer()
        now_ms = int(time.time() * 1000)
        recent = now_ms - 24 * 3600 * 1000
        old = now_ms - 30 * 24 * 3600 * 1000
        holders = [
            _holder("0xnew", 100, 80.0, first_tx_timestamp=recent),
            _holder("0xold", 100, 20.0, first_tx_timestamp=old),
        ]
        with patch.object(analyzer, "_fetch_holder_data", AsyncMock(return_value=holders)):
            report = await analyzer.analyze("0xtoken", "ethereum")
        assert report.new_wallets_percentage == 80.0
        assert any("NEW WALLETS" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_analyze_fake_diversification_score(self) -> None:
        """Clustered wallets should push the fake diversification score up."""
        analyzer = HolderAnalyzer()
        holders = [
            _holder("0xaaa", 100, 40.0, funding_source="0xfunder"),
            _holder("0xbbb", 100, 40.0, funding_source="0xfunder"),
            _holder("0xccc", 100, 20.0),
        ]
        with patch.object(analyzer, "_fetch_holder_data", AsyncMock(return_value=holders)):
            report = await analyzer.analyze("0xtoken", "ethereum")
        assert report.fake_diversification_score > 0
        assert any("FAKE DIVERSIFICATION" in w for w in report.warnings)


if __name__ == "__main__":
    pytest.main([__file__])
