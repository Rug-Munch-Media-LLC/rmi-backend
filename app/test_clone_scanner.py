"""
Tests for Token Clone Scanner (clone_scanner.py)

Tests the core logic: string similarity, name similarity scoring,
risk labeling, data models, and fast_check surrogate.
Network tests are skipped in offline mode.
"""

import os
import sys
import unittest

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.clone_scanner import (
    CloneReport,
    CloneScanner,
    SimilarToken,
    name_similarity_score,
    string_similarity,
)


class TestStringHelpers(unittest.TestCase):
    """Core string similarity utilities."""

    def test_identical_strings(self):
        assert string_similarity("Pepe", "Pepe") == 1.0

    def test_case_insensitive(self):
        self.assertAlmostEqual(string_similarity("Pepe", "pepe"), 1.0)

    def test_completely_different(self):
        self.assertAlmostEqual(string_similarity("Pepe", "Bitcoin"), 0.0, places=1)

    def test_partial_match(self):
        sim = string_similarity("Pepe", "Pepe 2.0")
        assert sim > 0.3
        assert sim < 1.0

    def test_empty_strings(self):
        assert string_similarity("", "Pepe") == 0.0
        assert string_similarity("Pepe", "") == 0.0
        assert string_similarity("", "") == 0.0


class TestNameSimilarity(unittest.TestCase):
    """Name similarity scoring with clone patterns."""

    def test_identical_names(self):
        assert name_similarity_score("Pepe", "Pepe") == 1.0

    def test_prefix_clone(self):
        """'Baby Pepe' should score high vs 'Pepe'."""
        score = name_similarity_score("Baby Pepe", "Pepe")
        assert score >= 0.65

    def test_suffix_clone(self):
        """'Pepe v2' should score very high vs 'Pepe'."""
        score = name_similarity_score("Pepe v2", "Pepe")
        assert score >= 0.85

    def test_known_v2_pattern(self):
        score = name_similarity_score("SafeMoon", "SafeMoon 2.0")
        assert score >= 0.85

    def test_different_tokens(self):
        score = name_similarity_score("Bitcoin", "Pepe")
        assert score < 0.3


class TestCloneReport(unittest.TestCase):
    """CloneReport data model and helpers."""

    def test_to_dict_structure(self):
        report = CloneReport(
            token_address="0xabc123",
            chain="ethereum",
            name="TestToken",
            symbol="TEST",
            clone_score=45.5,
            risk_label="medium",
            similar_tokens=[
                SimilarToken(
                    address="0xdef456",
                    chain="ethereum",
                    name="TestToken Clone",
                    symbol="TSTC",
                    name_similarity=0.85,
                    symbol_similarity=0.6,
                )
            ],
            matched_keywords=["pepe"],
            unverified_contract=True,
            same_deployer_other_tokens=12,
        )
        d = report.to_dict()
        assert d["token_address"] == "0xabc123"
        assert d["clone_score"] == 45.5
        assert d["risk_label"] == "medium"
        assert len(d["similar_tokens"]) == 1
        assert d["matched_keywords"] == ["pepe"]
        assert d["unverified_contract"]
        assert d["same_deployer_other_tokens"] == 12

        # Check signals sub-dict
        assert "bytecode_similarity" in d["signals"]
        assert "name_similarity" in d["signals"]
        assert "deployer_risk" in d["signals"]
        assert "metadata_risk" in d["signals"]

    def test_summary_format(self):
        report = CloneReport(
            token_address="0xabc123def4567890123456789012345678901234",
            chain="ethereum",
            name="ShadyToken",
            symbol="SHADY",
            clone_score=72.3,
            risk_label="high",
            similar_tokens=[
                SimilarToken(
                    address="0xdead",
                    chain="ethereum",
                    name="RealToken",
                    symbol="REAL",
                    name_similarity=0.9,
                )
            ],
            matched_keywords=["pepe", "moon"],
            unverified_contract=True,
        )
        summary = report.summary()
        assert "HIGH" in summary
        assert "72" in summary
        assert "ShadyToken" in summary
        assert "1 similar tokens" in summary

    def test_risk_label_none(self):
        report = CloneReport(token_address="0xaaa", chain="ethereum", clone_score=0.0)
        scanner = CloneScanner()
        report.risk_label = scanner._label_risk(0.0)
        assert report.risk_label == "none"

    def test_risk_label_critical(self):
        report = CloneReport(token_address="0xaaa", chain="ethereum", clone_score=85.0)
        scanner = CloneScanner()
        report.risk_label = scanner._label_risk(85.0)
        assert report.risk_label == "critical"

    def test_risk_label_brackets(self):
        report = CloneReport(token_address="0xaaa", chain="ethereum", clone_score=25.0)
        scanner = CloneScanner()
        report.risk_label = scanner._label_risk(25.0)
        assert report.risk_label == "medium"


class TestSimilarToken(unittest.TestCase):
    """SimilarToken helper properties."""

    def test_overall_similarity(self):
        t = SimilarToken(
            address="0xabc",
            chain="ethereum",
            name="TokenA",
            symbol="TKA",
            name_similarity=0.9,
            symbol_similarity=0.5,
        )
        assert t.overall_similarity == 0.9

    def test_overall_uses_symbol_when_higher(self):
        t = SimilarToken(
            address="0xabc",
            chain="ethereum",
            name="TokenA",
            symbol="TKA",
            name_similarity=0.4,
            symbol_similarity=0.95,
        )
        assert t.overall_similarity == 0.95


class TestScannerScoring(unittest.TestCase):
    """Unit tests for scoring methods (no network)."""

    def setUp(self):
        self.scanner = CloneScanner()

    def test_score_bytecode_unverified(self):
        score = self.scanner._score_bytecode_risk(True, {})
        assert score == 40  # 40 for unverified

    def test_score_bytecode_verified(self):
        score = self.scanner._score_bytecode_risk(False, {"other_unverified": 0})
        assert score == 0.0

    def test_score_bytecode_mass_deployer(self):
        score = self.scanner._score_bytecode_risk(
            True, {"other_unverified": 10, "other_tokens": 30}
        )
        # 40 (unverified) + 20 (many unverified) + 15 (mass deployer) = 75
        assert score == 75

    def test_score_name_no_keywords(self):
        score = self.scanner._score_name_similarity([], [])
        assert score == 0.0

    def test_score_name_with_keywords(self):
        score = self.scanner._score_name_similarity([], ["pepe", "moon"])
        assert score == 25

    def test_score_name_with_high_similarity(self):
        t = SimilarToken("0x1", "eth", "Clone", "CLO", name_similarity=0.95)
        score = self.scanner._score_name_similarity([t], [])
        # 1 high-sim match = 20
        assert score == 20

    def test_score_deployer_low(self):
        score = self.scanner._score_deployer_risk({"other_tokens": 2})
        assert score == 0.0

    def test_score_deployer_medium(self):
        score = self.scanner._score_deployer_risk({"other_tokens": 10})
        assert score == 15

    def test_score_deployer_high(self):
        score = self.scanner._score_deployer_risk({"other_tokens": 30})
        assert score == 30

    def test_score_deployer_critical(self):
        score = self.scanner._score_deployer_risk({"other_tokens": 100})
        assert score == 40

    def test_score_deployer_clone_flag(self):
        score = self.scanner._score_deployer_risk({"other_tokens": 5, "clone_deployer": True})
        # 5 other tokens = no bonus (needs >20), +30 clone flag = 30
        assert score == 30

    def test_score_metadata_missing(self):
        score = self.scanner._score_metadata_risk({}, [], [])
        # Empty dict = no name or symbol = 20+20 = 40
        assert score == 40

    def test_score_metadata_with_social(self):
        score = self.scanner._score_metadata_risk(
            {"name": "T", "symbol": "S", "social": {"twitter": "@x"}}, [], []
        )
        assert score == 10  # missing website

    def test_score_metadata_full(self):
        score = self.scanner._score_metadata_risk(
            {
                "name": "Test",
                "symbol": "TST",
                "social": {"twitter": "@x", "telegram": "@g"},
                "website": "https://t.com",
            },
            [],
            [],
        )
        assert score == 0.0

    def test_compute_composite_score(self):
        report = CloneReport(
            token_address="0x1",
            chain="eth",
            bytecode_similarity_score=100,
            name_similarity_score=50,
            deployer_risk_score=0,
            metadata_risk_score=0,
        )
        # 0.25*100 + 0.35*50 + 0.25*0 + 0.15*0 = 25 + 17.5 = 42.5
        assert self.scanner._compute_clone_score(report) == 42.5

    def test_well_known_by_name(self):
        assert self.scanner._is_well_known("Bitcoin", "BTC")
        assert self.scanner._is_well_known("Ethereum", "ETH")

    def test_well_known_by_symbol(self):
        assert self.scanner._is_well_known("Random", "ETH")

    def test_not_well_known(self):
        assert not self.scanner._is_well_known("ShadyCoin", "SHADY")

    def test_valid_evm_address(self):
        assert self.scanner._validate_address("0x1234567890abcdef1234567890abcdef12345678", "ethereum")

    def test_valid_solana_address(self):
        assert self.scanner._validate_address("So11111111111111111111111111111111111111112", "solana")

    def test_invalid_address(self):
        assert not self.scanner._validate_address("not-an-address", "ethereum")


if __name__ == "__main__":
    unittest.main(verbosity=2)
