"""Pure tests for the RugMunchBot command menu + instructions UX.

Covers:
  - the BotFather command list (``_BOT_COMMANDS``): well-formed, unique,
    ordered, descriptions within Telegram limits;
  - the rendered /help reference (``HELP_TEXT``): category headers, key
    commands, balanced HTML tags;
  - the main-menu inline keyboard (``_main_menu_rows``): sane grouping,
    no orphan callback data, owner-only admin button.

These tests never touch the network, the DB, or the Telegram API.
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("ENVIRONMENT", "dev")

from app.domains.telegram.rugmunchbot.bot import _BOT_COMMANDS
from app.domains.telegram.rugmunchbot.handlers.scanning import _HELP_SECTIONS, HELP_TEXT
from app.domains.telegram.rugmunchbot.keyboards import main_menu_kb, welcome_menu_kb

# ── Command list (BotFather composer menu) ────────────────────────────────

EXPECTED_ORDER = ["scan", "wallet", "price", "chart", "trace", "market", "news", "alerts", "chat", "help", "start"]


def test_command_list_has_expected_priority_order() -> None:
    commands = [c.command for c in _BOT_COMMANDS]
    assert commands == EXPECTED_ORDER


def test_command_list_unique() -> None:
    commands = [c.command for c in _BOT_COMMANDS]
    assert len(commands) == len(set(commands))


def test_command_list_descriptions_non_empty_and_short() -> None:
    assert _BOT_COMMANDS, "command list must not be empty"
    for c in _BOT_COMMANDS:
        assert c.command, "command name must be non-empty"
        assert c.description.strip(), f"command /{c.command} must have a non-empty description"
        assert len(c.description) <= 30, f"/{c.command} description exceeds 30 chars: {c.description!r}"


def test_command_list_total_description_within_telegram_limit() -> None:
    # Bot API: the sum of all descriptions must not exceed 256 chars.
    total = sum(len(c.description) for c in _BOT_COMMANDS)
    assert total <= 256, f"total description length {total} exceeds Telegram's 256-char limit"


# ── /help reference text ──────────────────────────────────────────────────

EXPECTED_HELP_HEADERS = [
    "Scan & Security",
    "Wallet & Tracing",
    "Market & Analysis",
    "News & Alerts",
    "Chat",
]


def test_help_text_renders_group_headers() -> None:
    for header in EXPECTED_HELP_HEADERS:
        assert f"<b>{header}</b>" in HELP_TEXT, f"missing help group header: {header}"


def test_help_text_renders_key_commands() -> None:
    for cmd in ("scan", "wallet", "price", "chart", "trace", "market", "news", "alerts", "chat"):
        assert f"/{cmd} " in HELP_TEXT or f"/{cmd}\n" in HELP_TEXT, f"key command /{cmd} missing from help text"


def test_help_text_renders_every_section_command() -> None:
    for _header, cmds in _HELP_SECTIONS:
        for cmd, desc in cmds:
            line = f"/{cmd} {desc}"
            assert line in HELP_TEXT, f"help text is missing line: {line!r}"


def test_help_text_html_tags_balanced() -> None:
    for tag in ("b", "code"):
        opens = len(re.findall(f"<{tag}>", HELP_TEXT))
        closes = len(re.findall(f"</{tag}>", HELP_TEXT))
        assert opens == closes, f"unbalanced <{tag}> tags in help text: {opens} opens vs {closes} closes"


def test_help_text_sections_unique() -> None:
    headers = [h for h, _ in _HELP_SECTIONS]
    assert len(headers) == len(set(headers))


# ── Main-menu inline keyboard ─────────────────────────────────────────────

# Every callback_data the main menu may emit must be handled in
# handlers/callback/__init__.py — do NOT add new ones without a handler.
KNOWN_CALLBACKS = {
    "menu_main",
    "menu_cat_admin",
    "menu_cat_scan",
    "menu_cat_wallet",
    "menu_cat_alerts",
    "menu_cat_intel",
    "menu_cat_charts",
    "menu_cat_rugmaps",
    "menu_cat_security",
    "menu_cat_market",
    "menu_cat_ai",
    "menu_cat_chat",
    "menu_cat_portfolio",
    "menu_cat_account",
    "menu_cat_help",
}


def _flatten_buttons(kb):
    return [btn for row in kb.inline_keyboard for btn in row]


def test_main_menu_structure_sane() -> None:
    kb = main_menu_kb(None)
    rows = kb.inline_keyboard
    assert rows, "main menu must have rows"
    # No empty rows; no rows wider than two buttons (Telegram UX convention).
    for row in rows:
        assert 1 <= len(row) <= 2, f"row has {len(row)} buttons, expected 1-2"
    # Most-used action first: the first row opens with Scan.
    first_text = rows[0][0].text
    assert "Scan" in first_text, f"first button should be Scan, got {first_text!r}"


def test_main_menu_has_help_and_no_orphan_callbacks() -> None:
    kb = main_menu_kb(None)
    texts = [btn.text for btn in _flatten_buttons(kb)]
    assert any("Help" in t for t in texts), "main menu must expose a Help button"
    for btn in _flatten_buttons(kb):
        if btn.callback_data is not None:
            assert btn.callback_data in KNOWN_CALLBACKS, f"orphan callback_data {btn.callback_data!r} has no handler"


def test_main_menu_owner_admin_button() -> None:
    owner_kb = main_menu_kb(7075336557)  # default owner from TELEGRAM_ALLOWED_USERS
    owner_texts = [btn.text for btn in _flatten_buttons(owner_kb)]
    assert any("Admin" in t for t in owner_texts), "owner main menu must show the Admin button"

    public_kb = main_menu_kb(123456789)
    public_texts = [btn.text for btn in _flatten_buttons(public_kb)]
    assert not any("Admin" in t for t in public_texts), "non-owner main menu must hide the Admin button"


def test_welcome_menu_matches_main_menu() -> None:
    # /help and /start attach the same main-menu keyboard.
    main = main_menu_kb(None).inline_keyboard
    welcome = welcome_menu_kb(None).inline_keyboard
    assert [[(b.text, b.callback_data, b.url) for b in row] for row in welcome] == [
        [(b.text, b.callback_data, b.url) for b in row] for row in main
    ]
