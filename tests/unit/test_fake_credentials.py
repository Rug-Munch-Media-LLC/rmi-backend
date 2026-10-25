"""Tests for the shared fake-credential fixture used by other test modules."""

from __future__ import annotations

import os

import pytest

from tests.support.fake_credentials import fake_credential

_KINDS = ("api_key", "password", "secret", "token")


@pytest.mark.parametrize("kind", _KINDS)
def test_fake_credential_is_deterministic_and_marked_fake(kind: str) -> None:
    value = fake_credential(kind)

    assert value == fake_credential(kind)
    assert value.startswith("rmi-test-")
    assert "not-a-real-credential" in value
    assert len(value) > 16


@pytest.mark.parametrize("kind", _KINDS)
def test_fake_credential_separates_flavours(kind: str) -> None:
    assert fake_credential(kind, "a") != fake_credential(kind, "b")


def test_fake_credential_rejects_unknown_kind() -> None:
    with pytest.raises(KeyError):
        fake_credential("moral-support-token")


def test_fake_credential_never_reads_process_environment() -> None:
    os.environ["RMI_TEST_CREDENTIAL"] = "leaked-from-environment"
    try:
        assert "leaked-from-environment" not in fake_credential("secret")
    finally:
        del os.environ["RMI_TEST_CREDENTIAL"]
