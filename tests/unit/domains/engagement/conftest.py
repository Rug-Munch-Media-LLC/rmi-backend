"""Fixtures for the article engagement tests.

Real in-memory store (same contract as the SQL one), identity and ban seams
overridden through ``app.dependency_overrides`` — no monkeypatching anywhere.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.domains.engagement.deps import get_ban_policy, get_optional_actor, get_store
from app.domains.engagement.permissions import Actor
from app.domains.engagement.router import router
from app.error_handlers import register_error_handlers
from tests.unit.domains.engagement.helpers import InMemoryEngagementStore


class RecordingBanPolicy:
    """Ban seam reporting a fixed set of banned user ids."""

    def __init__(self, banned: set[str] | None = None) -> None:
        self.banned = banned or set()
        self.calls: list[str] = []

    async def is_banned(self, user_id: str) -> bool:
        self.calls.append(user_id)
        return user_id in self.banned


@pytest.fixture
def store() -> InMemoryEngagementStore:
    return InMemoryEngagementStore()


@pytest.fixture
def ban_policy() -> RecordingBanPolicy:
    return RecordingBanPolicy()


@pytest.fixture
def client(store: InMemoryEngagementStore, ban_policy: RecordingBanPolicy) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)

    async def _override_actor(request: Request) -> Actor | None:
        """Identity seam: headers stand in for validated JWT claims."""
        user_id = request.headers.get("x-test-user", "")
        if not user_id:
            return None
        return Actor(user_id=user_id, role=request.headers.get("x-test-role", "USER"))

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_ban_policy] = lambda: ban_policy
    app.dependency_overrides[get_optional_actor] = _override_actor
    test_client = TestClient(app)
    # The rate limiter is a process-wide singleton keyed on the client identity,
    # so each test gets its own bucket (an API key) instead of inheriting the
    # previous test's spend. Production still limits per real identity.
    test_client.headers.update({"X-API-Key": f"test-{uuid.uuid4()}"})
    return test_client


def as_user(client: TestClient, user_id: str, role: str = "USER") -> TestClient:
    """Authenticate subsequent requests as ``user_id``."""
    client.headers.update({"X-Test-User": user_id, "X-Test-Role": role})
    return client


def anon(client: TestClient) -> TestClient:
    client.headers.pop("X-Test-User", None)
    client.headers.pop("X-Test-Role", None)
    return client


def post(client: TestClient, url: str, payload: dict[str, Any] | None = None) -> Any:
    return client.post(url, json=payload if payload is not None else {})
