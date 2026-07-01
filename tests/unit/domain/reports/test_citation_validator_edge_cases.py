"""Tests for app/domain/reports/citation_validator edge cases."""


from app.domain.reports.citation_validator import validate_section


class TestCitationValidatorEdgeCases:
    """Edge case tests for citation validator."""

    def test_long_citation_range(self):
        """Test long citation range like [1-10].

        Per T05 spec: validator must (1) parse [1-N] correctly AND
        (2) verify claim is supported by source content. With strict
        default overlap (40%), generic claim text won't match
        'Source 0' / 'Source 4' content — so claim is unciteable.
        """
        sources = [f"Source {i}" for i in range(20)]
        result = validate_section(
            'Test [1-5] citation.',
            sources,
            on_unciteable='strip'
        )
        # Parsing correct (1-5 expanded to 5 indices)
        # But claim doesn't overlap with source content
        assert result['unciteable_count'] == 1

    def test_overlapping_citations(self):
        """Test overlapping citations like [1, 2-3, 4].

        Per T05 spec: validator parses [1, 2-3, 4] correctly AND verifies
        claim support. With strict default overlap, generic claim text
        doesn't overlap with 'Source N' content.
        """
        sources = ["Source 1", "Source 2", "Source 3", "Source 4"]
        result = validate_section(
            'Test [1, 2-3, 4] citation.',
            sources,
            on_unciteable='strip'
        )
        # Parsing correct ([1, 2-3, 4] expanded to [1, 2, 3, 4])
        # But content overlap fails for generic claim
        assert result['unciteable_count'] == 1

    def test_single_char_source_text(self):
        """Test with very short source text."""
        result = validate_section(
            'Test [1] citation.',
            ['a'],
            on_unciteable='strip'
        )
        assert 'validated_text' in result
        assert 'citations' in result

    def test_source_text_with_only_stopwords(self):
        """Test with source text that has only stopwords."""
        result = validate_section(
            'Test [1] citation.',
            ['the and is a'],
            on_unciteable='strip'
        )
        assert 'validated_text' in result

    def test_very_long_text(self):
        """Test with very long input text."""
        long_text = "This is a very long sentence. " * 100
        result = validate_section(
            long_text + " [1].",
            ["Source text for validation"],
            on_unciteable='strip'
        )
        assert 'validated_text' in result

    def test_multiple_sentences_with_citations(self):
        """Test multiple sentences each with different citations."""
        sources = [
            "First source content",
            "Second source content",
            "Third source content",
        ]
        result = validate_section(
            "First sentence [1]. Second sentence [2]. Third sentence [3].",
            sources,
            on_unciteable='strip'
        )
        assert result['validation_rate'] == 1.0
        assert result['unciteable_count'] == 0

    def test_mixed_citeable_and_unciteable(self):
        """Test mix of citeable and unciteable content.

        Per T05 spec: validator must mark sentences as unciteable if
        (1) citation index is out of range OR (2) claim doesn't match
        source content. With strict overlap, even a valid [1] citation
        for claim "Valid [1]" is unciteable because "Valid" doesn't
        overlap with "Source one".
        """
        sources = ["Source one"]
        result = validate_section(
            "Valid [1]. Invalid [99]. Also valid [1].",
            sources,
            on_unciteable='strip'
        )
        # All 3 sentences fail content overlap check (with strict default)
        # The 1st and 3rd have valid index [1] but claim "Valid" doesn't
        # overlap with "Source one" content. The 2nd has out-of-range [99].
        assert result['unciteable_count'] == 3

    def test_citation_without_closing_bracket(self):
        """Test citation with missing closing bracket (malformed)."""
        sources = ["Source one"]
        result = validate_section(
            'Test [1 unciteable.',
            sources,
            on_unciteable='strip'
        )
        # Should still process and mark as unciteable
        assert 'validated_text' in result

    def test_citation_with_extra_whitespace(self):
        """Test citation with extra whitespace like [ 1 , 2 ].

        Per T05 spec: validator must (1) parse whitespace in citations
        AND (2) verify claim support. Whitespace is parsed correctly,
        but claim text 'Test citation' doesn't overlap with sources.
        """
        sources = ["Source 1", "Source 2"]
        result = validate_section(
            'Test [ 1 , 2 ] citation.',
            sources,
            on_unciteable='strip'
        )
        # Parsed [ 1 , 2 ] → [1, 2] successfully (whitespace tolerated)
        # But content overlap fails for generic claim
        assert result['unciteable_count'] == 1
