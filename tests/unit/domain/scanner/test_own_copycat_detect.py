"""Tests for own_copycat_detect — ClickHouse + RAG-powered copycat detection.

Tests the Levenshtein distance function, symbol similarity checking,
and aggregate scoring logic as pure functions (no ClickHouse required).
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines.own_copycat_detect import (
    _check_symbol_similarity,
    _compute_copycat_score,
    _levenshtein_distance,
    clear_cache,
    get_cache_stats,
)


class TestLevenshteinDistance:
    """Tests for _levenshtein_distance — string similarity metric."""

    def test_identical_strings(self):
        """Identical strings should have distance 0."""
        assert _levenshtein_distance("PEPE", "PEPE") == 0

    def test_completely_different(self):
        """Completely different strings should have large distance."""
        assert _levenshtein_distance("ABC", "XYZ") == 3

    def test_one_insertion(self):
        """One character insertion should have distance 1."""
        assert _levenshtein_distance("PEPE", "PEPE2") == 1

    def test_one_substitution(self):
        """One character substitution should have distance 1."""
        assert _levenshtein_distance("PEPE", "PEPA") == 1

    def test_one_deletion(self):
        """One character deletion should have distance 1."""
        assert _levenshtein_distance("PEPE", "PEP") == 1

    def test_empty_string(self):
        """Empty string vs another should have full length distance."""
        assert _levenshtein_distance("", "PEPE") == 4
        assert _levenshtein_distance("PEPE", "") == 4

    def test_case_sensitive(self):
        """Levenshtein is case-sensitive."""
        assert _levenshtein_distance("pepe", "PEPE") == 4

    def test_longer_first_arg_handled(self):
        """Should handle when first argument is longer."""
        assert _levenshtein_distance("FLOKI", "FLOK") == 1


class TestCheckSymbolSimilarity:
    """Tests for _check_symbol_similarity — matching against known symbols."""

    def test_exact_top_symbol_returns_empty(self):
        """Exact match of a top symbol should return no matches (not a copycat)."""
        matches = _check_symbol_similarity("ETH", "Ethereum")
        assert matches == []

    def test_near_match_returns_matches(self):
        """Symbols within Levenshtein distance of top symbols should match."""
        matches = _check_symbol_similarity("ETN", "Ethereum copy")
        assert len(matches) >= 1
        assert any(m.get("symbol") in ("ETH", "BTC", "VET", "LTC") for m in matches)

    def test_distinct_symbol_returns_empty(self):
        """Completely different symbols should not match."""
        matches = _check_symbol_similarity("XYZ123", "Random token")
        assert matches == []

    def test_empty_symbol_returns_empty(self):
        """Empty symbol should return no matches."""
        matches = _check_symbol_similarity("", "")
        assert matches == []

    def test_name_squatting_detected(self):
        """Names containing known symbols should be flagged as name squatting."""
        matches = _check_symbol_similarity("MYTOKEN", "ETHEREUM BLUE CHIP")
        squatting = [m for m in matches if m.get("match_type") == "name_squatting"]
        assert len(squatting) >= 1

    def test_dedup_by_symbol(self):
        """Duplicate symbol matches should be deduplicated."""
        matches = _check_symbol_similarity("ETN", "ETN token")
        symbols = [m["symbol"] for m in matches]
        assert len(symbols) == len(set(symbols))

    def test_sorted_by_score_desc(self):
        """Matches should be sorted by score descending."""
        matches = _check_symbol_similarity("LTC2", "Litecoin copy")
        if matches:
            scores = [m["score"] for m in matches]
            assert scores == sorted(scores, reverse=True)


class TestComputeCopycatScore:
    """Tests for _compute_copycat_score — aggregate scoring."""

    def test_no_similar_tokens_returns_unique(self):
        """No similar tokens should result in unique label."""
        result: dict[str, Any] = {
            "similar_tokens": [],
            "symbol_similarity_found": False,
            "name_similarity_found": False,
            "deployer_copy_pattern": False,
            "is_copycat": False,  # Set as default before scoring
        }
        _compute_copycat_score(result)
        assert result.get("is_copycat") is False
        assert result["risk_label"] == "unique"

    def test_high_score_matches_flagged_copycat(self):
        """High similarity scores should flag as copycat."""
        result: dict[str, Any] = {
            "similar_tokens": [
                {"symbol": "PEPE", "name": "PEPE", "score": 90.0, "match_type": "symbol_similarity"},
            ],
            "symbol_similarity_found": True,
            "name_similarity_found": False,
            "deployer_copy_pattern": False,
        }
        _compute_copycat_score(result)
        assert result["is_copycat"] is True
        assert result["risk_label"] == "high"
        assert result["confidence"] == 90.0

    def test_multiple_signals_increase_confidence(self):
        """Multiple signals should add confidence bonus."""
        result: dict[str, Any] = {
            "similar_tokens": [
                {"symbol": "PEPE", "name": "PEPE", "score": 75.0, "match_type": "symbol_similarity"},
            ],
            "symbol_similarity_found": True,
            "name_similarity_found": True,
            "deployer_copy_pattern": False,
        }
        _compute_copycat_score(result)
        # 75 + 15 bonus for 2 signals = 90
        assert result["confidence"] == 90.0

    def test_dedup_similar_tokens(self):
        """Duplicate similar tokens should be deduplicated."""
        result: dict[str, Any] = {
            "similar_tokens": [
                {"symbol": "PEPE", "name": "PEPE", "score": 90.0, "match_type": "symbol_similarity"},
                {"symbol": "PEPE", "name": "PEPE", "score": 85.0, "match_type": "name_similarity"},
            ],
            "symbol_similarity_found": True,
            "name_similarity_found": True,
            "deployer_copy_pattern": False,
        }
        _compute_copycat_score(result)
        assert len(result["similar_tokens"]) == 1


class TestCopycatCache:
    """Tests for copycat cache."""

    def setup_method(self):
        clear_cache()

    def test_cache_starts_empty(self):
        """Cache should start empty."""
        stats = get_cache_stats()
        assert stats["cache_size"] == 0

    def test_cache_max_positive(self):
        """Cache max should be positive."""
        stats = get_cache_stats()
        assert stats["cache_max"] > 0

    def test_cache_ttl_positive(self):
        """Cache TTL should be positive."""
        stats = get_cache_stats()
        assert stats["cache_ttl_seconds"] > 0
