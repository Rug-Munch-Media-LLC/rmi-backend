"""Tests for the secrets scrub — pattern layer + value loader (no real files)."""

from __future__ import annotations

from app.domains.telegram.adminbot import secrets_scrub


def test_patterns_redact_common_formats() -> None:
    cases = [
        ("sk-abcdefghij0123456789", "[REDACTED]"),
        ("github_pat_" + "a" * 30, "[REDACTED]"),
        ("hf_" + "b" * 30, "[REDACTED]"),
        ("gsk_" + "c" * 30, "[REDACTED]"),
        ("AKIAIOSFODNN7EXAMPLE", "[REDACTED]"),
        ("bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", "bot<REDACTED>"),
    ]
    for raw, marker in cases:
        out = secrets_scrub.scrub(f"prefix {raw} suffix")
        assert raw not in out, f"not redacted: {raw[:12]}…"
        assert marker in out


def test_jwt_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"
    out = secrets_scrub.scrub(f"token={jwt}")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "[REDACTED-JWT]" in out


def test_value_loader_from_files(tmp_path) -> None:
    keys = tmp_path / "keys.env"
    keys.write_text("API_KEY=supersecretvalue123\nSHORT=x\n# comment\n")
    backend = tmp_path / "backend.env"
    backend.write_text("REDIS_PASSWORD=anothersecretvalue456\nHARMLESS=justtext\n")
    values = secrets_scrub.load_secret_values(str(keys), str(backend))
    assert "supersecretvalue123" in values
    assert "anothersecretvalue456" in values  # sensitive name in backend.env
    assert "justtext" not in values  # non-sensitive name skipped
    assert "x" not in values  # too short


def test_scrub_empty_and_safe_text() -> None:
    assert secrets_scrub.scrub("") == ""
    assert secrets_scrub.scrub("hello world") == "hello world"
