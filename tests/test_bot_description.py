"""PURE tests for the RugMunch bot self-description text.

Covers ``welcome_card_text()`` (brand line, capability list, CTA) and
``social_proof_text()`` (evidence-first trust block). No network and no
DB access — both functions are pure string formatters.
"""

from __future__ import annotations

import os
import re

os.environ["ENVIRONMENT"] = "dev"

from app.domains.telegram.rugmunchbot.formatters import (
    social_proof_text,
    welcome_card_text,
)


def _strip_tags(text: str) -> str:
    """Remove balanced HTML tags; the remainder must contain no raw < >."""
    stripped = re.sub(r"</?(b|i|code|a)>", "", text)
    stripped = re.sub(r'<a href="[^"]*">', "", stripped)
    return stripped


# ── welcome_card_text ──────────────────────────────────────────────


def test_welcome_card_brand_line() -> None:
    text = welcome_card_text()
    assert "Rug Munch Bot" in text
    assert "Rug Munch Intelligence" in text


def test_welcome_card_capability_keywords() -> None:
    text = welcome_card_text().lower()
    for kw in ("scan", "wallet", "trace", "price", "technical", "market", "news"):
        assert kw in text, f"missing capability keyword: {kw!r}"


def test_welcome_card_capability_bullet_count() -> None:
    body = welcome_card_text().split("<b>What I can do</b>", 1)[1]
    bullets = body.split("\n\n", 1)[0].splitlines()
    bullets = [line for line in bullets if line.strip()]
    assert 6 <= len(bullets) <= 8, f"expected 6-8 capability bullets, got {len(bullets)}"


def test_welcome_card_cta() -> None:
    text = welcome_card_text()
    assert "<code>/scan</code>" in text
    assert "<code>/chat</code>" in text
    assert "/help" in text  # via footer_links
    assert "t.me" in text  # channel link in footer_links


def test_welcome_card_html_safe() -> None:
    stripped = _strip_tags(welcome_card_text())
    assert "<" not in stripped
    assert ">" not in stripped


# ── social_proof_text ──────────────────────────────────────────────


def test_social_proof_sanctioned_figures() -> None:
    text = social_proof_text()
    assert "17 chains" in text
    assert "359M+" in text
    assert "200+" in text


def test_social_proof_no_invented_stats() -> None:
    """Fallback counters like '1,000+ users' / '50,000+ scans' must not
    be presented as fact."""
    text = social_proof_text().lower()
    assert "users" not in text
    assert "scans completed" not in text


def test_social_proof_html_safe() -> None:
    stripped = _strip_tags(social_proof_text())
    assert "<" not in stripped
    assert ">" not in stripped
