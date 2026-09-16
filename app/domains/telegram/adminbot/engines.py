"""Engine registry — run one-shot tasks on any agent engine in the stack.

The boss picks an engine with ``/engine`` and the choice is remembered
(Redis ``rmi:adminbot:engine``). ``/ask <prompt>`` runs through the current
engine. Every CLI engine is invoked as a plain subprocess with a hard
timeout; local lanes are OpenAI-compatible HTTP calls.

Engines:
  defiant  — local Defiant 9B lane            (127.0.0.1:8009, fast/private)
  ff711    — local Fable Fusion 711 27B lane  (127.0.0.1:8014, run /heavy first)
  opencode — opencode CLI (read-only agent)
  aider    — aider --message (no auto-commit)
  cline    — Cline terminal agent
  kilo     — Kilo CLI (kilo.ai gateway)
  kilocode — KiloCode CLI
  hermes   — Hermes agent one-shot (-z)

No monkeypatching anywhere: state goes through the same Redis helpers the
alerts feed uses; tests inject a fake client.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.domains.telegram.adminbot import redact

logger = get_logger("admin_bot.engines")

ENGINE_STATE_KEY = "rmi:adminbot:engine"
DEFAULT_ENGINE = "defiant"
CLI_TIMEOUT_SECONDS = int(os.getenv("ADMINBOT_ENGINE_TIMEOUT", "300"))
MAX_OUTPUT_CHARS = 3500

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class EngineSpec:
    """Static description of one runnable engine."""

    id: str
    label: str
    kind: str  # "cli" | "http"
    command: tuple[str, ...] = ()
    url: str = ""
    model: str = ""
    hint: str = ""


ENGINES: dict[str, EngineSpec] = {
    "defiant": EngineSpec(
        "defiant",
        "Defiant 9B (local)",
        "http",
        url="http://127.0.0.1:8009/v1/chat/completions",
        model="qwen3.6-35b-a3b",
        hint="fast · private · uncensored",
    ),
    "ff711": EngineSpec(
        "ff711",
        "FF711 27B (heavy)",
        "http",
        url="http://127.0.0.1:8014/v1/chat/completions",
        model="ff711",
        hint="deep quality · /heavy start first",
    ),
    "opencode": EngineSpec(
        "opencode",
        "OpenCode CLI",
        "cli",
        command=("/usr/bin/opencode", "run"),
        hint="full agent toolset",
    ),
    "aider": EngineSpec(
        "aider",
        "Aider",
        "cli",
        command=("/usr/local/bin/aider", "--message"),
        hint="pair programmer",
    ),
    "cline": EngineSpec(
        "cline",
        "Cline",
        "cli",
        command=("/usr/bin/cline",),
        hint="terminal agent",
    ),
    "kilo": EngineSpec(
        "kilo",
        "Kilo",
        "cli",
        command=("/usr/bin/kilo", "run"),
        hint="kilo.ai gateway",
    ),
    "kilocode": EngineSpec(
        "kilocode",
        "KiloCode",
        "cli",
        command=("/usr/bin/kilocode", "run"),
        hint="kilo.ai gateway",
    ),
    "hermes": EngineSpec(
        "hermes",
        "Hermes Agent",
        "cli",
        command=(
            "/usr/local/lib/hermes-agent/venv/bin/python",
            "-m",
            "hermes_cli.main",
            "-z",
        ),
        hint="fleet orchestrator",
    ),
}


def available(engine: EngineSpec) -> bool:
    """Availability probe: CLI binary present / HTTP always attempted."""
    if engine.kind == "http":
        return True
    return shutil.which(engine.command[0]) is not None or os.path.exists(engine.command[0])


def available_ids() -> list[str]:
    """Engine ids runnable on this host right now."""
    return [eid for eid, spec in ENGINES.items() if available(spec)]


async def _get_redis(redis_client: Any | None) -> tuple[Any | None, bool]:
    """Return (client, owns_client). Owned clients must be closed by caller."""
    if redis_client is not None:
        return redis_client, False
    try:
        import redis.asyncio as aioredis

        from app.domains.telegram.adminbot.redis_listener import (
            _build_redis_url,
            _resolve_redis_password,
        )

        url = _build_redis_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            _resolve_redis_password(),
        )
        return aioredis.from_url(url, decode_responses=True, protocol=2), True
    except Exception as exc:  # fail-open: engine choice falls back to default
        logger.debug("engine state redis unavailable: %s", exc)
        return None, False


async def get_current(redis_client: Any | None = None) -> str:
    """Current engine id (Redis-persisted; DEFAULT_ENGINE when unset)."""
    client, owns = await _get_redis(redis_client)
    if client is None:
        return DEFAULT_ENGINE
    try:
        value = await client.get(ENGINE_STATE_KEY)
        if value and str(value) in ENGINES:
            return str(value)
    except Exception as exc:
        logger.debug("engine state read failed: %s", exc)
    finally:
        if owns:
            await client.aclose()
    return DEFAULT_ENGINE


async def set_current(engine_id: str, redis_client: Any | None = None) -> bool:
    """Persist the engine choice. False for unknown ids or unavailable Redis."""
    if engine_id not in ENGINES:
        return False
    client, owns = await _get_redis(redis_client)
    if client is None:
        return False
    try:
        await client.set(ENGINE_STATE_KEY, engine_id)
        return True
    except Exception as exc:
        logger.warning("engine state write failed: %s", exc)
        return False
    finally:
        if owns:
            await client.aclose()


def truncate(output: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Trim engine output to a Telegram-friendly size."""
    text = (output or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… (truncated)"


async def run_engine(
    engine_id: str, prompt: str, *, timeout: int = CLI_TIMEOUT_SECONDS, prepend: str = ""
) -> dict[str, Any]:
    """Run one prompt through an engine.

    Returns ``{ok, engine, label, output, latency_ms}`` — never raises.
    """
    if prepend:
        prompt = f"{prepend}\n\n{prompt}"
    spec = ENGINES.get(engine_id)
    if spec is None:
        return {
            "ok": False,
            "engine": engine_id,
            "label": engine_id,
            "output": f"unknown engine: {engine_id}",
            "latency_ms": 0.0,
        }
    start = time.monotonic()
    if spec.kind == "http":
        ok, output = await _run_http(spec, prompt, timeout)
    else:
        ok, output = await _run_cli(spec, prompt, timeout)
    return {
        "ok": ok,
        "engine": spec.id,
        "label": spec.label,
        "output": output,
        "latency_ms": (time.monotonic() - start) * 1000.0,
    }


async def _run_http(spec: EngineSpec, prompt: str, timeout: int) -> tuple[bool, str]:
    """OpenAI-compatible call against a local llama.cpp lane."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=30.0)) as client:
            resp = await client.post(
                spec.url,
                json={
                    "model": spec.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1200,
                },
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content", "")
        return True, redact.scrub(truncate(content or "(empty)"))
    except Exception as exc:
        return False, f"engine unreachable: {str(exc)[:200]} (lane parked? try /heavy start)"


async def _run_cli(spec: EngineSpec, prompt: str, timeout: int) -> tuple[bool, str]:
    """Run a CLI engine as a subprocess with a hard timeout."""
    cmd = [*spec.command, prompt]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd="/root",
        )
    except FileNotFoundError:
        return False, f"engine binary missing: {cmd[0]}"
    except Exception as exc:
        return False, f"engine failed to start: {str(exc)[:200]}"

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"engine timed out after {timeout}s (still running? killed)"

    text = _ANSI_RE.sub("", stdout.decode("utf-8", errors="replace"))
    return proc.returncode == 0, redact.scrub(truncate(text or "(no output)"))
