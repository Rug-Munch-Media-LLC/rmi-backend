"""Test to verify the quarantine mechanism is working correctly."""

import pytest


@pytest.mark.quarantined(owner="test-engineer", reason="Test quarantined for verification", review_date="2026-12-08")
def test_quarantine_registration():
    """Test that quarantine marker is properly registered."""
    assert True  # This test should pass


@pytest.mark.flaky(reruns=2, reruns_delay=1)
def test_flaky_marker_direct():
    """Test that direct flaky marker still works."""
    assert True


def test_normal_test():
    """Test that normal tests work without markers."""
    assert True
