"""Tests for Levenshtein distance in app/domains/scanner/service.py.

The _levenshtein_distance function is a pure string algorithm used
for copycat token detection. These tests cover all edge cases.
"""

from __future__ import annotations

import pytest


class TestLevenshteinDistance:
    """Tests for _levenshtein_distance function."""

    def test_identical_strings(self):
        """Identical strings should have distance 0."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("ETH", "ETH") == 0
        assert _levenshtein_distance("", "") == 0

    def test_completely_different_strings(self):
        """Completely different strings should have large distance."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("abc", "xyz") == 3

    def test_insertion(self):
        """Inserting a character should cost 1."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("cat", "cats") == 1

    def test_deletion(self):
        """Deleting a character should cost 1."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("cats", "cat") == 1

    def test_substitution(self):
        """Substituting a character should cost 1."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("cat", "cut") == 1

    def test_case_sensitivity(self):
        """The function should be case-sensitive."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("ETH", "eth") == 3

    def test_empty_vs_nonempty(self):
        """Distance from empty to non-empty should equal string length."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("", "hello") == 5
        assert _levenshtein_distance("hello", "") == 5

    def test_long_strings(self):
        """Distance should work with longer strings."""
        from app.domains.scanner.service import _levenshtein_distance

        s1 = "ethereum"
        s2 = "ethereumclassic"
        assert _levenshtein_distance(s1, s2) == 7

    def test_transposition_not_handled(self):
        """Swapping two chars should cost 2 (not 1 like Damerau-Levenshtein)."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("ab", "ba") == 2

    def test_numbers(self):
        """Numeric strings should work."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("12345", "123456") == 1
        assert _levenshtein_distance("100", "001") == 2

    def test_with_whitespace(self):
        """Strings with whitespace should work."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("hello world", "helloworld") == 1

    def test_single_character(self):
        """Single character strings."""
        from app.domains.scanner.service import _levenshtein_distance

        assert _levenshtein_distance("a", "b") == 1
        assert _levenshtein_distance("a", "a") == 0
