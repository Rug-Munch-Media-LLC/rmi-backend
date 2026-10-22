"""Tests for the durable adminbot command audit (writer, rotation, wrapper)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import TYPE_CHECKING

from app.domains.telegram.adminbot.cmd_audit.record import record_command
from app.domains.telegram.adminbot.cmd_audit.rotation import rotate_if_needed, week_key
from app.domains.telegram.adminbot.cmd_audit.wrap import args_count, audit_call, command_name

if TYPE_CHECKING:
    from pathlib import Path

FIELDS = {"ts", "user_id", "command", "args_count", "duration_ms", "status", "error_type", "error_msg"}


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class _User:
    id = 42


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text


class _Update:
    def __init__(self, text: str) -> None:
        self.effective_user = _User()
        self.effective_message = _Msg(text)


def test_record_writes_one_json_line(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "commands.jsonl"
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(log))
    record_command(user_id=7, command="status", args_count=2, duration_ms=5)
    rows = _rows(log)
    assert len(rows) == 1
    assert set(rows[0]) >= FIELDS
    assert rows[0]["command"] == "status"
    assert rows[0]["user_id"] == 7
    assert rows[0]["status"] == "ok"
    assert rows[0]["source"] == "handler"


def test_record_truncates_error_message(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(log))
    record_command(user_id=1, command="x", status="error", error_msg="E" * 500)
    assert len(_rows(log)[0]["error_msg"]) == 200


def test_record_never_raises_on_unwritable_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(tmp_path))  # a directory, not a file
    assert record_command(user_id=1, command="status")["status"] == "ok"


def test_unknown_status_degrades_to_error(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(log))
    assert record_command(user_id=1, command="x", status="banana")["status"] == "error"


def test_rotation_archives_previous_week(tmp_path: Path) -> None:
    log = tmp_path / "commands.jsonl"
    log.write_text("{}\n")
    old = time.time() - 14 * 86400
    os.utime(log, (old, old))
    rotate_if_needed(log)
    assert not log.exists()
    assert (tmp_path / f"commands-{week_key(old)}.jsonl").exists()


def test_rotation_keeps_four_archives(tmp_path: Path) -> None:
    for week in range(10, 15):
        (tmp_path / f"commands-2026-W{week}.jsonl").write_text("{}\n")
    log = tmp_path / "commands.jsonl"
    log.write_text("{}\n")
    old = time.time() - 14 * 86400
    os.utime(log, (old, old))
    rotate_if_needed(log)
    assert len(list(tmp_path.glob("commands-*.jsonl"))) == 4


def test_wrapper_records_ok(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(log))

    async def handler(update, ctx):
        return "done"

    assert asyncio.run(audit_call("status", handler)(_Update("/status now"), None)) == "done"
    row = _rows(log)[0]
    assert row["status"] == "ok"
    assert row["command"] == "status"
    assert row["args_count"] == 1


def test_wrapper_records_error_and_reraises(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(log))

    async def handler(update, ctx):
        raise RuntimeError("boom")

    with contextlib.suppress(RuntimeError):
        asyncio.run(audit_call("health", handler)(_Update("/health"), None))
    row = _rows(log)[0]
    assert row["status"] == "error"
    assert row["error_type"] == "RuntimeError"
    assert "boom" in row["error_msg"]


def test_wrapper_records_timeout(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "c.jsonl"
    monkeypatch.setenv("RMI_ADMINBOT_AUDIT_LOG", str(log))

    async def handler(update, ctx):
        raise TimeoutError

    with contextlib.suppress(TimeoutError):
        asyncio.run(audit_call("logs", handler)(_Update("/logs rmi-backend"), None))
    assert _rows(log)[0]["status"] == "timeout"


def test_command_parsing_helpers() -> None:
    assert command_name("/status@rmi_admin_bot now") == "status"
    assert command_name("plain text") == ""
    assert args_count("/logs rmi-backend 50") == 2
    assert args_count("/start") == 0
