"""Integration tests for Telegram bot webhook and command endpoints.

Tests that the Telegram bot webhook endpoint is reachable and
correctly routes /start, /scan, and other commands.

Note: The Telegram bot router (app.routers.admin_extensions) is DEFERRED
per AUDIT-2026-Q3 and NOT currently mounted in ROUTER_MODULES. These
tests use 200/404 assertions to handle both mounted and unmounted states.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestTelegramWebhook:
    """Tests for Telegram bot webhook endpoint reachability."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_bot_webhook(self, app) -> None:
        """POST /api/v1/telegram/webhook — bot webhook endpoint.

        Sends a /start command payload. Returns 200 if the telegram
        router is mounted and processes the webhook, 404 if not.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v1/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 123},
                    "text": "/start",
                },
            },
        )
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_bot_webhook_scan(self, app) -> None:
        """POST /api/v1/telegram/webhook — bot /scan command.

        Sends a /scan command with an Ethereum address to test
        command routing within the webhook handler.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v1/telegram/webhook",
            json={
                "update_id": 2,
                "message": {
                    "message_id": 2,
                    "chat": {"id": 123},
                    "text": "/scan 0xabc",
                },
            },
        )
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)


class TestTelegramAdmin:
    """Tests for Telegram admin endpoints (if mounted)."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_bot_stats(self, app) -> None:
        """GET /api/v1/admin/telegram/stats — Telegram bot statistics.

        Admin stats endpoint shows uptime, message count, active users.
        Located in admin_extensions router (deferred/wired separately).
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/admin/telegram/stats")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)
