"""Deterministic stand-in credentials for tests.

Real credentials never live in this repo. Tests that need a credential-shaped
string call :func:`fake_credential` instead of embedding a literal, so no
secret scanner (or reviewer) can mistake a fixture for a leaked key.
"""

from __future__ import annotations

_SEGMENTS = {
    "api_key": "api-key",
    "password": "password",
    "secret": "secret",
    "token": "token",
}


def fake_credential(kind: str, flavour: str = "primary") -> str:
    """Return a deterministic, obviously-fake credential string.

    Args:
        kind: One of ``api_key``, ``password``, ``secret``, ``token``.
        flavour: Disambiguates multiple credentials inside one test.

    Raises:
        KeyError: ``kind`` is not a supported credential type.
    """
    segment = _SEGMENTS[kind]
    return f"rmi-test-{segment}-{flavour}-not-a-real-credential"
