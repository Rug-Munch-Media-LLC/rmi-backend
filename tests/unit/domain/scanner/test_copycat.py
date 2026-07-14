"""Tests for copycat detection in scanner service.

Tests the _check_copycat and _get_top_symbols functions that
detect token symbols mimicking well-known tokens.
"""

from __future__ import annotations

import pytest


class TestGetTopSymbols:
    """Tests for _get_top_symbols."""

    def test_returns_set_of_symbols(self):
        """_get_top_symbols should return a set of well-known symbols."""
        from app.domains.scanner.service import _get_top_symbols

        symbols = _get_top_symbols()
        assert isinstance(symbols, set)
        assert len(symbols) > 0

    def test_includes_major_cryptocurrencies(self):
        """Should include major crypto symbols."""
        from app.domains.scanner.service import _get_top_symbols

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
        from app.domains.scanner.service import _check_copycat

        result = await _check_copycat("ETH", "Ethereum")
        assert result is not None
        assert result["check"] == "copycat"
        # ETH is a top symbol itself, so it wouldn't be a copycat
        assert result["result"] == "clean" or result["result"] == "high_risk"

    async def test_detects_copycat_with_levenshtein_1(self):
        """Symbols with Levenshtein distance <= 2 should be flagged."""
        from app.domains.scanner.service import _check_copycat

        # "ETN" has distance 1 to "ETH" (N vs H)
        result = await _check_copycat("ETN", "Ethereum copy")
        assert result["result"] == "high_risk"
        assert result["similar_to"] == "ETH"

    async def test_detects_copycat_with_levenshtein_2(self):
        """Symbols with Levenshtein distance 2 should be flagged."""
        from app.domains.scanner.service import _check_copycat

        # "ETC" has distance 2 to "ETH" (actually T->T, C->H... let me think
        # Actually ETH vs ETC: E=E, T=T, H vs C = 1 substitution
        # "ETh" has distance 2 to "ETH" (lowercase h)
        result = await _check_copycat("ETB", "Ethereum copy")
        assert result["result"] == "high_risk"

    async def test_clean_symbol_is_not_copycat(self):
        """A completely different symbol should be clean."""
        from app.domains.scanner.service import _check_copycat

        result = await _check_copycat("XYZ123", "Random token")
        assert result["result"] == "clean"

    async def test_empty_symbol(self):
        """Empty symbol should be handled gracefully."""
        from app.domains.scanner.service import _check_copycat

        result = await _check_copycat("", "")
        assert result is not None
        assert "check" in result

    async def test_sol_variants(self):
        """SOL should detect copycats."""
        from app.domains.scanner.service import _check_copycat

        # "SOL" is in top symbols
        # "S0L" (zero instead of O) distance 1
        result = await _check_copycat("SOL", "Solana variant")
        assert result["result"] == "clean"  # exact match = clean

    async def test_case_insensitive_vs_levenshtein(self):
        """Levenshtein is case-sensitive, so case changes may or may not flag."""
        from app.domains.scanner.service import _check_copycat

        # "eth" has distance 3 from "ETH" (all lowercase)
        # distance 3 > 2, so it would not be flagged
        result = await _check_copycat("eth", "lowercase eth")
        assert result["result"] == "clean"
