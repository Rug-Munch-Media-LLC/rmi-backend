"""Contract tests for the adminbot menu and AI CLI bridge."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("JWT_SECRET", "test-secret-" * 8)
os.environ.setdefault("RAG_COLLECTIONS", '["codebase_reference_v2","security_patterns","threat_intel_v2"]')

from app.domains.telegram.adminbot import admin_ai
from app.domains.telegram.adminbot.tg_menu import build_menu, find_handler
from app.domains.telegram.adminbot.tg_menu_handlers import register


def test_menu_routes_are_registered_before_legacy_dispatcher() -> None:
    app = Mock()
    register(app)

    assert [call.kwargs["group"] for call in app.add_handler.call_args_list] == [-1, -1]
    assert app.add_handler.call_args_list[0].args[0].commands == {"menu"}
    assert app.add_handler.call_args_list[1].args[0].pattern.pattern == r"^(ai|ops|srv|fleet|menu):"


def test_menu_actions_have_a_stable_dispatch_target() -> None:
    assert find_handler("ops:status") == ("ops", "status")
    assert find_handler("ai:oc") == ("ai", "oc")
    assert find_handler("menu:refresh") == ("menu", "refresh")
    assert find_handler("unknown:action") is None
    assert build_menu() is not None


def test_admin_ai_uses_supported_opencode_run_cli(monkeypatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="OK", stderr=""))
    monkeypatch.setattr(admin_ai.subprocess, "run", run)
    monkeypatch.setattr(admin_ai, "_opencode_path", lambda: "/usr/local/bin/opencode")

    result = admin_ai.run_opencode("Reply with exactly OK", read_only=True)

    assert result["ok"] is True
    command = run.call_args.args[0]
    assert isinstance(command, list)
    assert command[0:2] == ["/usr/local/bin/opencode", "run"]
    assert "--prompt" not in command
    assert "--max-turns" not in command
    assert "9router/ds/deepseek-v4-flash" in command
    assert command[-1].endswith("Reply with exactly OK")
