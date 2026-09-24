"""
admin_ai — AI model gateway for the RMI Admin Bot.

Provides OpenCode CLI integration and general AI query capability
using the fleet's AI providers via opencode CLI.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any

from app.domains.telegram.adminbot import owner_persona

logger = logging.getLogger("admin_bot.admin_ai")

# Models available via OpenCode
MODELS: dict[str, dict[str, str]] = {
    "deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "provider": "9Router"},
    "deepseek-v4-pro": {"name": "DeepSeek V4 Pro", "provider": "9Router"},
    "qwen3-coder": {"name": "Qwen3 Coder 1M", "provider": "Alibaba"},
    "nemotron-ultra": {"name": "Nemotron Ultra 550B", "provider": "OpenCode"},
}

# The Telegram UI keeps stable short keys, while the CLI requires a fully
# qualified provider/model id.  Short ids such as ``deepseek-v4-flash`` are
# rejected by opencode 1.18.x and were the reason every AI request failed.
MODEL_IDS: dict[str, str] = {
    "deepseek-v4-flash": "9router/ds/deepseek-v4-flash",
    "deepseek-v4-pro": "9router/ds/deepseek-v4-pro",
    "qwen3-coder": "alibaba/qwen3-coder-480b-a35b-instruct",
    "nemotron-ultra": "opencode/nemotron-3-ultra-free",
}

DEFAULT_MODEL = "deepseek-v4-flash"
OPENCODE_BIN = "/usr/local/bin/opencode"
OPENCODE_TIMEOUT = 120

# Read-only opencode agent (no write/bash/tool side effects).
READ_ONLY_AGENT = "plan"
# Tooled /oc confirmations expire after this many seconds.
OC_CONFIRM_TTL = 300  # 5 minutes

# Per-user state
_user_sessions: dict[int, dict[str, Any]] = {}

# Pending /oc (tooled) confirmations: user_id -> {prompt, task, ts}
_pending_oc: dict[int, dict[str, Any]] = {}


def _opencode_path() -> str:
    """Find opencode binary."""
    candidates = [
        OPENCODE_BIN,
        "/usr/bin/opencode",
        "/root/.local/bin/opencode",
        "/home/ops/.local/bin/opencode",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return OPENCODE_BIN


# ═══════════════════════════════════════════════
# USER STATE
# ═══════════════════════════════════════════════


def get_session(user_id: int) -> dict[str, Any]:
    """Get or create a user session."""
    if user_id not in _user_sessions:
        _user_sessions[user_id] = {
            "model": DEFAULT_MODEL,
            "history": [],
            "tokens": 0,
            "messages": 0,
        }
    return _user_sessions[user_id]


def set_model(user_id: int, model_id: str) -> None:
    if model_id in MODELS:
        get_session(user_id)["model"] = model_id


def clear_history(user_id: int) -> None:
    s = get_session(user_id)
    s["history"] = []
    s["messages"] = 0


def get_usage(user_id: int) -> dict[str, Any]:
    s = get_session(user_id)
    return {"model": s["model"], "tokens": s["tokens"], "messages": s["messages"]}


# ═══════════════════════════════════════════════
# TOOLED /OC CONFIRMATION GATE
# ═══════════════════════════════════════════════


def stage_oc_prompt(user_id: int, prompt: str, task: bool = False) -> None:
    """Stage a tooled opencode prompt awaiting explicit confirmation."""
    _pending_oc[user_id] = {"prompt": prompt, "task": task, "ts": time.time()}


def pop_oc_prompt(user_id: int) -> dict[str, Any] | None:
    """Pop a pending /oc prompt if confirmed within TTL, else drop it."""
    entry = _pending_oc.pop(user_id, None)
    if entry is None:
        return None
    if time.time() - entry["ts"] > OC_CONFIRM_TTL:
        return None
    return entry


def cancel_oc_prompt(user_id: int) -> None:
    """Discard any pending /oc prompt."""
    _pending_oc.pop(user_id, None)


def list_models() -> str:
    lines = ["🤖 *Available AI Models*\n"]
    for mid, info in MODELS.items():
        lines.append(f"• `{mid}` — {info['name']} ({info['provider']})")
    lines.append("\nUse `/ai ask <prompt>` for a read-only query, or `/oc <prompt>` for a confirmed tooled run.")
    return "\n".join(lines)


def model_keyboard() -> Any:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons: list[list[Any]] = []
    row: list[Any] = []
    for mid in MODELS:
        row.append(InlineKeyboardButton(MODELS[mid]["name"], callback_data=f"ai_model_{mid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🧹 Clear History", callback_data="ai_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


# ═══════════════════════════════════════════════
# OPENCODE INTEGRATION
# ═══════════════════════════════════════════════


def run_opencode(
    prompt: str,
    model: str = DEFAULT_MODEL,
    max_turns: int = 3,
    *,
    read_only: bool = False,
) -> dict[str, Any]:
    """Run a prompt through OpenCode CLI.

    Uses the fleet's model routing — deepseek-v4-flash for quick tasks,
    deepseek-v4-pro for complex reasoning.

    ``read_only=True`` runs the prompt under the read-only ``plan`` agent
    (no write/bash/tool side effects) — the default for ``/ai``.
    """
    # Kept in the public signature for compatibility with older callers; the
    # current opencode CLI controls turn limits through its run configuration.
    del max_turns
    t0 = time.time()
    prompt = owner_persona.apply(prompt)

    opencode = _opencode_path()
    if not os.path.exists(opencode):
        return {
            "ok": False,
            "output": "",
            "error": f"OpenCode binary not found (searched: {opencode})",
            "duration": 0.0,
            "model": model,
        }

    # opencode 1.18.x uses the explicit ``run`` subcommand.  The old
    # ``--prompt/--max-turns/--format text`` invocation was an invalid CLI
    # combination, so every /ai request failed before reaching a provider.
    resolved_model = MODEL_IDS.get(model, model)
    cmd = [
        opencode,
        "run",
        "--pure",
        "--format",
        "default",
        "--model",
        resolved_model,
    ]
    if read_only:
        cmd += ["--agent", READ_ONLY_AGENT]
    cmd.append(prompt)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=OPENCODE_TIMEOUT, shell=False)
        duration = time.time() - t0
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return {
            "ok": result.returncode == 0,
            "output": output,
            "error": result.stderr if result.returncode != 0 else "",
            "duration": duration,
            "model": model,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "error": f"OpenCode timed out ({OPENCODE_TIMEOUT}s)",
            "duration": time.time() - t0,
            "model": model,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "output": "",
            "error": "OpenCode binary not found",
            "duration": 0.0,
            "model": model,
        }
    except (PermissionError, OSError, ValueError) as e:
        return {
            "ok": False,
            "output": "",
            "error": str(e),
            "duration": time.time() - t0,
            "model": model,
        }


def run_quick_task(task: str) -> dict[str, Any]:
    """Run a quick server management task via OpenCode."""
    prompt = (
        f"You are an RMI server admin assistant. Execute this task concisely:\n\n{task}\n\n"
        "Provide a brief summary of what was done and the results."
    )
    return run_opencode(prompt, model=DEFAULT_MODEL, max_turns=2)


# ═══════════════════════════════════════════════
# CHAT
# ═══════════════════════════════════════════════


async def chat(
    user_id: int,
    user_prompt: str,
    system_prompt: str = "You are an RMI server admin assistant. Be concise and technical.",
) -> dict[str, Any]:
    """Send a chat message to the selected AI model.

    This delegates to opencode CLI with the user's selected model.
    Returns the model response.
    """
    session = get_session(user_id)
    model = session["model"]

    full_prompt = f"{system_prompt}\n\nUser: {user_prompt}"
    # /ai runs READ-ONLY (plan agent, no tools) + single turn.
    result = run_opencode(full_prompt, model=model, max_turns=1, read_only=True)

    if result["ok"]:
        session["messages"] += 1
        session["tokens"] += len(result["output"].split()) * 2  # rough estimate

    return {
        "ok": result["ok"],
        "content": result["output"],
        "error": result.get("error", ""),
        "model": model,
        "tokens": session["tokens"],
        "messages": session["messages"],
    }
