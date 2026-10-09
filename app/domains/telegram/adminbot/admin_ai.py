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
from app.domains.telegram.adminbot.ai_session import (
    get_session,
)

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


# ════════════════════════════════════════════════
# OPENCODE INTEGRATION
# ════════════════════════════════════════════════


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


# ════════════════════════════════════════════════
# CHAT
# ════════════════════════════════════════════════


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
