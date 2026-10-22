"""The command audit wrapper — one record per handler invocation."""

from __future__ import annotations

import functools
import time
from typing import Any

from .record import record_command

MARK = "__cmd_audit_wrapped__"


def command_name(text: str | None) -> str:
    """Extract `/command` from raw text (``@botname`` suffix stripped)."""
    if not text or not text.startswith("/"):
        return ""
    return text.split()[0][1:].split("@", 1)[0][:32]


def args_count(text: str | None) -> int:
    """Number of whitespace-separated arguments after the command."""
    return max(len(text.split()) - 1, 0) if text else 0


def _write(name: str, update: Any, started: float, status: str, exc: BaseException | None) -> None:
    """Persist one record from a completed (or failed) invocation."""
    user = getattr(update, "effective_user", None)
    text = getattr(getattr(update, "effective_message", None), "text", None)
    record_command(
        user_id=getattr(user, "id", None),
        command=name,
        args_count=args_count(text),
        duration_ms=int((time.perf_counter() - started) * 1000),
        status=status,
        error_type=type(exc).__name__ if exc else None,
        error_msg=str(exc) if exc else None,
    )


def audit_call(name: str, handler: Any) -> Any:
    """Wrap one handler so every call leaves exactly one audit record."""
    if getattr(handler, MARK, False):
        return handler

    @functools.wraps(handler)
    async def _wrapped(update: Any, ctx: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await handler(update, ctx)
        except TimeoutError as exc:
            _write(name, update, started, "timeout", exc)
            raise
        except Exception as exc:
            _write(name, update, started, "error", exc)
            raise
        _write(name, update, started, "ok", None)
        return result

    setattr(_wrapped, MARK, True)
    return _wrapped
