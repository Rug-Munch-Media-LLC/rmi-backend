"""Unit tests for the RugMunchBot engagement layer (v11).

Covers the additive UX-polish pieces:

  * ``handlers/_engagement.py`` — rotating footer CTA: deterministic,
    one-line, HTML-safe, idempotent append;
  * ``handlers/onboarding.py`` — first-run detection heuristic + welcome text;
  * ``keyboards.py`` — quick-start keyboard, daily-hook keyboard, additive
    menu merge;
  * ``tools/menus.py`` — capability menu extension reflecting the newest
    surfaces (Market Condition, News, Alpha & Whales).

Pure logic only — no network, no DB, no Telegram API.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.domains.telegram.rugmunchbot.handlers._engagement import (
    FOOTER_CTA_POOL,
    append_footer_cta,
    footer_cta_for,
    footer_cta_index,
    rotating_cta_for,
)
from app.domains.telegram.rugmunchbot.handlers.onboarding import (
    DAILY_HOOK_ACTIONS,
    FIRST_RUN_WINDOW_SECONDS,
    ONBOARDING_QUICK_START,
    is_first_run,
    welcome_message,
)
from app.domains.telegram.rugmunchbot.keyboards import daily_hook_kb, merge_menu_kb, quick_start_kb
from app.domains.telegram.rugmunchbot.tools import menus

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup

# ══════════════════════════════════════════════════════════════════════════
# rotating footer CTA (_engagement)
# ══════════════════════════════════════════════════════════════════════════


class TestFooterCta:
    def test_pool_is_curated_and_well_formed(self) -> None:
        assert len(FOOTER_CTA_POOL) >= 4
        for emoji, phrase, command in FOOTER_CTA_POOL:
            assert emoji
            assert phrase
            assert command
            assert command.startswith("/"), f"CTA command must be a /command: {command!r}"
            assert "<" not in phrase
            assert ">" not in phrase, "CTA pool must be HTML-safe"

    def test_renders_exactly_one_line(self) -> None:
        line = footer_cta_for("seed")
        assert "\n" not in line, "CTA must be a single line"
        assert line.count(":") == 1, "CTA format: '<emoji> <phrase>: <command>'"

    def test_deterministic_rotation(self) -> None:
        assert footer_cta_for("user:2026-08-14") == footer_cta_for("user:2026-08-14")
        assert footer_cta_index("a") == footer_cta_index("a")
        # Different seeds should (almost always) rotate to different slots;
        # assert only that the index is in range and stable.
        assert 0 <= footer_cta_index("anything") < len(FOOTER_CTA_POOL)

    def test_rotating_cta_for_is_stable_within_a_day(self) -> None:
        a = rotating_cta_for(123)
        b = rotating_cta_for(123)
        assert a == b
        assert "\n" not in a

    def test_append_footer_cta_is_idempotent(self) -> None:
        base = "Hello world"
        once = append_footer_cta(base, seed=1)
        twice = append_footer_cta(once, seed=1)
        assert once != base
        assert twice == once, "must never double-append the CTA"


# ══════════════════════════════════════════════════════════════════════════
# onboarding (first-run detection + welcome text)
# ══════════════════════════════════════════════════════════════════════════


class TestOnboarding:
    def _fresh_row(self, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "user_id": 1,
            "created_at": int(time.time()),
            "total_scans": 0,
            "total_ai_msgs": 0,
            "first_name": "Test",
        }
        row.update(overrides)
        return row

    def test_fresh_row_is_first_run(self) -> None:
        assert is_first_run(self._fresh_row()) is True

    def test_old_row_is_returning_user(self) -> None:
        row = self._fresh_row(created_at=int(time.time()) - FIRST_RUN_WINDOW_SECONDS - 1000)
        assert is_first_run(row) is False

    def test_row_with_usage_is_not_first_run(self) -> None:
        assert is_first_run(self._fresh_row(total_scans=1)) is False
        assert is_first_run(self._fresh_row(total_ai_msgs=1)) is False

    def test_row_without_created_at_is_not_first_run(self) -> None:
        row = self._fresh_row(created_at=None)  # type: ignore[arg-type]  # heuristic handles missing
        assert is_first_run(row) is False

    def test_welcome_message_has_try_me_line(self) -> None:
        text = welcome_message("Alice")
        assert "Alice" in text
        assert "try me" in text.lower()
        assert "$BONK" in text
        assert "Quick start:" in text

    def test_welcome_message_is_html_safe(self) -> None:
        text = welcome_message("Alice")
        assert text.count("<b>") == text.count("</b>")
        assert text.count("<i>") == text.count("</i>")
        assert "ALL CAPS" not in text.upper()  # no shouting

    def test_quick_start_actions_are_existing_callbacks(self) -> None:
        callbacks = {cb for _label, cb in ONBOARDING_QUICK_START}
        assert callbacks == {"run_scan", "run_trending", "menu_cat_help"}

    def test_daily_hook_actions_are_existing_callbacks(self) -> None:
        callbacks = {cb for _label, cb in DAILY_HOOK_ACTIONS}
        assert callbacks == {"run_trending", "run_market"}


# ══════════════════════════════════════════════════════════════════════════
# keyboards — quick-start, daily hook, additive merge
# ══════════════════════════════════════════════════════════════════════════


class TestEngagementKeyboards:
    def _flat(self, kb: InlineKeyboardMarkup) -> list[str]:
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    def test_quick_start_kb_has_three_buttons(self) -> None:
        kb = quick_start_kb()
        callbacks = self._flat(kb)
        assert len(callbacks) == 3
        assert set(callbacks) == {"run_scan", "run_trending", "menu_cat_help"}

    def test_daily_hook_kb_has_two_buttons(self) -> None:
        kb = daily_hook_kb()
        callbacks = self._flat(kb)
        assert len(callbacks) == 2
        assert set(callbacks) == {"run_trending", "run_market"}

    def test_merge_menu_kb_is_additive(self) -> None:
        base = quick_start_kb()
        hook = daily_hook_kb()
        merged = merge_menu_kb(base, hook)
        assert self._flat(merged) == self._flat(base) + self._flat(hook)
        # original keyboards are not mutated
        assert len(self._flat(base)) == 3
        assert len(self._flat(hook)) == 2


# ══════════════════════════════════════════════════════════════════════════
# tools/menus — capability list extension
# ══════════════════════════════════════════════════════════════════════════


class TestMenusExtension:
    def test_newest_capabilities_are_surfaced(self) -> None:
        labels = " | ".join(c.label for c in menus.CAPABILITIES)
        assert "Market Condition" in labels
        assert "News" in labels
        assert "Alpha & Whales" in labels
        # Chart + chart patterns (folded into TA) reflect the chart/TA tools.
        assert "Chart" in labels
        assert "chart patterns" in " | ".join(c.description for c in menus.CAPABILITIES)
        # Technical Analysis + Prediction Markets already present (TA /
        # Prediction markets labels keep the pre-existing substring checks).
        assert "TA" in labels
        assert "Prediction" in labels

    def test_capability_callbacks_are_distinct(self) -> None:
        callbacks = [c.callback for c in menus.CAPABILITIES]
        assert len(callbacks) == len(set(callbacks)), "capability callbacks must be unique"

    def test_capability_callbacks_are_known_surfaces(self) -> None:
        # Every menu callback must resolve to an existing dispatcher path:
        # run_* handled by handlers/callback, menu_cat_chat by the chat card.
        known = {
            "run_scan",
            "run_trending",
            "run_price",
            "run_chart",
            "run_ta",
            "run_polymarket",
            "run_market",
            "run_news",
            "run_whales",
            "run_smartmoney",
            "menu_cat_chat",
        }
        callbacks = {c.callback for c in menus.CAPABILITIES}
        assert callbacks <= known
        # Every capability still resolves through the lookup helper.
        for c in menus.CAPABILITIES:
            assert menus.capability_by_callback(c.callback) is c

    def test_menu_keyboard_keeps_back_row(self) -> None:
        kb = menus.capability_menu_kb()
        flat = [b for row in kb.inline_keyboard for b in row]
        assert len(flat) == len(menus.CAPABILITIES) + 1
        assert flat[-1].callback_data == "menu_main"
