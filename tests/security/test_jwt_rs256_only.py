"""Security tests: RS256-only JWT validation outside the test environment.

Audit 2026-09-06: jwt_validation silently fell back to HS256 whenever RS256
keys were missing — in ANY environment. HS256 with a shared secret is a
downgrade attack surface. These tests assert:
  - prod + RS256 without keys → RuntimeError (hard fail)
  - non-prod, non-test + RS256 without keys → RuntimeError (fallback removed)
  - test env + RS256 without keys → HS256 fallback allowed (test fixtures)
"""

from __future__ import annotations

import importlib

import pytest


def _reload_jwt_validation(monkeypatch, environment: str):
    """Reload app.auth.jwt_validation with a forced environment setting."""
    import app.config as config_mod

    monkeypatch.setattr(config_mod.settings, "environment", environment)
    monkeypatch.setattr(config_mod.settings, "jwt_algorithm", "RS256")
    monkeypatch.setattr(config_mod.settings, "jwt_private_key", "")
    monkeypatch.setattr(config_mod.settings, "jwt_public_key", "")
    import app.auth.jwt_validation as jwv

    return importlib.reload(jwv)


@pytest.mark.parametrize("environment", ["prod", "staging"])
def test_rs256_without_keys_raises_outside_dev_test(monkeypatch, environment):
    """HS256 fallback is removed in prod/staging — hard configuration error."""
    with pytest.raises(RuntimeError, match=r"HS256 fallback is disabled|not set"):
        _reload_jwt_validation(monkeypatch, environment)


@pytest.mark.parametrize("environment", ["dev", "development", "test"])
def test_rs256_without_keys_falls_back_only_in_dev_test(monkeypatch, environment):
    """Dev/test environments keep the HS256 fallback so fixtures can sign tokens."""
    jwv = _reload_jwt_validation(monkeypatch, environment)
    assert jwv._JWT_ALGORITHM == "HS256"


def test_rs256_with_keys_stays_rs256(monkeypatch):
    """Properly configured RS256 is untouched."""
    import app.config as config_mod

    monkeypatch.setattr(config_mod.settings, "environment", "prod")
    monkeypatch.setattr(config_mod.settings, "jwt_algorithm", "RS256")
    monkeypatch.setattr(config_mod.settings, "jwt_private_key", "-----BEGIN PRIVATE KEY-----")
    monkeypatch.setattr(config_mod.settings, "jwt_public_key", "-----BEGIN PUBLIC KEY-----")
    import app.auth.jwt_validation as jwv

    jwv = importlib.reload(jwv)
    assert jwv._JWT_ALGORITHM == "RS256"
