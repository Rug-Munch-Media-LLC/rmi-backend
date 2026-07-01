"""
Tests for Social Engineering & Identity Fraud Detector
=======================================================
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.social_engineering_detector import (
    analyze_domain,
    analyze_profile_photo,
    analyze_social_presence,
    analyze_whitepaper_content,
)


class TestDomainAnalysis:
    """Domain analysis tests."""

    def test_no_domain(self):
        result = analyze_domain(None)
        assert result["risk_score"] >= 20
        assert result["has_domain"] is False

    def test_suspicious_tld(self):
        result = analyze_domain("https://example.xyz")
        assert result["risk_score"] >= 15

    def test_clean_domain(self):
        result = analyze_domain("example.com")
        assert result["risk_score"] <= 10

    def test_keyword_stuffed(self):
        result = analyze_domain("crypto-token-finance-swap.xyz")
        # 15 (suspicious TLD) + 10 (keyword match) = 25
        assert result["risk_score"] >= 25

    def test_homoglyph_detection(self):
        result = analyze_domain("app1e-f1n4nc3.io")
        assert result["risk_score"] >= 20


class TestProfilePhoto:
    """Profile photo analysis tests."""

    def test_no_photo(self):
        result = analyze_profile_photo(None, "John Doe")
        assert result["has_photo"] is False
        assert result["risk_score"] == 15

    def test_stock_photo(self):
        result = analyze_profile_photo("https://shutterstock.com/photo/12345", "Alice")
        assert result["risk_score"] >= 25
        assert any("stock" in f.lower() for f in result["flags"])

    def test_ai_generated(self):
        result = analyze_profile_photo("https://thispersondoesnotexist.com/image.jpg", "Bob")
        assert result["risk_score"] >= 40

    def test_normal_photo(self):
        result = analyze_profile_photo("https://media.licdn.com/photo.jpg", "Charlie")
        assert result["has_photo"] is True
        assert result["risk_score"] == 0  # No flags


class TestSocialPresence:
    """Social media presence tests."""

    def test_no_social(self):
        result = analyze_social_presence(None)
        assert result["risk_score"] >= 30
        assert result["platform_count"] == 0

    def test_minimal_social(self):
        result = analyze_social_presence(["https://twitter.com/abc"])
        assert result["risk_score"] >= 25

    def test_complete_social(self):
        result = analyze_social_presence(
            [
                "https://twitter.com/legit_project",
                "https://github.com/legit_project",
                "https://medium.com/@legit_project",
                "https://t.me/legit_project",
            ]
        )
        assert result["risk_score"] <= 15

    def test_short_username(self):
        result = analyze_social_presence(["https://twitter.com/ab"])
        assert any("short" in f.lower() for f in result["flags"])

    def test_private_telegram(self):
        result = analyze_social_presence(
            [
                "https://twitter.com/project",
                "https://t.me/+abc123def456",
            ]
        )
        assert any("private" in f.lower() for f in result["flags"])


class TestWhitepaperContent:
    """Whitepaper/content authenticity tests."""

    def test_no_content(self):
        result = analyze_whitepaper_content(None)
        assert result["risk_score"] >= 20
        assert result["has_content"] is False

    def test_ai_generated(self):
        content = """
        # Introduction

        As an AI language model, I cannot provide specific investment advice.
        I apologize, but as a language model, I don't have access to real-time data.
        This appears to be a revolutionary paradigm shift in the crypto space.

        ## Tokenomics
        [To be announced]

        ## Roadmap
        Q1 2025 - Coming soon

        Lorem ipsum dolor sit amet.
        """
        result = analyze_whitepaper_content(content)
        assert result["risk_score"] >= 40

    def test_clean_whitepaper(self):
        content = """
        # Project Overview

        We are building a decentralized exchange for cross-chain swaps.
        Our platform uses atomic swaps to enable trustless trading
        between Ethereum, Solana, and BSC.

        ## Token Distribution
        - 40% Public Sale
        - 20% Team (vested 2 years)
        - 20% Development Fund
        - 20% Ecosystem Growth

        ## Roadmap
        Q1 2025: Mainnet launch
        Q2 2025: Mobile app release
        """
        result = analyze_whitepaper_content(content)
        assert result["has_content"] is True
        # Should still flag some things (generic roadmap) but not extreme
        assert result["risk_score"] < 60

    def test_hype_keywords(self):
        content = (
            "This revolutionary game-changing paradigm shift will moon and lambo. "
            "Wen ser? Based. NGMI if you sleep on this revolutionary next-gen project. "
            "This is the most revolutionary project ever created. Game-changing technology. "
            "A paradigm shift in DeFi. Moon soon. Lambo by EOY. Wen listing? Ser, please. "
            "This is based. Don't NGMI this opportunity. Revolutionary innovation. "
            "Game-changing approach to decentralized finance. Next-gen protocol. "
            "Moon or bust. Lambo or nothing. Wen airdrop? Ser, check the docs. "
            "Based team. NGMI if you fade. Revolutionary technology stack. "
        )
        result = analyze_whitepaper_content(content)
        assert any("hype" in f.lower() for f in result["flags"])
