"""Tests for the adminbot server-control surface (control_ops/actions/keyboards).

Locks the two invariants that matter:
  1. Every button the keyboards emit is handled by the router (0 orphans).
  2. A destructive action is unreachable without a valid, single-use confirm
     token — the confirm gate cannot be bypassed by a stale button.
"""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")
os.environ.setdefault("TELEGRAM_ALLOWED_USERS", "7075336557")

import time

from app.domains.telegram.adminbot import control_actions, control_keyboards, control_ops


def _emitted_callback_data() -> set[str]:
    """Every callback_data the control keyboards can produce."""
    datas: set[str] = set()
    for kb in (
        control_keyboards.units_keyboard(),
        control_keyboards.data_ops_keyboard(),
        control_keyboards.gates_keyboard(),
    ):
        for row in kb.inline_keyboard:
            for b in row:
                datas.add(b.callback_data)
    for kind, name, _label in control_ops.FLEET_UNITS:
        for row in control_keyboards.unit_card_keyboard(kind, name).inline_keyboard:
            for b in row:
                datas.add(b.callback_data)
    for row in control_keyboards.confirm_keyboard("restart", "docker", "rmi-backend", "deadbeef").inline_keyboard:
        for b in row:
            datas.add(b.callback_data)
    return datas


def _router_handles(data: str) -> bool:
    """Mirror the router's branch predicates (control_handlers.on_control_tap)."""
    if data in ("ctl_units", "ctl_gates", "ctl_dataops", "ctl_vitals", "ctl_failed"):
        return True
    if data.startswith(("ctl_unit_", "ctl_status_")):
        return control_ops.parse_unit_key(data.split("_", 2)[2]) is not None
    if data.startswith("ctl_logs_"):
        return control_ops.parse_unit_key(data[len("ctl_logs_") :]) is not None
    if data.startswith("ctl_ask_"):
        action, _, key = data[len("ctl_ask_") :].partition("_")
        return True if action == "trigger" else control_ops.parse_unit_key(key) is not None
    if data.startswith(("ctl_do_", "ctl_no_")):
        return True
    return False


def test_every_control_button_has_a_handler() -> None:
    """0 orphans: every emitted ctl_* callback is routed."""
    orphans = [d for d in _emitted_callback_data() if d.startswith("ctl_") and not _router_handles(d)]
    assert orphans == [], f"orphan buttons: {orphans}"


def test_parse_unit_key_rejects_malformed() -> None:
    assert control_ops.parse_unit_key("docker:rmi-backend") == ("docker", "rmi-backend")
    assert control_ops.parse_unit_key("systemd:rmiadminbot") == ("systemd", "rmiadminbot")
    assert control_ops.parse_unit_key("bad:x") is None
    assert control_ops.parse_unit_key("docker:") is None
    assert control_ops.parse_unit_key("docker:rmi backend") is None


def test_confirm_token_is_single_use() -> None:
    token = control_actions.mint_token("restart", "docker", "rmi-backend")
    assert control_actions.consume_token(token) == ("restart", "docker", "rmi-backend")
    # Second consume must fail — a replayed button cannot re-fire the action.
    assert control_actions.consume_token(token) is None


def test_confirm_token_expires() -> None:
    token = control_actions.mint_token("stop", "systemd", "rugmunchbot")
    action, kind, name, _minted = control_actions._PENDING[token]
    control_actions._PENDING[token] = (action, kind, name, time.time() - control_actions.CONFIRM_TTL_SECONDS - 1)
    assert control_actions.consume_token(token) is None


def test_unknown_token_is_refused() -> None:
    assert control_actions.consume_token("not-a-real-token") is None


def test_destructive_actions_are_declared() -> None:
    """The set the confirm gate protects must include every mutating verb."""
    assert {"start", "stop", "restart", "trigger"} <= control_actions.__dict__.get(
        "DESTRUCTIVE_ACTIONS", control_ops.DESTRUCTIVE_ACTIONS
    )


def test_run_unit_action_rejects_bad_input() -> None:
    assert control_actions.run_unit_action("restart", "docker", "bad name", 1)["ok"] is False
    assert control_actions.run_unit_action("nuke", "docker", "rmi-backend", 1)["ok"] is False


def test_run_data_op_rejects_unknown_key() -> None:
    assert control_actions.run_data_op("not-a-real-op", 1)["ok"] is False
