"""
_ops_docker — docker inspection + compose deploy helpers.

Extracted from ``admin_ops`` (degodding 2026-08-14).
"""

from __future__ import annotations

import shlex
from typing import Any

from app.domains.telegram.adminbot._ops_core import _err, _run_subprocess, _validate_name


def docker_ps() -> str:
    result = _run_subprocess(
        ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
        timeout=30,
    )
    return result.get("output", "")


def docker_stats() -> dict[str, Any]:
    return _run_subprocess(["docker", "stats", "--no-stream"], timeout=30)


def docker_restart(container: str) -> dict[str, Any]:
    if not _validate_name(container):
        return _err(f"Invalid container name: '{container}'")
    return _run_subprocess(["docker", "restart", container], timeout=60)


def docker_compose(action: str) -> dict[str, Any]:
    try:
        parts = shlex.split(action)
    except ValueError as exc:
        return _err(f"Invalid action syntax: {exc}")
    return _run_subprocess(["docker", "compose", *parts], timeout=120)


def docker_logs(service: str, tail: int = 50) -> dict[str, Any]:
    if not _validate_name(service):
        return _err(f"Invalid service name: '{service}'")
    return _run_subprocess(
        ["docker", "logs", service, "--tail", str(tail), "--timestamps"],
        timeout=30,
    )


def deploy_service(name: str) -> dict[str, Any]:
    """Deploy a service by name."""
    if not _validate_name(name):
        return _err(f"Invalid service name: '{name}'")
    return _run_subprocess(["docker", "compose", "up", "-d", name], timeout=120)
