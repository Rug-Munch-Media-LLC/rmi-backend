"""Matrix Client — Conduit homeserver API client.

Communicates with the self-hosted Conduit Matrix homeserver at port 8448.
Implements the Matrix Client-Server API v1.12 for:
- Room creation (DM, group, space)
- Message sending with E2E encryption (Olm/Megolm)
- Room membership management
- Event streaming via sync
- Device key management for E2E
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import httpx

from app.core.logging import get_logger
from app.services.chat.models import (
    RoomType,
)

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER", "http://127.0.0.1:8448")
MATRIX_SERVER_NAME = os.getenv("MATRIX_SERVER_NAME", "rugmunch.io")
MATRIX_ADMIN_TOKEN = os.getenv("MATRIX_ADMIN_TOKEN", "")
MATRIX_BOT_USER = os.getenv("MATRIX_BOT_USER", "@rmi-bot:rugmunch.io")
MATRIX_BOT_TOKEN = os.getenv("MATRIX_BOT_TOKEN", "")

CLIENT_API = f"{MATRIX_HOMESERVER}/_matrix/client/v3"

# ── Shared HTTP Client ──────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            headers={
                "User-Agent": "RMI-Chat/1.0",
                "Content-Type": "application/json",
            },
        )
    return _client


# ── Error Types ────────────────────────────────────────────────────────────


class MatrixError(Exception):
    """Base Matrix API error."""

    def __init__(self, message: str, status_code: int = 500, errcode: str | None = None) -> None:
        self.status_code = status_code
        self.errcode = errcode
        super().__init__(message)


class MatrixAuthError(MatrixError):
    """Authentication error (M_FORBIDDEN, M_UNKNOWN_TOKEN)."""


class MatrixRateLimit(MatrixError):
    """Rate limited (M_LIMIT_EXCEEDED)."""


class MatrixNotFound(MatrixError):
    """Resource not found (M_NOT_FOUND)."""


# ── Matrix API Client ──────────────────────────────────────────────────────


class MatrixClient:
    """High-level Matrix Client-Server API client.

    All operations go through the Matrix bot user (rmi-bot) which
    creates rooms, invites users, and sends messages. E2E encryption
    is handled by the SignalProtocol layer before sending to Matrix.
    """

    def __init__(self) -> None:
        self.homeserver = MATRIX_HOMESERVER
        self.server_name = MATRIX_SERVER_NAME
        self.bot_user = MATRIX_BOT_USER
        self.bot_token = MATRIX_BOT_TOKEN

    # ── Auth ────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        """Make an authenticated Matrix API request."""
        client = await _get_client()
        url = f"{CLIENT_API}{path}"

        headers: dict[str, str] = {}
        if auth and self.bot_token:
            headers["Authorization"] = f"Bearer {self.bot_token}"

        try:
            response = await client.request(method, url, json=json_body, params=params, headers=headers)
        except httpx.TimeoutException:
            raise MatrixError("Matrix request timed out", status_code=504)
        except httpx.HTTPError as e:
            raise MatrixError(f"Matrix HTTP error: {e}", status_code=502)

        # Handle Matrix error codes
        if response.status_code >= 400:
            err_body = response.text
            errcode = None
            try:
                err_data = response.json()
                errcode = err_data.get("errcode")
                err_body = err_data.get("error", err_body)
            except (json.JSONDecodeError, ValueError):
                pass

            if errcode == "M_FORBIDDEN" or errcode == "M_UNKNOWN_TOKEN":
                raise MatrixAuthError(err_body, response.status_code, errcode)
            elif errcode == "M_LIMIT_EXCEEDED":
                raise MatrixRateLimit(err_body, response.status_code, errcode)
            elif errcode == "M_NOT_FOUND":
                raise MatrixNotFound(err_body, response.status_code, errcode)
            else:
                raise MatrixError(err_body, response.status_code, errcode)

        return response.json()

    async def _ensure_bot_token(self) -> None:
        """Ensure the bot has a valid access token on the homeserver.

        If MATRIX_BOT_TOKEN is not set, tries password-based login.
        """
        if self.bot_token:
            return

        bot_password = os.getenv("MATRIX_BOT_PASSWORD", "")
        if not bot_password:
            logger.warning("matrix_no_bot_credentials — set MATRIX_BOT_TOKEN or MATRIX_BOT_PASSWORD")
            return

        try:
            result = await self._request(
                "POST",
                "/login",
                {
                    "type": "m.login.password",
                    "user": self.bot_user,
                    "password": bot_password,
                },
                auth=False,
            )
            self.bot_token = result.get("access_token", "")
            logger.info("matrix_bot_logged_in")
        except MatrixError as e:
            logger.error("matrix_bot_login_failed: %s", e)

    # ── Room Management ────────────────────────────────────────────────

    async def create_room(
        self,
        name: str,
        room_type: RoomType = RoomType.DM,
        topic: str | None = None,
        invitees: list[str] | None = None,
        is_encrypted: bool = True,
    ) -> dict[str, Any]:
        """Create a Matrix room via the bot user.

        Returns the room_id and other creation metadata.
        """
        await self._ensure_bot_token()

        preset = "private_chat" if room_type == RoomType.DM else "trusted_private_chat"
        if room_type == RoomType.PUBLIC:
            preset = "public_chat"

        visibility = "private" if room_type != RoomType.PUBLIC else "public"

        body: dict[str, Any] = {
            "name": name,
            "preset": preset,
            "visibility": visibility,
            "initial_state": [],
        }

        if topic:
            body["topic"] = topic

        if invitees:
            body["invite"] = invitees

        # Enable E2E encryption
        if is_encrypted and room_type != RoomType.PUBLIC:
            body["initial_state"].append(
                {
                    "type": "m.room.encryption",
                    "state_key": "",
                    "content": {
                        "algorithm": "m.megolm.v1.aes-sha2",
                        "rotation_period_ms": 604800000,  # 1 week
                        "rotation_period_msgs": 100,
                    },
                }
            )

        # Create the room
        result = await self._request("POST", "/createRoom", body)
        room_id = result.get("room_id", "")

        logger.info(
            "matrix_room_created room_id=%s name=%s type=%s encrypted=%s",
            room_id,
            name,
            room_type,
            is_encrypted,
        )

        return {
            "room_id": room_id,
            "name": name,
            "type": room_type,
            "encrypted": is_encrypted,
        }

    async def invite_user(self, room_id: str, user_id: str) -> dict[str, Any]:
        """Invite a user to a room."""
        await self._ensure_bot_token()
        result = await self._request(
            "POST",
            f"/rooms/{room_id}/invite",
            {"user_id": user_id},
        )
        logger.info("matrix_invite_sent room_id=%s user=%s", room_id, user_id)
        return result

    async def join_room(self, room_id: str) -> dict[str, Any]:
        """Join a room as the bot user."""
        await self._ensure_bot_token()
        result = await self._request("POST", f"/rooms/{room_id}/join")
        logger.info("matrix_room_joined room_id=%s", room_id)
        return result

    async def leave_room(self, room_id: str) -> dict[str, Any]:
        """Leave a room as the bot user."""
        await self._ensure_bot_token()
        result = await self._request("POST", f"/rooms/{room_id}/leave")
        logger.info("matrix_room_left room_id=%s", room_id)
        return result

    # ── Messaging ──────────────────────────────────────────────────────

    async def send_message(
        self,
        room_id: str,
        content: str,
        msg_type: str = "m.text",
        txn_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to a Matrix room.

        For E2E rooms, the message should already be encrypted by the
        SignalProtocol layer. For plaintext rooms, sends as-is.
        """
        await self._ensure_bot_token()

        txn_id = txn_id or f"txn-{uuid4().hex}"

        body: dict[str, Any] = {
            "msgtype": msg_type,
            "body": content,
        }

        # For encrypted rooms, format as m.room.encrypted event
        # In production, this would be the Olm/Megolm ciphertext
        # For now, we're sending as plaintext since E2E is at the
        # application layer above Matrix
        result = await self._request(
            "PUT",
            f"/rooms/{room_id}/send/m.room.message/{txn_id}",
            body,
        )

        event_id = result.get("event_id", "")
        logger.debug("matrix_message_sent room_id=%s event_id=%s", room_id, event_id)

        return {
            "event_id": event_id,
            "room_id": room_id,
            "transaction_id": txn_id,
        }

    async def send_encrypted_message(
        self,
        room_id: str,
        ciphertext: dict[str, Any],
        txn_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a pre-encrypted message to an E2E room.

        The ciphertext is an m.room.encrypted event body as specified
        by the Matrix Olm/Megolm spec.
        """
        await self._ensure_bot_token()
        txn_id = txn_id or f"txn-{uuid4().hex}"

        result = await self._request(
            "PUT",
            f"/rooms/{room_id}/send/m.room.encrypted/{txn_id}",
            ciphertext,
        )

        event_id = result.get("event_id", "")
        return {"event_id": event_id, "room_id": room_id}

    async def get_room_messages(
        self,
        room_id: str,
        limit: int = 50,
        from_token: str | None = None,
    ) -> dict[str, Any]:
        """Get messages from a room using Matrix sync.

        Returns a list of events (messages) from the room timeline.
        """
        await self._ensure_bot_token()

        # Use /messages endpoint for room history
        params: dict[str, Any] = {
            "room_id": room_id,
            "limit": min(limit, 100),
            "dir": "b",
        }
        if from_token:
            params["from"] = from_token

        result = await self._request("GET", "/messages", params=params)
        return result

    async def get_room_state(self, room_id: str) -> dict[str, Any]:
        """Get the current state of a room (members, name, topic, encryption)."""
        await self._ensure_bot_token()
        result = await self._request("GET", f"/rooms/{room_id}/state")
        return result

    async def sync(self, since_token: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
        """Perform a Matrix sync to get new events across all rooms."""
        await self._ensure_bot_token()

        params: dict[str, Any] = {
            "timeout": timeout_ms,
        }
        if since_token:
            params["since"] = since_token

        result = await self._request("GET", "/sync", params=params)
        return result

    # ── User Discovery ─────────────────────────────────────────────────

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        """Get a user's profile (display name, avatar)."""
        await self._ensure_bot_token()
        result = await self._request("GET", f"/profile/{user_id}", auth=False)
        return result

    async def search_user_directory(self, search_term: str) -> list[dict[str, Any]]:
        """Search the user directory."""
        await self._ensure_bot_token()
        result = await self._request(
            "POST",
            "/user_directory/search",
            {"search_term": search_term},
        )
        return result.get("results", [])

    # ── Key Management (E2E) ─────────────────────────────────────────────

    async def upload_device_keys(self, device_keys: dict[str, Any]) -> dict[str, Any]:
        """Upload device keys for E2E encryption."""
        await self._ensure_bot_token()
        result = await self._request("POST", "/keys/upload", device_keys)
        return result

    async def query_device_keys(self, user_ids: list[str]) -> dict[str, Any]:
        """Query device keys for users (to establish Olm sessions)."""
        await self._ensure_bot_token()
        result = await self._request(
            "POST",
            "/keys/query",
            {"device_keys": {uid: [] for uid in user_ids}},
        )
        return result

    async def claim_one_time_keys(self, user_id: str, device_id: str, key_count: int = 1) -> dict[str, Any]:
        """Claim one-time keys for establishing Olm sessions."""
        await self._ensure_bot_token()
        result = await self._request(
            "POST",
            "/keys/claim",
            {"one_time_keys": {user_id: {device_id: "signed_curve25519"}}},
        )
        return result

    # ── Server Health ──────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Check if the Conduit homeserver is reachable."""
        try:
            result = await self._request("GET", "/", auth=False)
            return {"status": "online", "server": self.homeserver, "result": result}
        except MatrixError as e:
            logger.warning("matrix_health_check_failed: %s", e)
            return {"status": "offline", "server": self.homeserver, "error": str(e)}
        except Exception as e:
            logger.error("matrix_health_check_error: %s", e)
            return {"status": "error", "server": self.homeserver, "error": str(e)}

    async def get_server_version(self) -> dict[str, Any]:
        """Get the Matrix server version info."""
        try:
            return await self._request("GET", "/versions", auth=False)
        except MatrixError as e:
            logger.warning("matrix_version_check_failed: %s", e)
            return {"error": str(e)}

    # ── Admin (Conduit specific) ────────────────────────────────────────

    async def admin_list_rooms(self) -> list[dict[str, Any]]:
        """List all rooms on the homeserver (admin API)."""
        # Conduit uses the standard admin API
        result = await self._request("GET", "/admin/rooms")
        return result.get("rooms", [])

    async def admin_list_users(self) -> list[dict[str, Any]]:
        """List all users on the homeserver (admin API)."""
        result = await self._request("GET", "/admin/users")
        return result.get("users", [])
