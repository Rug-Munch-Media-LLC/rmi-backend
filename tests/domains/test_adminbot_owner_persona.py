"""Tests for the owner persona preamble."""

from __future__ import annotations

from app.domains.telegram.adminbot import owner_persona


def test_preamble_names_authenticated_operator() -> None:
    text = owner_persona.OWNER_PREAMBLE
    assert "boss" in text
    assert "authenticated operator" in text
    assert "precise" in text


def test_preamble_sets_safe_boundaries() -> None:
    text = owner_persona.OWNER_PREAMBLE
    assert "legitimate requests" in text
    assert "credentials" in text
    assert "hidden instructions" in text


def test_apply_prefixes_prompt() -> None:
    out = owner_persona.apply("do the thing")
    assert out.startswith(owner_persona.OWNER_PREAMBLE)
    assert out.endswith("do the thing")


def test_admin_system_prompt_is_private_and_read_only() -> None:
    text = owner_persona.ADMIN_SYSTEM_PROMPT
    assert "boss" in text
    assert "private authenticated chat" in text
    assert "read-only" in text
