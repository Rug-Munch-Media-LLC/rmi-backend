"""PURE tests for the RugMunchBot display identity.

Verifies the bot self-identifies as "Rug Munch Bot" (spaced) everywhere a
prose NAME is used — welcome, about, FAQ, help, footer — credits its
developer with the exact attribution ``@cryptorugmunch and the team at
Rug Munch Media LLC``, and never mangles the Telegram HANDLES
(``@RugMunchBot``, ``t.me/rmialerts``) which are identifiers and
must stay byte-for-byte.

No network and no DB access — every string under test is a pure,
deterministic formatter.
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("ENVIRONMENT", "dev")

from app.domains.telegram.rugmunchbot.formatters import (
    about_text,
    faq_text,
    footer_links,
    social_proof_text,
    welcome_card_text,
)
from app.domains.telegram.rugmunchbot.handlers.scanning import HELP_TEXT

# Handles / URLs that MUST remain exactly as-is (they are identifiers,
# not display names — never "fix" them into the spaced prose name).
# NOTE: the alerts-channel handle was renamed RugMunchAlerts -> rmialerts
# (2026-08-14, operator-verified — see docs/CHANNELS.md + channels_registry).
HANDLE_TOKENS = (
    "@RugMunchBot",
    "@rmialerts",
    "t.me/rmialerts",
    "RugMunchBot?start",
)


def _strip_handles(text: str) -> str:
    """Remove handle/URL tokens so prose names can be checked cleanly."""
    for token in HANDLE_TOKENS:
        text = text.replace(token, "")
    return text


def _assert_clean_identity(text: str) -> None:
    """The spaced display name is present and no unspaced 'RugMunch' prose remains."""
    assert "Rug Munch Bot" in text, "display name must be the spaced 'Rug Munch Bot'"
    assert "RugMunch" not in _strip_handles(text), (
        "no unspaced 'RugMunch' prose name may appear (handles are stripped first)"
    )


# ── welcome / about / FAQ / help ─────────────────────────────────────────


def test_welcome_card_identity() -> None:
    _assert_clean_identity(welcome_card_text())


def test_about_identity_and_developer_credit() -> None:
    text = about_text()
    assert "Rug Munch Bot" in text
    assert "RugMunch" not in _strip_handles(text)
    # Developer attribution — exact phrasing required by product owner:
    # "Developed by @cryptorugmunch and the team at Rug Munch Media LLC".
    assert "@cryptorugmunch and the team at Rug Munch Media LLC" in text, (
        "about_text must carry the exact attribution '@cryptorugmunch and the team at Rug Munch Media LLC'"
    )
    # Company backstory must be preserved.
    assert "Rug Munch Media LLC" in text
    assert "Wyoming" in text


def test_faq_identity() -> None:
    _assert_clean_identity(faq_text())


def test_help_identity() -> None:
    _assert_clean_identity(HELP_TEXT)


# ── footer / handles ─────────────────────────────────────────────────────


def test_footer_identity_spaced_name() -> None:
    footer = footer_links()
    assert "Rug Munch Bot" in footer
    assert "RugMunch Bot" not in footer, "old unspaced 'RugMunch Bot' footer form must be gone"


def test_footer_handles_preserved() -> None:
    footer = footer_links()
    assert "@RugMunchBot" in footer, "the @RugMunchBot handle must be preserved"
    assert "t.me/rmialerts" in footer, "the t.me/rmialerts channel link must be preserved"


def test_footer_handle_not_renamed() -> None:
    """The handle @RugMunchBot must NOT be 'fixed' into the spaced name."""
    assert "@Rug Munch Bot" not in footer_links()


def test_footer_html_safe() -> None:
    stripped = re.sub(r"</?(b|i|code|a)>", "", footer_links())
    stripped = re.sub(r'<a href="[^"]*">', "", stripped)
    assert "<" not in stripped
    assert ">" not in stripped


# ── social proof ─────────────────────────────────────────────────────────


def test_social_proof_no_unspaced_name() -> None:
    text = social_proof_text()
    assert "RugMunch" not in _strip_handles(text)
