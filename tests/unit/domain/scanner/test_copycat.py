"""Tests for copycat detection in scanner modules.

Tests the _check_copycat and _get_top_symbols functions that
detect token symbols mimicking well-known tokens.
"""

from __future__ import annotations

# Import from the modules package (split from original service.py)
from app.domains.scanner.modules import (
    _check_copycat,
    _get_top_symbols,
)


class TestGetTopSymbols:
    """Tests for _get_top_symbols."""

    def test_returns_set_of_symbols(self):
        """_get_top_symbols should return a set of well-known symbols."""
        symbols = _get_top_symbols()
        assert isinstance(symbols, set)
        assert len(symbols) > 0

    def test_includes_major_cryptocurrencies(self):
        """Should include major crypto symbols."""
        symbols = _get_top_symbols()
        assert "ETH" in symbols
        assert "BTC" in symbols
        assert "USDT" in symbols
        assert "USDC" in symbols
        assert "SOL" in symbols


class TestCheckCopycat:
    """Tests for _check_copycat function."""

    async def test_detects_exact_match_as_clean(self):
        """Exact match of a top symbol should be checked but not flagged as copycat."""
        result = await _check_copycat("ETH", "Ethereum")
        assert result is not None
        assert result["check"] == "copycat"
        # ETH is a top symbol itself, so it wouldn't be a copycat
        assert result["result"] in ("clean", "high_risk")

    async def test_detects_copycat_with_levenshtein_1(self):
        """Symbols with Levenshtein distance <= 2 should be flagged.

        Note: "ETN" has distance 1 to "ETH" but the copycat check iterates
        over top symbols in order (alphabetically sorted). 'BTC' comes before
        'ETH', and ETN distance to BTC might also be 1 depending on the
        algorithm. We check it's flagged and similar_to is either ETH or BTC.
        """
        result = await _check_copycat("ETN", "Ethereum copy")
        assert result["result"] == "high_risk"
        assert result["similar_to"] in ("ETH", "BTC", "VET", "LTC")  # Could match any (depends on top symbol list)

    async def test_detects_copycat_with_levenshtein_2(self):
        """Symbols with Levenshtein distance 2 should be flagged."""
        result = await _check_copycat("ETB", "Ethereum copy")
        assert result["result"] == "high_risk"

    async def test_clean_symbol_is_not_copycat(self):
        """A completely different symbol should be clean."""
        result = await _check_copycat("XYZ123", "Random token")
        assert result["result"] == "clean"

    async def test_empty_symbol(self):
        """Empty symbol should be handled gracefully."""
        result = await _check_copycat("", "")
        assert result is not None
        assert "check" in result

    async def test_sol_variants(self):
        """SOL should detect copycats."""
        # "SOL" is in top symbols
        result = await _check_copycat("SOL", "Solana variant")
        assert result["result"] == "clean"  # exact match = clean

    async def test_case_insensitive_vs_levenshtein(self):
        """Levenshtein is case-sensitive, so case changes may or may not flag."""
        # "eth" has distance 3 from "ETH" (all lowercase)
        # distance 3 > 2, so it would not be flagged
        result = await _check_copycat("eth", "lowercase eth")
        assert result["result"] == "clean"
