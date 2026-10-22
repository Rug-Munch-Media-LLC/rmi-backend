"""Tests for the /diag PASS/FAIL renderer (pure formatting)."""

from __future__ import annotations

from app.domains.telegram.adminbot.cmd_audit.format import MAX_CHARS, format_diag

ROWS = [
    ("start", "PASS", 4, ""),
    ("status", "PASS", 12, ""),
    ("health", "FAIL", 30, "KeyError: 'fleet'"),
    ("logs", "TIMEOUT", 8000, ">8s"),
    ("selftest", "SKIP", 0, "budget spent"),
]


def test_counts_pass_and_flags_failures() -> None:
    text = format_diag(ROWS, 9.4)
    assert "2/5 pass" in text
    assert "9.4s" in text
    assert "❌ /health — KeyError: 'fleet'" in text
    assert "⏰ /logs — >8s" in text
    assert "⏭ /selftest" in text
    assert "Failing: /health, /logs" in text


def test_all_pass_has_no_failing_line() -> None:
    text = format_diag([("start", "PASS", 3, ""), ("status", "PASS", 8, "")], 1.2)
    assert "2/2 pass" in text
    assert "Failing:" not in text


def test_skips_are_reported_as_partial() -> None:
    text = format_diag([("start", "PASS", 1, ""), ("news", "SKIP", 0, "budget spent")], 50.0)
    assert "skipped (time budget)" in text
    assert "1/2 pass" in text


def test_output_is_capped_for_telegram() -> None:
    rows = [(f"cmd{index}", "FAIL", 1, "X" * 80) for index in range(200)]
    assert len(format_diag(rows, 1.0)) <= MAX_CHARS
