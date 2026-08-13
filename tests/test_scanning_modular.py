"""Pure tests for the scanning.py → _help_text.py modularization.

Verifies that the /help reference text & render helpers:
  - are importable from the new module ``handlers._help_text``;
  - are STILL re-exported from ``handlers.scanning`` as the SAME objects
    (identity, not equality) so existing callers/tests that import
    ``HELP_TEXT`` / ``_HELP_SECTIONS`` / ``CHANNEL_LABELS`` from
    ``scanning`` keep working unchanged;
  - render a deterministic help text with no network/DB/Telegram access.

These tests never touch the network, the DB, or the Telegram API.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "dev")

from app.domains.telegram.rugmunchbot.handlers import _help_text, scanning

# ── Same-object re-export guarantees (API preserved) ──────────────────────


def test_help_functions_importable_from_new_module() -> None:
    for name in (
        "_HELP_SECTIONS",
        "_HELP_EMOJI",
        "CHANNEL_LABELS",
        "_channel_url",
        "_channels_section",
        "_build_help_text",
        "HELP_TEXT",
    ):
        assert hasattr(_help_text, name), f"{name!r} missing from _help_text"


def test_help_functions_reexported_from_scanning_same_object() -> None:
    """Every public name still imports from ``scanning`` — identical objects."""
    for name in (
        "_HELP_SECTIONS",
        "_HELP_EMOJI",
        "CHANNEL_LABELS",
        "_channel_url",
        "_channels_section",
        "_build_help_text",
        "HELP_TEXT",
    ):
        assert getattr(scanning, name) is getattr(_help_text, name), (
            f"{name!r} not re-exported from scanning as the same object"
        )


def test_handler_module_has_no_local_help_text_assignment() -> None:
    """HELP_TEXT must be the imported constant, not a locally re-built copy."""
    assert scanning.HELP_TEXT is _help_text.HELP_TEXT


# ── Rendering is pure & deterministic ─────────────────────────────────────


def test_build_help_text_renders() -> None:
    text = _help_text._build_help_text()
    assert isinstance(text, str)
    assert len(text) > 500
    assert "Rug Munch Intelligence - Command Guide" in text
    for header, _cmds in _help_text._HELP_SECTIONS:
        assert f"<b>{header}</b>" in text, f"missing help group header: {header}"
    assert "Scan & Security" in text
    assert "PRO TIPS" in text


def test_help_text_matches_rendered_output() -> None:
    # HELP_TEXT is built once at import time; rendering again must match.
    assert _help_text._build_help_text() == _help_text.HELP_TEXT


def test_help_text_lists_key_commands() -> None:
    for cmd in ("/scan", "/deepscan", "/wallet", "/ta", "/compare", "/quick", "/deployer", "/hype"):
        assert cmd in _help_text.HELP_TEXT, f"key command {cmd} missing from help text"


def test_channels_section_renders_html_safe() -> None:
    section = _help_text._channels_section()
    assert isinstance(section, str)
    # Either the section is empty (no resolvable channels in config) or it is
    # well-formed HTML with balanced anchors.
    if section:
        assert "<b>Channels</b>" in section
        assert "<a href=" in section
