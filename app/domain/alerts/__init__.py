"""Alerts domain — public API + health check registration."""
from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


def _read_env_file() -> dict[str, str]:
    """Read env from /proc/self/environ (process env, not the mutable os.environ)."""
    env: dict[str, str] = {}
    try:
        with open("/proc/self/environ", "rb") as f:
            for chunk in f.read().split(b"\x00"):
                if b"=" in chunk:
                    k, _, v = chunk.partition(b"=")
                    env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    except Exception:
        pass
    return env


async def _health_check() -> DomainHealth:
    """Alerts health: Redis reachable via process env (not mutable os.environ)."""
    import redis as redis_lib

    env = _read_env_file()
    host = env.get("REDIS_HOST", "rmi-redis")
    port = int(env.get("REDIS_PORT", "6379"))
    password = env.get("REDIS_PASSWORD", "") or None

    try:
        client = redis_lib.Redis(
            host=host,
            port=port,
            password=password,
            db=int(env.get("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        return DomainHealth(
            name="alerts",
            healthy=True,
            details={"redis": "ok", "host": host, "port": port},
        )
    except Exception as e:
        return DomainHealth(
            name="alerts",
            healthy=False,
            details={"host": host, "port": port},
            error=str(e),
        )


health_mod.register_health_check("alerts", _health_check)


# Public API
from app.domain.alerts.broadcaster import AlertBroadcaster
from app.domain.alerts.models import (
    AlertEvent,
    AlertSubscription,
    AlertType,
    CreateAlertRequest,
)
from app.domain.alerts.repository import AlertRepository
from app.domain.alerts.service import AlertService

__all__ = [
    "AlertBroadcaster",
    "AlertEvent",
    "AlertRepository",
    "AlertService",
    "AlertSubscription",
    "AlertType",
    "CreateAlertRequest",
]
