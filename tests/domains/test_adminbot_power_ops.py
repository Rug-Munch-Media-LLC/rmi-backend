"""Tests for power ops pure parts (P5) — formatting + keyboard wiring."""

from __future__ import annotations

from app.domains.telegram.adminbot import power_ops


def test_format_heavy_status_active_with_health() -> None:
    text = power_ops.format_heavy_status("active", {"models": [{"name": "ff711"}]})
    assert "ACTIVE" in text
    assert "8014" in text


def test_format_heavy_status_loading() -> None:
    text = power_ops.format_heavy_status("active", None)
    assert "loading" in text


def test_format_heavy_status_parked() -> None:
    text = power_ops.format_heavy_status("inactive", None)
    assert "parked" in text
    assert "/heavy start" in text


def test_keyboards_have_back_button() -> None:
    for kb in (power_ops.heavy_keyboard(), power_ops.community_keyboard()):
        flat = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "main" in flat


def test_community_units_cover_bot_and_bridge() -> None:
    assert "rugmunchbot" in power_ops.COMMUNITY_UNITS
    assert "chat-bridge" in power_ops.COMMUNITY_UNITS
