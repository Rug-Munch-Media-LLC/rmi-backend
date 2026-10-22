"""Destructive server-control actions — executed ONLY after a confirm step.

Every function here mutates state. They are never called from a button
directly: ``control_handlers`` renders a confirm keyboard first, and only the
``*_confirm`` callback reaches this module. Each call is audited.

The confirm token is a short random string minted at render time and echoed
back in the callback_data, so a stale button from an old message cannot fire a
different action than the one the operator saw.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from app.domains.telegram.adminbot.admin_ops import _run_subprocess
from app.domains.telegram.adminbot.control_ops import DATA_OPS, valid_unit

#: Confirm tokens expire after this many seconds (a stale tap is refused).
CONFIRM_TTL_SECONDS = 120

#: token -> (action, kind, name, minted_at). In-process; the bot is single-process.
_PENDING: dict[str, tuple[str, str, str, float]] = {}


def mint_token(action: str, kind: str, name: str) -> str:
    """Mint a short-lived confirm token for one (action, unit) pair."""
    token = secrets.token_hex(4)
    _PENDING[token] = (action, kind, name, time.time())
    # Opportunistic GC so the map cannot grow without bound.
    cutoff = time.time() - CONFIRM_TTL_SECONDS
    for stale in [t for t, v in _PENDING.items() if v[3] < cutoff]:
        _PENDING.pop(stale, None)
    return token


def consume_token(token: str) -> tuple[str, str, str] | None:
    """Validate + burn a confirm token. None when unknown or expired."""
    entry = _PENDING.pop(token, None)
    if entry is None:
        return None
    action, kind, name, minted = entry
    if time.time() - minted > CONFIRM_TTL_SECONDS:
        return None
    return action, kind, name


def _audit(action: str, detail: str, user_id: int) -> None:
    try:
        from app.domains.telegram.adminbot import admin_audit

        admin_audit.log_action(action, detail, user_id)
    except Exception:  # noqa: S110 — audit is best-effort; never block the action
        pass


def run_unit_action(action: str, kind: str, name: str, user_id: int) -> dict[str, Any]:
    """Execute start/stop/restart/disable on a docker container or systemd unit."""
    if not valid_unit(name):
        return {"ok": False, "output": "", "error": f"invalid unit '{name}'", "duration": 0.0}
    if action not in ("start", "stop", "restart", "disable"):
        return {"ok": False, "output": "", "error": f"invalid action '{action}'", "duration": 0.0}
    if action == "disable" and kind != "systemd":
        return {"ok": False, "output": "", "error": "disable applies to systemd units, not containers", "duration": 0.0}
    _audit(f"unit.{action}", f"{kind}:{name}", user_id)
    if kind == "docker":
        return _run_subprocess(["docker", action, name], timeout=90)
    return _run_subprocess(["systemctl", action, name], timeout=90)


def run_data_op(op_key: str, user_id: int) -> dict[str, Any]:
    """Trigger a data-op systemd unit (oneshot) by its short key."""
    unit = next((u for k, _label, u in DATA_OPS if k == op_key), None)
    if unit is None or not valid_unit(unit):
        return {"ok": False, "output": "", "error": f"unknown data op '{op_key}'", "duration": 0.0}
    _audit("data.trigger", unit, user_id)
    # --no-block: the unit runs detached; the operator gets the start ack, not
    # a 10-minute hang waiting for a rollout to finish.
    return _run_subprocess(["systemctl", "start", "--no-block", f"{unit}.service"], timeout=20)
