"""Units, queues and lanes the self-test probes."""

from __future__ import annotations

SERVICE_UNITS: tuple[str, ...] = (
    "rugmunchbot",
    "rmiadminbot",
    "chat-bridge",
    "llm-guard",
    "hermes-gateway",
)

LANES: tuple[tuple[str, str], ...] = (
    ("rmi-backend api :8000", "http://127.0.0.1:8000/health"),
    ("snappy-2b :8010", "http://127.0.0.1:8010/health"),
    ("defiant-9b :8009", "http://127.0.0.1:8009/health"),
    ("ff711-heavy :8014", "http://127.0.0.1:8014/health"),
    ("embed :8004", "http://127.0.0.1:8004/health"),
    ("rerank :8003", "http://127.0.0.1:8003/health"),
    ("llm-guard :8016", "http://127.0.0.1:8016/health"),
    ("community-bridge :8095", "http://127.0.0.1:8095/v1/models"),
)

ALERT_QUEUES: tuple[str, ...] = ("rmi:alerts:system", "rmi:alerts:security")
