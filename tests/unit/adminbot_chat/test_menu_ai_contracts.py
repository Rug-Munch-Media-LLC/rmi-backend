"""Contract tests for the adminbot menu and AI CLI bridge."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("JWT_SECRET", "test-secret-" * 8)
os.environ.setdefault("RAG_COLLECTIONS", '["codebase_reference_v2","security_patterns","threat_intel_v2"]')

from app.domains.telegram.adminbot import admin_ai
from app.domains.telegram.adminbot.tg_menu import CONSOLE_PATTERN, SECTIONS, build_menu, find_handler
from app.domains.telegram.adminbot.tg_menu_actions import LEAVES
from app.domains.telegram.adminbot.tg_menu_handlers import register


def test_menu_routes_are_registered_before_legacy_dispatcher() -> None:
    app = Mock()
    register(app)

    assert [call.kwargs["group"] for call in app.add_handler.call_args_list] == [-1, -1]
    assert app.add_handler.call_args_list[0].args[0].commands == {"menu"}
    assert app.add_handler.call_args_list[1].args[0].pattern.pattern == CONSOLE_PATTERN


def test_menu_actions_have_a_stable_dispatch_target() -> None:
    assert find_handler("srv:status") == ("srv", "status")
    assert find_handler("docker:ps") == ("docker", "ps")
    assert find_handler("docker:do:rmi-backend") == ("docker", "do:rmi-backend")
    assert find_handler("menu:refresh") == ("menu", "refresh")
    assert find_handler("noop") == ("menu", "noop")
    # Legacy control-panel data belongs to control_handlers (group -1), never here.
    assert find_handler("ctl_units") is None
    assert find_handler("unknown:action") is None
    assert build_menu() is not None


def test_every_board_button_resolves_to_a_real_leaf() -> None:
    """No dead buttons: every label in SECTIONS has a resolution AND a leaf."""
    for _title, buttons in SECTIONS:
        for _label, data in buttons:
            section, _, action = data.partition(":")
            assert find_handler(data) == (section, action), data
            assert (section, action) in LEAVES, f"{data} has no leaf action"


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
