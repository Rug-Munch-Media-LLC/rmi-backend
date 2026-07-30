"""
Tests for portfolio_risk_aggregator.py
"""

import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.portfolio_risk_aggregator import (
    ChainPortfolio,
    PortfolioRiskAggregator,
    PortfolioRiskProfile,
    RiskLevel,
    TokenHolding,
    TokenRiskScorer,
)


def test_risk_level_from_score():
    """Test RiskLevel classification."""
    assert RiskLevel.from_score(90) == RiskLevel.SAFE
    assert RiskLevel.from_score(75) == RiskLevel.LOW
    assert RiskLevel.from_score(50) == RiskLevel.MEDIUM
    assert RiskLevel.from_score(30) == RiskLevel.HIGH
    assert RiskLevel.from_score(10) == RiskLevel.CRITICAL
    assert RiskLevel.from_score(0) == RiskLevel.CRITICAL
    assert RiskLevel.from_score(100) == RiskLevel.SAFE
    assert RiskLevel.from_score(80) == RiskLevel.SAFE
    assert RiskLevel.from_score(60) == RiskLevel.LOW
    assert RiskLevel.from_score(40) == RiskLevel.MEDIUM
    assert RiskLevel.from_score(20) == RiskLevel.HIGH


def test_token_holding_risk_level():
    """Test TokenHolding risk level computation."""
    safe = TokenHolding(
        chain="ethereum",
        symbol="ETH",
        name="Ether",
        contract_address="0x0000000000000000000000000000000000000000",
        balance_raw=0,
        balance_formatted=1.0,
        decimals=18,
        risk_score=90,
    )
    assert safe.risk_level() == RiskLevel.SAFE

    critical = TokenHolding(
        chain="bsc",
        symbol="SCAM",
        name="Scam Token",
        contract_address="0x1234",
        balance_raw=100,
        balance_formatted=100,
        decimals=18,
        risk_score=10,
        risk_flags=["KNOWN_SCAM_TOKEN"],
    )
    assert critical.risk_level() == RiskLevel.CRITICAL
    assert "KNOWN_SCAM_TOKEN" in critical.risk_flags


def test_token_holding_to_dict():
    """Test TokenHolding serialization."""
    t = TokenHolding(
        chain="base",
        symbol="USDC",
        name="USD Coin",
        contract_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        balance_raw=1000000,
        balance_formatted=1.0,
        decimals=6,
        estimated_usd_value=1.0,
        risk_score=95,
    )
    d = t.to_dict()
    assert d["chain"] == "base"
    assert d["symbol"] == "USDC"
    assert d["risk_score"] == 95
    assert d["estimated_usd_value"] == 1.0


def test_chain_portfolio_stats():
    """Test ChainPortfolio computed stats."""
    cp = ChainPortfolio(chain="ethereum")
    cp.holdings = [
        TokenHolding(
            chain="ethereum",
            symbol="ETH",
            name="",
            contract_address="0x0",
            balance_raw=0,
            balance_formatted=1.0,
            decimals=18,
            estimated_usd_value=2800,
            risk_score=90,
        ),
        TokenHolding(
            chain="ethereum",
            symbol="USDC",
            name="",
            contract_address="0xA0b8",
            balance_raw=1000000,
            balance_formatted=1.0,
            decimals=6,
            estimated_usd_value=1.0,
            risk_score=95,
        ),
        TokenHolding(
            chain="ethereum",
            symbol="SHIT",
            name="",
            contract_address="0x1234",
            balance_raw=100,
            balance_formatted=100.0,
            decimals=18,
            estimated_usd_value=50,
            risk_score=15,
            risk_flags=["KNOWN_SCAM_TOKEN"],
        ),
    ]
    cp.token_count = len(cp.holdings)
    cp.total_value_usd = sum(h.estimated_usd_value for h in cp.holdings)
    cp.avg_risk_score = sum(h.risk_score for h in cp.holdings) / len(cp.holdings)
    cp.high_risk_count = len([h for h in cp.holdings if h.risk_score < 40])
    cp.critical_risk_count = len([h for h in cp.holdings if h.risk_score < 20])

    assert cp.token_count == 3
    assert cp.total_value_usd == 2851.0
    assert 60 <= cp.avg_risk_score <= 70
    assert cp.high_risk_count == 1
    assert cp.critical_risk_count == 1

    d = cp.to_dict()
    assert d["chain"] == "ethereum"
    assert d["total_value_usd"] == 2851.0
    assert d["high_risk_count"] == 1


def test_portfolio_risk_profile():
    """Test PortfolioRiskProfile computations."""
    profile = PortfolioRiskProfile(
        wallet_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        chains_scanned=["ethereum", "base", "solana"],
    )

    # Add chain portfolios
    eth_cp = ChainPortfolio(chain="ethereum")
    eth_cp.holdings = [
        TokenHolding(
            chain="ethereum",
            symbol="ETH",
            name="",
            contract_address="0x0",
            balance_raw=0,
            balance_formatted=10.0,
            decimals=18,
            estimated_usd_value=28000,
            risk_score=90,
        ),
    ]
    eth_cp.total_value_usd = 28000
    eth_cp.token_count = 1
    eth_cp.avg_risk_score = 90

    base_cp = ChainPortfolio(chain="base")
    base_cp.holdings = [
        TokenHolding(
            chain="base",
            symbol="SCAM",
            name="",
            contract_address="0xdead",
            balance_raw=1000,
            balance_formatted=1000,
            decimals=18,
            estimated_usd_value=5000,
            risk_score=10,
            risk_flags=["KNOWN_SCAM_TOKEN"],
        ),
    ]
    base_cp.total_value_usd = 5000
    base_cp.token_count = 1
    base_cp.avg_risk_score = 10
    base_cp.critical_risk_count = 1

    profile.chain_portfolios = {"ethereum": eth_cp, "base": base_cp}
    profile.total_value_usd = 33000
    profile.total_tokens = 2
    profile.chain_count = 2

    # Test concentration
    profile.concentration_risk_pct = (28000 / 33000) * 100  # ~84.8%

    # Test report generation
    text_report = profile.report(format="text")
    assert profile.wallet_address[:12] in text_report
    assert "ETH" in text_report or "ethereum" in text_report
    assert "SCAM" in text_report or "base" in text_report

    # Test JSON report
    json_report = profile.report(format="json")
    data = json.loads(json_report)
    assert data["wallet_address"] == profile.wallet_address
    assert data["total_value_usd"] == 33000
    assert data["overall_risk_level"] in ["safe", "low", "medium", "high", "critical"]

    # Test to_dict
    d = profile.to_dict()
    assert "wallet_address" in d
    assert "chain_portfolios" in d
    assert "timestamp" in d


def test_token_risk_scorer_heuristic():
    """Test TokenRiskScorer heuristic scoring."""
    scorer = TokenRiskScorer()

    # Known good token
    result = scorer._heuristic_score("ethereum", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "USDC")
    assert result["risk_score"] == 90
    assert result["reason"] == "Known token"

    # Unknown token with scam name
    result = scorer._heuristic_score("bsc", "0x1234dead5678", "SAFEMOONELON")
    assert result["risk_score"] <= 35
    assert any("SCAM_INDICATOR" in f for f in result.get("risk_flags", []))

    # Unknown neutral token
    result = scorer._heuristic_score("base", "0xabcd1234", "FOO")
    assert result["risk_score"] == 30  # 50 - 20 for non-standard address
    assert "NON_STANDARD_ADDRESS" in result.get("risk_flags", [])


def test_validate_wallet_address():
    """Test wallet address validation."""
    from app.portfolio_risk_aggregator import validate_wallet_address

    # Valid EVM
    valid, hint = validate_wallet_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
    assert valid
    assert hint == "evm"

    valid, hint = validate_wallet_address("0x0000000000000000000000000000000000000000")
    assert valid
    assert hint == "evm"

    # Valid Solana
    valid, hint = validate_wallet_address("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV")
    assert valid
    assert hint == "solana"

    # Invalid addresses
    valid, _ = validate_wallet_address("not_an_address")
    assert not valid

    valid, _ = validate_wallet_address("0xshort")
    assert not valid

    valid, _ = validate_wallet_address("")
    assert not valid

    # Lenient EVM (mixed case OK)
    valid, hint = validate_wallet_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44E")
    assert valid


async def test_price_oracle_defaults():
    """Test PriceOracle has sensible defaults."""
    from app.portfolio_risk_aggregator import PriceOracle

    oracle = PriceOracle()
    assert await oracle.get_price("ETH") == 2800.0
    assert await oracle.get_price("SOL") == 145.0
    assert await oracle.get_price("USDC") == 1.0
    assert oracle.estimate_native_price("ethereum") == 2800.0
    assert oracle.estimate_native_price("solana") == 145.0
    assert oracle.estimate_native_price("bsc") == 580.0


def test_aggregator_chains():
    """Test PortfolioRiskAggregator chain configuration."""
    agg = PortfolioRiskAggregator(chains=["ethereum", "base"])
    assert "ethereum" in agg.chains
    assert "base" in agg.chains
    assert "solana" not in agg.chains


def test_aggregator_initialization():
    """Test default aggregator initialization."""
    agg = PortfolioRiskAggregator()
    assert len(agg.chains) >= 7
    assert "ethereum" in agg.chains
    assert "solana" in agg.chains
    assert "base" in agg.chains


def test_health_score_calculation():
    """Test the _calculate_health_score method."""
    agg = PortfolioRiskAggregator(chains=["ethereum", "base"])

    profile = PortfolioRiskProfile(
        wallet_address="0x1234",
        chains_scanned=["ethereum", "base"],
    )

    # Perfect portfolio
    eth_cp = ChainPortfolio(chain="ethereum")
    eth_cp.holdings = [
        TokenHolding(
            chain="ethereum",
            symbol="ETH",
            name="",
            contract_address="0x0",
            balance_raw=0,
            balance_formatted=1.0,
            decimals=18,
            estimated_usd_value=2800,
            risk_score=95,
        ),
    ]
    eth_cp.total_value_usd = 2800
    eth_cp.token_count = 1

    base_cp = ChainPortfolio(chain="base")
    base_cp.holdings = [
        TokenHolding(
            chain="base",
            symbol="USDC",
            name="",
            contract_address="0xabc",
            balance_raw=1000000,
            balance_formatted=1000,
            decimals=6,
            estimated_usd_value=1000,
            risk_score=95,
        ),
    ]
    base_cp.total_value_usd = 1000
    base_cp.token_count = 1

    profile.chain_portfolios = {"ethereum": eth_cp, "base": base_cp}
    profile.total_value_usd = 3800
    profile.total_tokens = 2
    profile.concentration_risk_pct = (2800 / 3800) * 100  # ~73.7%

    health = agg._calculate_health_score(profile)
    assert 70 <= health <= 100  # Should be fairly healthy

    # Risky portfolio
    profile2 = PortfolioRiskProfile(
        wallet_address="0x5678",
        chains_scanned=["ethereum"],
    )
    risky_cp = ChainPortfolio(chain="ethereum")
    risky_cp.holdings = [
        TokenHolding(
            chain="ethereum",
            symbol="SCAM",
            name="",
            contract_address="0xdead",
            balance_raw=1000,
            balance_formatted=1000,
            decimals=18,
            estimated_usd_value=100,
            risk_score=5,
            risk_flags=["KNOWN_SCAM_TOKEN"],
        ),
    ]
    risky_cp.total_value_usd = 100
    risky_cp.token_count = 1

    profile2.chain_portfolios = {"ethereum": risky_cp}
    profile2.total_value_usd = 100
    profile2.total_tokens = 1
    profile2.concentration_risk_pct = 100.0

    health2 = agg._calculate_health_score(profile2)
    assert health2 < 30  # Should be very low


def test_findings_generation():
    """Test finding generation logic."""
    agg = PortfolioRiskAggregator(chains=["ethereum"])

    # Portfolio with critical risk
    profile = PortfolioRiskProfile(
        wallet_address="0x1234",
        chains_scanned=["ethereum"],
    )
    cp = ChainPortfolio(chain="ethereum")
    cp.holdings = [
        TokenHolding(
            chain="ethereum",
            symbol="SCAM",
            name="",
            contract_address="0xdead",
            balance_raw=100,
            balance_formatted=100,
            decimals=18,
            estimated_usd_value=50,
            risk_score=5,
            risk_flags=["HONEYPOT_DETECTED"],
        ),
    ]
    cp.total_value_usd = 50
    cp.token_count = 1
    profile.chain_portfolios = {"ethereum": cp}
    profile.total_value_usd = 50
    profile.total_tokens = 1
    profile.concentration_risk_pct = 100.0

    findings = agg._generate_findings(profile)
    assert len(findings) > 0
    assert any("CRITICAL" in f for f in findings)
    assert any("HONEYPOT" in f or "scam" in f.lower() for f in findings)

    # Clean portfolio
    profile2 = PortfolioRiskProfile(
        wallet_address="0x5678",
        chains_scanned=["ethereum", "base"],
    )
    clean_cp = ChainPortfolio(chain="ethereum")
    clean_cp.holdings = [
        TokenHolding(
            chain="ethereum",
            symbol="ETH",
            name="",
            contract_address="0x0",
            balance_raw=0,
            balance_formatted=10,
            decimals=18,
            estimated_usd_value=28000,
            risk_score=90,
        ),
    ]
    clean_cp.total_value_usd = 28000
    clean_cp.token_count = 1
    clean_cp.avg_risk_score = 90

    base_cp = ChainPortfolio(chain="base")
    base_cp.holdings = [
        TokenHolding(
            chain="base",
            symbol="USDC",
            name="",
            contract_address="0xabc",
            balance_raw=5000000,
            balance_formatted=5,
            decimals=6,
            estimated_usd_value=5,
            risk_score=95,
        ),
    ]
    base_cp.total_value_usd = 5
    base_cp.token_count = 1
    base_cp.avg_risk_score = 95

    profile2.chain_portfolios = {"ethereum": clean_cp, "base": base_cp}
    profile2.chain_count = 2
    profile2.total_value_usd = 28005
    profile2.total_tokens = 2
    profile2.overall_health_score = 85
    profile2.concentration_risk_pct = 99.98

    findings2 = agg._generate_findings(profile2)
    assert any("clean" in f.lower() for f in findings2)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
