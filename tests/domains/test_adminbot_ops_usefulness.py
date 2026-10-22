"""Regression tests: server-ops replies must be useful, budgeted, and gated.

Covers the 2026-10-23 fixes in ``admin_ops`` (budget + disk staging context),
``admin_docker`` (health/uptime in /dps), ``admin_server`` (failed-first
/services), ``_cmd_server`` (/svc_logs default), ``_cmd_docker`` (/dlogs
default, /dprune stays confirm-gated) and ``admin_health`` (systemd "●" parse).
"""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")

from typing import Any

from app.domains.telegram.adminbot import (
    _cmd_docker,
    _cmd_server,
    admin_docker,
    admin_health,
    admin_ops,
    admin_server,
    helpers,
)

# ═══════════════════════════════════════════════
# Stubs (no live bot, no live docker/systemd)
# ═══════════════════════════════════════════════


class _Chat:
    def __init__(self, chat_type: str) -> None:
        self.id = 7075336557
        self.type = chat_type


class _User:
    id = 7075336557
    username = "tester"
    first_name = "tester"
    is_bot = False


class _Msg:
    def __init__(self, sent: list[dict[str, Any]]) -> None:
        self._sent = sent

    async def reply_text(self, text: str = "", **kwargs: Any) -> "_Msg":
        self._sent.append({"text": str(text), "markup": kwargs.get("reply_markup")})
        return self

    async def edit_text(self, text: str = "", **kwargs: Any) -> "_Msg":
        self._sent.append({"text": str(text), "markup": kwargs.get("reply_markup")})
        return self


class _Update:
    def __init__(self, sent: list[dict[str, Any]], chat_type: str = "private") -> None:
        self.message = _Msg(sent)
        self.effective_message = self.message
        self.effective_chat = _Chat(chat_type)
        self.effective_user = _User()
        self.callback_query = None


class _Bot:
    async def send_message(self, *args: Any, **kwargs: Any) -> _Msg:
        return _Msg([])

    async def send_chat_action(self, *args: Any, **kwargs: Any) -> bool:
        return True


class _Ctx:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []
        self.bot = _Bot()
        self.user_data: dict[str, Any] = {}
        self.chat_data: dict[str, Any] = {}
        self.bot_data: dict[str, Any] = {}


async def _run(
    handler: Any, args: list[str] | None = None, chat_type: str = "private"
) -> list[dict[str, Any]]:
    helpers._last_msg.clear()  # bypass the 2s spam cooldown between cases
    sent: list[dict[str, Any]] = []
    await handler(_Update(sent, chat_type), _Ctx(args))
    return sent


# ═══════════════════════════════════════════════
# Reply budget
# ═══════════════════════════════════════════════


class TestClipText:
    def test_short_text_untouched(self) -> None:
        assert admin_ops.clip_text("hello") == "hello"

    def test_long_text_fits_one_message_and_reports_drops(self) -> None:
        out = admin_ops.clip_text("\n".join(f"line {i}" for i in range(500)))
        assert len(out) <= admin_ops.TELEGRAM_SAFE_LIMIT
        assert "more lines" in out

    def test_unbalanced_fence_is_closed(self) -> None:
        out = admin_ops.clip_text(
            "```\n" + "\n".join(f"row {i}" for i in range(500)) + "\n```", 300
        )
        assert out.count("```") % 2 == 0


# ═══════════════════════════════════════════════
# /dps — health, uptime, unhealthy first
# ═══════════════════════════════════════════════


class TestDockerPs:
    def test_status_parsing(self) -> None:
        assert admin_docker._health_from_status("Up 5 days (healthy)") == "healthy"
        assert admin_docker._health_from_status("Up 2 minutes (unhealthy)") == "unhealthy"
        assert admin_docker._health_from_status("Up 7 minutes") == ""
        assert admin_docker._uptime_from_status("Up 5 days (healthy)") == "Up 5 days"
        assert admin_docker._uptime_from_status("Up 7 minutes") == "Up 7 minutes"

    def test_unhealthy_flagged_first_and_budget_kept(self) -> None:
        rows = [
            {
                "name": f"c{i}",
                "status": "Up 1 day (healthy)",
                "health": "healthy",
                "uptime": "Up 1 day",
            }
            for i in range(300)
        ]
        rows.append(
            {
                "name": "sick",
                "status": "Up 2 minutes (unhealthy)",
                "health": "unhealthy",
                "uptime": "Up 2 minutes",
            }
        )
        text = admin_docker.format_docker_ps({"ok": True, "rows": rows})
        assert len(text) <= admin_ops.TELEGRAM_SAFE_LIMIT
        assert "UNHEALTHY (1)" in text
        assert text.index("sick") < text.index("c0")


# ═══════════════════════════════════════════════
# /services — failed first, never hidden, real names
# ═══════════════════════════════════════════════

_SYSTEMCTL = """\
● broken-a.service loaded failed failed Broken A
● broken-b.service loaded failed failed Broken B
  ok.service loaded active running Ok
  ok2.service loaded active running Ok2
  ok3.service loaded active running Ok3
"""


class TestServices:
    def test_failed_first_with_names_and_never_truncated(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            admin_server, "_run_subprocess", lambda *a, **k: {"ok": True, "output": _SYSTEMCTL}
        )
        result = admin_server.services_report(limit=1)
        names = [u["unit"] for u in result["rows"]]
        assert result["failed"] == 2
        assert names[:2] == ["broken-a.service", "broken-b.service"]
        assert len(names) > 2, "active units must still get a slot"

    def test_marker_never_swallows_the_unit_name(self) -> None:
        units = admin_health.parse_systemd_units(_SYSTEMCTL)
        assert units[0]["unit"] == "broken-a.service"

    def test_format_shows_failed_block(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            admin_server, "_run_subprocess", lambda *a, **k: {"ok": True, "output": _SYSTEMCTL}
        )
        text = admin_server.format_services(admin_server.services_report())
        assert "FAILED (2)" in text and "broken-a.service" in text
        assert len(text) <= admin_ops.TELEGRAM_SAFE_LIMIT


# ═══════════════════════════════════════════════
# Log defaults + confirmation gates
# ═══════════════════════════════════════════════


class TestCommandDefaults:
    async def test_svc_logs_defaults_to_admin_bot(self, monkeypatch: Any) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            admin_server,
            "service_logs",
            lambda name, lines: seen.append(name) or {"ok": True, "output": "line"},
        )
        sent = await _run(_cmd_server.cmd_svc_logs)
        assert seen == ["rmiadminbot"]
        assert "rmiadminbot" in sent[-1]["text"]

    async def test_dlogs_defaults_to_backend_container(self, monkeypatch: Any) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            admin_docker,
            "docker_logs_tail",
            lambda name, lines: seen.append(name) or {"ok": True, "output": "line"},
        )
        await _run(_cmd_docker.cmd_dlogs)
        assert seen == ["rmi-backend"]

    async def test_dprune_shows_confirm_path_not_a_prune(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(admin_docker, "docker_disk_usage", lambda: {"ok": True, "output": "df"})
        called: list[int] = []
        monkeypatch.setattr(admin_docker, "docker_prune", lambda: called.append(1))
        sent = await _run(_cmd_docker.cmd_dprune)
        assert called == []
        callbacks = [b.callback_data for row in sent[-1]["markup"].inline_keyboard for b in row]
        assert "dprune" in callbacks  # -> confirm keyboard, not the prune itself
        assert "dprune_run" not in callbacks

    async def test_destructive_command_refused_in_group(self, monkeypatch: Any) -> None:
        called: list[int] = []
        monkeypatch.setattr(admin_docker, "docker_restart", lambda name: called.append(1))
        sent = await _run(_cmd_docker.cmd_drestart, ["rmi-backend"], chat_type="group")
        assert sent == [] and called == []
