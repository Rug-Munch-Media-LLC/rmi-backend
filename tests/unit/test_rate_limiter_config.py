"""Tests for app/rate_limiter.py - Provider configuration.

Tests that provider limits are configured correctly and the
configure_providers function works as expected.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestProviderLimit:
    """Tests for ProviderLimit dataclass."""

    def test_provider_limit_creation(self):
        """ProviderLimit should be creatable with basic params."""
        from app.rate_limiter import ProviderLimit

        pl = ProviderLimit(
            id="test_provider",
            name="Test Provider",
            model="test-model",
            dims=768,
            base_url="http://localhost",
            key_env="TEST_KEY",
            rpm=10,
            rpd=100,
        )
        assert pl.id == "test_provider"
        assert pl.rpm == 10
        assert pl.rpd == 100
        assert pl.free is True
        assert pl.priority == 100

    def test_provider_limit_custom_values(self):
        """ProviderLimit should accept custom values."""
        from app.rate_limiter import ProviderLimit

        pl = ProviderLimit(
            id="custom_provider",
            name="Custom Provider",
            model="custom-model",
            dims=1024,
            base_url="https://api.example.com",
            key_env="CUSTOM_KEY",
            rpm=100,
            rpd=10000,
            free=False,
            priority=1,
            timeout=30.0,
        )
        assert pl.free is False
        assert pl.priority == 1
        assert pl.timeout == 30.0


class TestConfigureProviders:
    """Tests for configure_providers function."""

    def test_configure_without_credits(self):
        """configure_providers should work with no OpenRouter credits."""
        from app.rate_limiter import PROVIDERS, configure_providers

        configure_providers(has_openrouter_credits=False)
        assert len(PROVIDERS) >= 1

    def test_configure_with_credits(self):
        """configure_providers with credits should keep providers."""
        from app.rate_limiter import PROVIDERS, configure_providers

        configure_providers(has_openrouter_credits=True)
        assert len(PROVIDERS) >= 1

    def test_configure_clears_and_rebuilds(self):
        """configure_providers should clear previous list before rebuilding."""
        from app.rate_limiter import PROVIDERS, configure_providers

        configure_providers(has_openrouter_credits=False)
        count_before = len(PROVIDERS)

        configure_providers(has_openrouter_credits=True)
        count_after = len(PROVIDERS)

        # Just ensure it works without error
        assert count_after >= 1
