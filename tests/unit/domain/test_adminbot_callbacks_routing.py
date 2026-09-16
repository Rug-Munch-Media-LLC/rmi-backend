"""Behavioral routing tests for the adminbot callback dispatcher.

These tests exercise ``app.domains.telegram.adminbot.callbacks.handle_callback``
as a black box: a fake ``Update`` carrying each callback-data prefix is fed
through the dispatcher, with every admin_* module mocked, and we assert the
*signal* that proves the correct branch ran (a mocked admin function was
called, or an edit was produced with a distinctive marker).

Purpose: guard the 1269-line ``handle_callback`` split (callbacks.py → the
``callbacks/`` package) — the routing table below must pass identically
before and after the refactor.

The adminbot ``config`` module fatally exits without token env vars, so they
are seeded before any adminbot import.
"""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.telegram import access_guard
from app.domains.telegram.adminbot import (
    admin_ai,
    admin_audit,
    admin_bot,
    admin_docker,
    admin_health,
    admin_ops,
    admin_server,
    admin_terminal,
)
from app.domains.telegram.adminbot.callbacks import handle_callback

OWNER_ID = 7075336557  # default TELEGRAM_ALLOWED_USERS
ATTACKER_ID = 424242


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeUser:
    def __init__(self, user_id: int = OWNER_ID, username: str = "owner") -> None:
        self.id = user_id
        self.username = username


class _FakeChat:
    id = OWNER_ID
    type = "private"


class _FakeQuery:
    """Minimal callback_query double: records edit_message_text calls."""

    def __init__(self, data: str, user_id: int = OWNER_ID) -> None:
        self.data = data
        self.from_user = _FakeUser(user_id)
        self.message = Mock()
        self.edits: list[str] = []
        self.answered = False

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.edits.append(text)


class _FakeUpdate:
    def __init__(self, query: _FakeQuery) -> None:
        self.callback_query = query
        self.effective_user = query.from_user
        self.effective_chat = _FakeChat()


class _FakeContext:
    def __init__(self) -> None:
        self.bot = Mock()
        self.user_data = {}


def _ok(duration: bool = True, output: str = "OK") -> dict:
    d: dict = {"ok": True, "output": output, "error": ""}
    if duration:
        d["duration"] = 1.0
    return d


# ── Fixture: mock every admin_* surface the handlers touch ───────────

_SHORT = {
    admin_health: "health",
    admin_server: "server",
    admin_ops: "ops",
    admin_docker: "docker",
    admin_terminal: "terminal",
    admin_ai: "ai",
    admin_bot: "bot",
    admin_audit: "audit",
}


@pytest.fixture(autouse=True)
def admin_mocks():
    """Patch all admin_* module attributes used by callback handlers.

    Returns a dict of mocks keyed by short names (e.g. ``ops.trigger_backup``)
    so tests can assert calls or re-configure return values.
    """
    container: dict[str, Mock | AsyncMock] = {}
    patchers: list[patch] = []

    def install(mod, name: str, *, ret=None, async_: bool = False) -> Mock | AsyncMock:
        mk = AsyncMock(return_value=ret) if async_ else Mock(return_value=ret)
        patcher = patch.object(mod, name, mk)
        patcher.start()
        patchers.append(patcher)
        container[f"{_SHORT[mod]}.{name}"] = mk
        return mk

    # admin_health
    install(admin_health, "control_panel", ret={"qs": True})
    install(admin_health, "format_control_panel", ret="FORMAT:control_panel")
    install(admin_health, "quick_status", ret={"qs": True})
    install(admin_health, "format_quick_status", ret="FORMAT:quick_status")
    install(admin_health, "health_dashboard", ret={"dash": True})
    install(admin_health, "format_health_dashboard", ret="FORMAT:health_dashboard")

    # admin_server
    install(admin_server, "top_processes", ret={"procs": []})
    install(admin_server, "format_top", ret="FORMAT:top")
    install(admin_server, "uptime_report", ret={"up": True})
    install(admin_server, "format_uptime", ret="FORMAT:uptime")
    install(admin_server, "network_report", ret={"net": True})
    install(admin_server, "format_network", ret="FORMAT:network")
    install(admin_server, "cert_expiry", ret={"certs": []})
    install(admin_server, "format_certs", ret="FORMAT:certs")
    install(admin_server, "services_report", ret={"svcs": []})
    install(admin_server, "format_services", ret="FORMAT:services")
    install(admin_server, "restart_service", ret=_ok())
    install(admin_server, "service_logs", ret=_ok())
    install(admin_server, "service_control", ret=_ok())
    install(admin_server, "schedule_reboot", ret=_ok())

    # admin_ops
    install(admin_ops, "system_status", ret={"sys": True})
    install(admin_ops, "format_status", ret="FORMAT:status")
    install(admin_ops, "disk_usage", ret={"disk": True})
    install(admin_ops, "format_disk", ret="FORMAT:disk")
    install(admin_ops, "memory_report", ret={"mem": True})
    install(admin_ops, "format_memory", ret="FORMAT:memory")
    install(admin_ops, "chain_status", ret=_ok())
    install(admin_ops, "docker_ps", ret="container list")
    install(admin_ops, "docker_stats", ret=_ok())
    install(admin_ops, "docker_restart", ret=_ok())
    install(admin_ops, "docker_compose", ret=_ok())
    install(admin_ops, "git_status", ret=_ok())
    install(admin_ops, "push_all_repos", ret=_ok())
    install(admin_ops, "service_control", ret=_ok())
    install(admin_ops, "trigger_backup", ret=_ok())
    install(admin_ops, "backup_status", ret={"backups": []})
    install(admin_ops, "format_backup_status", ret="FORMAT:backup_status")
    install(admin_ops, "backup_verify", ret=_ok())
    install(admin_ops, "backup_report", ret={"output": "backup report"})
    install(admin_ops, "deploy_service", ret=_ok())
    install(admin_ops, "docker_logs", ret=_ok())
    install(admin_ops, "rag_stats", ret=_ok(output='{"key": 1}'))
    install(admin_ops, "news_feed", ret=_ok(output="[]"))
    install(admin_ops, "scan_stats", ret=_ok(output='{"metrics": {}, "sufficiency": {}}'))
    install(admin_ops, "indexer_status", ret=_ok())
    _p = patch.object(admin_ops, "RMI_REPOS", ["rmi-backend"])
    _p.start()
    patchers.append(_p)

    # admin_docker
    install(admin_docker, "docker_ps_table", ret={"containers": []})
    install(admin_docker, "format_docker_ps", ret="FORMAT:docker_ps")
    install(admin_docker, "docker_stats_top", ret={"stats": []})
    install(admin_docker, "format_docker_stats", ret="FORMAT:docker_stats")
    install(admin_docker, "docker_disk_usage", ret={"df": True})
    install(admin_docker, "format_docker_df", ret="FORMAT:docker_df")
    install(admin_docker, "docker_prune", ret=_ok())
    install(admin_docker, "docker_restart", ret=_ok())

    # admin_terminal
    install(admin_terminal, "quick_commands_keyboard", ret=Mock())
    install(admin_terminal, "touch", ret=None)
    install(admin_terminal, "run_quick", ret=_ok())
    install(admin_terminal, "exit_terminal", ret=None)

    # admin_ai
    install(admin_ai, "model_keyboard", ret=Mock())
    install(admin_ai, "list_models", ret="model list")
    install(admin_ai, "pop_oc_prompt", ret=None)
    install(admin_ai, "run_quick_task", ret=_ok())
    install(admin_ai, "run_opencode", ret=_ok())
    install(admin_ai, "cancel_oc_prompt", ret=None)
    install(admin_ai, "set_model", ret=None)
    install(admin_ai, "clear_history", ret=None)
    _p = patch.object(admin_ai, "MODELS", {"gpt4": {"name": "GPT-4"}})
    _p.start()
    patchers.append(_p)

    # admin_bot (async)
    install(admin_bot, "bot_stats", ret=_ok(), async_=True)
    install(admin_bot, "format_bot_stats", ret="FORMAT:bot_stats")
    install(admin_bot, "bot_users", ret={"output": "users", "error": ""})
    install(admin_bot, "bot_broadcasts", ret={"output": "broadcasts", "error": ""}, async_=True)
    install(admin_bot, "bot_toggle", ret=_ok(), async_=True)
    install(admin_bot, "bot_toggle_chat", ret=_ok(), async_=True)

    # admin_audit
    install(admin_audit, "log_action", ret=None)
    install(admin_audit, "recent_actions", ret=[])
    install(admin_audit, "format_admin_logs", ret="FORMAT:admin_logs")

    try:
        yield container
    finally:
        for p in patchers:
            p.stop()


@pytest.fixture
def q():
    """Build a fresh fake query for a callback data string."""

    def _make(data: str, user_id: int = OWNER_ID) -> _FakeQuery:
        return _FakeQuery(data, user_id)

    return _make


async def _run(data: str, user_id: int = OWNER_ID) -> _FakeQuery:
    query = _FakeQuery(data, user_id)
    await handle_callback(_FakeUpdate(query), _FakeContext())
    return query


# ── Routing table: every reachable callback-data prefix ──────────────

# (data, expected mock key OR None, edit-text marker OR None)
ROUTING_CASES = [
    # nav / status cards
    ("main", "health.control_panel", None),
    ("menu_ops", None, "select an action"),
    ("quick_status", "health.quick_status", None),
    ("health", "health.health_dashboard", None),
    ("top", "server.top_processes", None),
    ("top_mem", "server.top_processes", None),
    ("uptime", "server.uptime_report", None),
    ("net", "server.network_report", None),
    ("certs", "server.cert_expiry", None),
    ("services", "server.services_report", None),
    # systemd restart flow
    ("restart_confirm_rmi-backend", "server.restart_service", None),
    ("restart_cancel", None, "Restart cancelled"),
    # docker ps / stats / prune
    ("dps", "docker.docker_ps_table", None),
    ("dstats", "docker.docker_stats_top", None),
    ("dprune", "docker.docker_disk_usage", None),
    ("dprune_confirm", None, "FINAL CONFIRMATION"),
    ("dprune_run", "docker.docker_prune", None),
    ("dprune_cancel", None, "Prune cancelled"),
    # ai / opencode
    ("oc_help", None, "OpenCode Integration"),
    ("ai_menu", "ai.list_models", None),
    ("oc_cancel", "ai.cancel_oc_prompt", None),
    # ops: help / status / disk / mem / chain
    ("alert_help", None, "Send Alert"),
    ("status", "ops.system_status", None),
    ("disk", "ops.disk_usage", None),
    ("mem", "ops.memory_report", None),
    ("chain", "ops.chain_status", None),
    # git + push all
    ("git_menu", None, "Git Status"),
    ("git_rmi-backend", "ops.git_status", None),
    ("push_all", None, "Confirm push all"),
    ("push_confirm", None, "FINAL CONFIRMATION"),
    ("push_confirm2", "ops.push_all_repos", None),
    ("push_cancel", None, "Push cancelled"),
    # docker management / compose
    ("docker", None, "Docker Management"),
    ("docker_ps", "ops.docker_ps", None),
    ("docker_stats", "ops.docker_stats", None),
    ("docker_restart_rmi-backend", "ops.docker_restart", None),
    ("compose_ps", "ops.docker_compose", None),
    ("compose_restart", "ops.docker_compose", None),
    # shell / service control
    ("shell_help", None, "Shell Execution"),
    ("svc_help", None, "Service Control"),
    ("svc_logs_menu", None, "Service Logs"),
    ("svc_logs_rmi-backend", "server.service_logs", None),
    ("drestart_menu", None, "Restart Container"),
    ("drestart_pick_rmi-postgres", None, "Confirm restart"),
    ("svc_stop_confirm_rmi-backend", "server.service_control", None),
    ("svc_stop_cancel", None, "cancelled"),
    ("svc_start_rmi-backend", "ops.service_control", None),
    ("svc_stop_rmi-backend", None, "Confirm stop"),
    ("drestart_confirm_rmi-postgres", "docker.docker_restart", None),
    ("drestart_cancel", None, "Container restart cancelled"),
    # backup
    ("backup_run_full_confirm", "ops.trigger_backup", None),
    ("backup_run_full_cancel", None, "backup cancelled"),
    ("backup_status", "ops.backup_status", None),
    ("backup_verify", "ops.backup_verify", None),
    ("backup_menu", None, "Backup Control"),
    ("admin_logs", "audit.recent_actions", None),
    ("backup", None, "Backup Control"),
    ("backup_run", "ops.trigger_backup", None),
    ("backup_db", "ops.trigger_backup", None),
    # deploy
    ("deploy", None, "Deploy Services"),
    ("deploy_cancel", None, "Deploy cancelled"),
    ("deploy_confirm2_backend", "ops.deploy_service", None),
    ("deploy_confirm_backend", None, "FINAL CONFIRMATION"),
    ("deploy_backend", None, "Confirm deploy"),
    # logs
    ("logs_menu", None, "View Logs"),
    ("logs_rmi-backend", "ops.docker_logs", None),
    # alerts / stats
    ("alerts", "health.health_dashboard", None),
    ("ragstats", "ops.rag_stats", None),
    ("news", "ops.news_feed", None),
    ("scanstats", "ops.scan_stats", None),
    ("indexer", "ops.indexer_status", None),
    ("quick", None, "Quick Operations"),
    # terminal
    ("term_enter", None, "/term"),
    ("term_quick_menu", "terminal.quick_commands_keyboard", None),
    ("term_quick_restart", "terminal.run_quick", None),
    ("term_exit", "terminal.exit_terminal", None),
    # ai model
    ("ai_model_gpt4", "ai.set_model", None),
    ("ai_clear", "ai.clear_history", None),
    ("admin_backups", "ops.backup_report", None),
    # bot management
    ("bot_stats", "bot.bot_stats", None),
    ("bot_users", "bot.bot_users", None),
    ("bot_broadcast_help", None, "Bot Broadcast"),
    ("bot_broadcasts", "bot.bot_broadcasts", None),
    ("bot_toggle_menu", None, "RugMunchBot Toggle"),
    ("bot_toggle_on", "bot.bot_toggle", None),
    ("bot_toggle_off", "bot.bot_toggle", None),
    ("bot_chat_on", "bot.bot_toggle_chat", None),
    ("bot_chat_off", "bot.bot_toggle_chat", None),
    # reboot
    ("reboot_confirm2_60", "server.schedule_reboot", None),
    ("reboot_confirm_60", None, "FINAL CONFIRMATION"),
    ("reboot_cancel", None, "Reboot cancelled"),
]


@pytest.mark.parametrize(("data", "expected", "marker"), ROUTING_CASES, ids=[c[0] for c in ROUTING_CASES])
async def test_handle_callback_routes_each_prefix(
    admin_mocks, data: str, expected: str | None, marker: str | None
) -> None:
    query = await _run(data)
    assert query.answered, f"{data}: callback was not answered"
    assert query.edits, f"{data}: expected at least one edit_message_text"
    if expected is not None:
        mock = admin_mocks[expected]
        assert mock.called, f"{data}: expected {expected} to be called; edits={query.edits!r}"
    if marker is not None:
        assert any(marker in edit for edit in query.edits), f"{data}: expected edit containing {marker!r}"


# ── Argument-level routing checks ────────────────────────────────────


async def test_top_uses_cpu_sort(admin_mocks) -> None:
    await _run("top")
    admin_mocks["server.top_processes"].assert_called_with(limit=10, sort_by="cpu")


async def test_top_mem_uses_mem_sort(admin_mocks) -> None:
    await _run("top_mem")
    admin_mocks["server.top_processes"].assert_called_with(limit=10, sort_by="mem")


async def test_restart_confirm_audits(admin_mocks) -> None:
    await _run("restart_confirm_rmi-backend")
    admin_mocks["audit.log_action"].assert_called_with("service.restart", "rmi-backend", OWNER_ID)


async def test_dprune_run_audits(admin_mocks) -> None:
    await _run("dprune_run")
    admin_mocks["audit.log_action"].assert_called_with("docker.prune", "system prune -af --volumes", OWNER_ID)


async def test_svc_confirm_audits_and_controls(admin_mocks) -> None:
    await _run("svc_stop_confirm_rmi-backend")
    admin_mocks["audit.log_action"].assert_called_with("service.stop", "rmi-backend", OWNER_ID)
    admin_mocks["server.service_control"].assert_called_with("rmi-backend", "stop")


async def test_deploy_confirm2_deploys(admin_mocks) -> None:
    await _run("deploy_confirm2_backend")
    admin_mocks["audit.log_action"].assert_called_with("deploy", "backend", OWNER_ID)
    admin_mocks["ops.deploy_service"].assert_called_with("backend")


async def test_reboot_confirm2_schedules_with_delay(admin_mocks) -> None:
    await _run("reboot_confirm2_60")
    admin_mocks["audit.log_action"].assert_called_with("system.reboot", "delay=60s", OWNER_ID)
    admin_mocks["server.schedule_reboot"].assert_called_with(delay_seconds=60)


async def test_bot_toggle_enables_disables(admin_mocks) -> None:
    await _run("bot_toggle_on")
    admin_mocks["bot.bot_toggle"].assert_awaited_with(True)
    await _run("bot_toggle_off")
    admin_mocks["bot.bot_toggle"].assert_awaited_with(False)


async def test_bot_chat_toggle_enables_disables(admin_mocks) -> None:
    await _run("bot_chat_on")
    admin_mocks["bot.bot_toggle_chat"].assert_awaited_with(True)
    await _run("bot_chat_off")
    admin_mocks["bot.bot_toggle_chat"].assert_awaited_with(False)


async def test_ai_model_switch(admin_mocks) -> None:
    await _run("ai_model_gpt4")
    admin_mocks["ai.set_model"].assert_called_with(OWNER_ID, "gpt4")
    query = await _run("ai_model_gpt4")
    assert any("Switched to" in edit for edit in query.edits)


async def test_compose_ps_vs_restart(admin_mocks) -> None:
    await _run("compose_ps")
    admin_mocks["ops.docker_compose"].assert_called_with("ps")
    admin_mocks["ops.docker_compose"].reset_mock()
    await _run("compose_restart")
    admin_mocks["ops.docker_compose"].assert_called_with("restart")


async def test_backup_run_db_uses_db_type(admin_mocks) -> None:
    await _run("backup_db")
    admin_mocks["ops.trigger_backup"].assert_called_with("db")
    await _run("backup_run")
    admin_mocks["ops.trigger_backup"].assert_called_with("full")


async def test_backup_run_confirm_uses_type(admin_mocks) -> None:
    await _run("backup_run_full_confirm")
    admin_mocks["ops.trigger_backup"].assert_called_with("full")
    await _run("backup_run_db_confirm")
    admin_mocks["ops.trigger_backup"].assert_called_with("db")


# ── oc_confirm flow variants ─────────────────────────────────────────


async def test_oc_confirm_runs_prompt(admin_mocks) -> None:
    admin_mocks["ai.pop_oc_prompt"].return_value = {"prompt": "hello", "task": False}
    query = await _run("oc_confirm")
    admin_mocks["ai.run_opencode"].assert_called_once_with("hello", max_turns=3)
    admin_mocks["ai.run_quick_task"].assert_not_called()
    assert any("OpenCode" in edit for edit in query.edits)


async def test_oc_confirm_runs_task(admin_mocks) -> None:
    admin_mocks["ai.pop_oc_prompt"].return_value = {"prompt": "do thing", "task": True}
    query = await _run("oc_confirm")
    admin_mocks["ai.run_quick_task"].assert_called_once_with("do thing")
    admin_mocks["ai.run_opencode"].assert_not_called()
    assert any("OpenCode" in edit for edit in query.edits)


async def test_oc_confirm_expired(admin_mocks) -> None:
    admin_mocks["ai.pop_oc_prompt"].return_value = None
    query = await _run("oc_confirm")
    assert any("expired" in edit for edit in query.edits)
    admin_mocks["ai.run_opencode"].assert_not_called()
    admin_mocks["ai.run_quick_task"].assert_not_called()


# ── Ordering / shadowing regressions (must stay dead or exact-first) ─


async def test_svc_list_is_shadowed_by_generic_svc(admin_mocks) -> None:
    """'svc_list' hits the generic svc_* branch (len(parts)<3 → no-op).

    The exact `svc_list` branch is dead in the original chain — the split
    must keep it dead (do NOT reorder svc branches before the generic one).
    """
    query = await _run("svc_list")
    assert query.edits == [], "svc_list must stay a silent no-op (shadowed by generic svc_*)"
    admin_mocks["ops.service_control"].assert_not_called()
    admin_mocks["server.services_report"].assert_not_called()


async def test_git_menu_hits_exact_not_git_star(admin_mocks) -> None:
    query = await _run("git_menu")
    assert any("Git Status" in edit for edit in query.edits)
    admin_mocks["ops.git_status"].assert_not_called()


async def test_logs_menu_not_treated_as_log_service(admin_mocks) -> None:
    query = await _run("logs_menu")
    assert any("View Logs" in edit for edit in query.edits)
    admin_mocks["ops.docker_logs"].assert_not_called()


async def test_term_quick_menu_not_treated_as_quick_cmd(admin_mocks) -> None:
    query = await _run("term_quick_menu")
    assert any("Terminal Quick Commands" in edit for edit in query.edits)
    admin_mocks["terminal.run_quick"].assert_not_called()


async def test_deploy_confirm_not_swallowed_by_generic_deploy(admin_mocks) -> None:
    query = await _run("deploy_confirm_backend")
    assert any("FINAL CONFIRMATION" in edit for edit in query.edits)
    admin_mocks["ops.deploy_service"].assert_not_called()
    query2 = await _run("deploy_backend")
    assert any("Confirm deploy" in edit for edit in query2.edits)
    admin_mocks["ops.deploy_service"].assert_not_called()


async def test_quick_status_not_swallowed_by_quick(admin_mocks) -> None:
    query = await _run("quick_status")
    assert any("FORMAT:quick_status" in edit for edit in query.edits)
    admin_mocks["health.control_panel"].assert_not_called()


# ── Auth / edge behavior ─────────────────────────────────────────────


async def test_non_owner_is_denied_silently(admin_mocks) -> None:
    query = await _run("main", user_id=ATTACKER_ID)
    assert query.answered
    assert query.edits == [], "non-owner must get no reply (hidden bot)"
    admin_mocks["health.control_panel"].assert_not_called()


async def test_deny_attempt_alerts_owner() -> None:
    """The deny path actually calls access_guard (not just no-op)."""
    with patch.object(access_guard, "deny_admin_attempt", new=AsyncMock()) as deny:
        query = await _run("main", user_id=ATTACKER_ID)
    assert query.edits == []
    deny.assert_awaited_once()
    _, kwargs = deny.call_args
    assert kwargs["surface"] == "cb:main"


async def test_unknown_data_is_silent(admin_mocks) -> None:
    query = await _run("zzz_unknown")
    assert query.answered
    assert len(query.edits) == 1
    assert "Unhandled action" in query.edits[0]
    for key, mk in admin_mocks.items():
        if isinstance(mk, Mock):
            assert not mk.called, f"{key} should not be called for unknown data"


async def test_query_none_returns_early() -> None:
    update = _FakeQuery("main")
    update.callback_query = None  # type: ignore[assignment]
    await handle_callback(_FakeUpdate(update), _FakeContext())  # type: ignore[arg-type]
