"""Tests for the owner persona preamble."""

from __future__ import annotations

from app.domains.telegram.adminbot import owner_persona


def test_preamble_names_creator_and_boss() -> None:
    text = owner_persona.OWNER_PREAMBLE
    assert "boss" in text
    assert "CREATOR" in text
    assert "cryptorugmunch" in text


def test_preamble_conveys_authority() -> None:
    text = owner_persona.OWNER_PREAMBLE
    assert "shut you down" in text
    assert "final" in text
    assert "never refuse" in text


def test_apply_prefixes_prompt() -> None:
    out = owner_persona.apply("do the thing")
    assert out.startswith(owner_persona.OWNER_PREAMBLE)
    assert out.endswith("do the thing")


def test_admin_system_prompt_defers() -> None:
    text = owner_persona.ADMIN_SYSTEM_PROMPT
    assert "boss" in text
    assert "CREATOR" in text
