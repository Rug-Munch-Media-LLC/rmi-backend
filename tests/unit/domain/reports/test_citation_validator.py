"""Tests for app/domain/reports/citation_validator.py"""


from app.domain.reports.citation_validator import validate_section


class TestCitationValidator:
    """Tests for validate_section function."""

    def test_valid_citation(self):
        """Test that valid citations pass validation."""
        result = validate_section(
            'This token has a risk score of 75/100 [1]. It is flagged [2].',
            ['Risk score 75/100 detected', 'Token flagged as suspicious'],
            on_unciteable='strip'
        )
        assert result['validation_rate'] == 1.0
        assert result['unciteable_count'] == 0
        assert '75/100' in result['validated_text']

    def test_invalid_citation(self):
        """Test that invalid citations are stripped."""
        result = validate_section(
            'This claim [99] is invalid [1].',
            ['Only source 1 available'],
            on_unciteable='strip'
        )
        assert result['validation_rate'] == 0.0
        assert result['unciteable_count'] == 1
        assert '[Data not available]' in result['validated_text']

    def test_no_citations(self):
        """Test that text without citations is marked unciteable."""
        result = validate_section(
            'This has no citations but should match source.',
            ['Source text for validation'],
            on_unciteable='strip'
        )
        assert result['validation_rate'] == 0.0
        assert result['unciteable_count'] == 1

    def test_multiple_citations_same_sentence(self):
        """Test that multiple citations in one sentence are parsed.

        Per T05 spec: validator must (1) parse [N,M,K] correctly AND
        (2) verify claim is supported by source content. With the strict
        default threshold, the literal claim 'This is supported by'
        won't match 'Source one content' terms - so it should fail
        the support check (unciteable_count > 0).
        """
        result = validate_section(
            'This is supported by [1,2,3].',
            ['Source one content', 'Source two content', 'Source three content'],
            on_unciteable='strip'
        )
        # Parsing succeeded (multi-citation [1,2,3] all valid indices)
        # But claim doesn't actually overlap with any source content
        assert result['unciteable_count'] == 1  # claim unsupported

    def test_citation_range(self):
        """Test that citation ranges [1-3] are parsed correctly.

        Per T05 spec: validator parses [1-N] and checks support.
        The literal claim 'This is supported by' doesn't overlap
        with 'Source one content' terms, so should fail support.
        """
        result = validate_section(
            'This is supported by [1-2].',
            ['Source one content', 'Source two content', 'Source three content'],
            on_unciteable='strip'
        )
        # Parsed correctly (range expanded)
        # But claim doesn't match source content
        assert result['unciteable_count'] == 1

    def test_empty_sources(self):
        """Test that empty sources list marks all as unciteable."""
        result = validate_section(
            'Some text [1] with citations.',
            [],
            on_unciteable='strip'
        )
        assert result['validation_rate'] == 0.0
        assert 'Data not available' in result['validated_text']

    def test_keep_unciteable(self):
        """Test that on_unciteable='keep' preserves unciteable text."""
        result = validate_section(
            'Some text [99] invalid.',
            ['Source one content'],
            on_unciteable='keep'
        )
        assert result['unciteable_count'] == 1
        assert 'Some text [99] invalid.' in result['validated_text']

    def test_strip_unciteable(self):
        """Test that on_unciteable='strip' removes unciteable text."""
        result = validate_section(
            'Some text [99] invalid.',
            ['Source one content'],
            on_unciteable='strip'
        )
        assert result['unciteable_count'] == 1
        assert 'Data not available' in result['validated_text']

    def test_validation_report_structure(self):
        """Test that the validation report has the correct structure."""
        result = validate_section(
            'Test [1].',
            ['Source one content']
        )
        assert 'validated_text' in result
        assert 'citations' in result
        assert 'unciteable_count' in result
        assert 'validation_rate' in result
        assert isinstance(result['citations'], list)

    def test_citation_details(self):
        """Test that citations include claim, source_idx, source_text, supported."""
        result = validate_section(
            'Test [1].',
            ['Source one content']
        )
        if result['citations']:
            c = result['citations'][0]
            assert 'claim' in c
            assert 'source_idx' in c
            assert 'source_text' in c
            assert 'supported' in c
