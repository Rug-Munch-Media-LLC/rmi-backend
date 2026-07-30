"""
Admin Backend — Session Manager
=================================
Admin session management: create, validate, destroy, list active sessions.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.domains.admin.backend.models import AdminRole

logger = get_logger("rmi_admin_backend")


class SessionManager:
    """
    Admin session management.
    - Track active sessions
    - Force logout
    - Concurrent session limits
    - Session expiry
    """

    MAX_CONCURRENT_SESSIONS = 3
    SESSION_TIMEOUT_HOURS = 8

    @staticmethod
    async def create_session(
        admin_id: str,
        admin_email: str,
        role: AdminRole,
        ip_address: str,
        user_agent: str,
    ) -> str:
        """Create a new admin session."""
        session_id = f"sess_{secrets.token_urlsafe(16)}"
        session_data = {
            "session_id": session_id,
            "admin_id": admin_id,
            "admin_email": admin_email,
            "role": role.value if isinstance(role, AdminRole) else role,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=SessionManager.SESSION_TIMEOUT_HOURS)).isoformat(),
            "last_active": datetime.now(UTC).isoformat(),
            "is_active": True,
        }

        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"admin_session:{session_id}"
            await r.setex(key, SessionManager.SESSION_TIMEOUT_HOURS * 3600, json.dumps(session_data))
            await r.sadd(f"admin_sessions:{admin_id}", session_id)

            # Enforce max concurrent sessions
            sessions = await r.smembers(f"admin_sessions:{admin_id}")
            if len(sessions) > SessionManager.MAX_CONCURRENT_SESSIONS:
                sorted_sessions = sorted(sessions)
                for old_session in sorted_sessions[: -SessionManager.MAX_CONCURRENT_SESSIONS]:
                    await SessionManager.destroy_session(old_session)

            return session_id
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Session creation failed: {e}")
            return ""

    @staticmethod
    async def validate_session(session_id: str) -> dict | None:
        """Validate and refresh a session."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            data = await r.get(f"admin_session:{session_id}")
            if not data:
                return None
            session = json.loads(data)
            expires = datetime.fromisoformat(session["expires_at"])
            if datetime.now(UTC) > expires:
                await SessionManager.destroy_session(session_id)
                return None
            session["last_active"] = datetime.now(UTC).isoformat()
            await r.setex(
                f"admin_session:{session_id}", SessionManager.SESSION_TIMEOUT_HOURS * 3600, json.dumps(session)
            )
            return session
        except (json.JSONDecodeError, ValueError, TypeError, ConnectionError, OSError, KeyError) as e:
            logger.debug("validate_session_error", exc_info=True)
            logger.error(f"Session validation failed: {e}")
            return None

    @staticmethod
    async def destroy_session(session_id: str) -> bool:
        """Destroy a session (logout)."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            data = await r.get(f"admin_session:{session_id}")
            if data:
                session = json.loads(data)
                admin_id = session.get("admin_id")
                if admin_id:
                    await r.srem(f"admin_sessions:{admin_id}", session_id)
            await r.delete(f"admin_session:{session_id}")
            return True
        except (
            ConnectionError,
            OSError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
            ValueError,
            KeyError,
            TypeError,
            TimeoutError,
        ) as e:
            logger.debug("destroy_session_error", exc_info=True)
            logger.error(f"Session destroy failed: {e}")
            return False

    @staticmethod
    async def get_active_sessions(admin_id: str) -> list[dict]:
        """Get all active sessions for an admin."""
        sessions = []
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            session_ids = await r.smembers(f"admin_sessions:{admin_id}")
            for sid in session_ids:
                data = await r.get(f"admin_session:{sid}")
                if data:
                    session = json.loads(data)
                    expires = datetime.fromisoformat(session["expires_at"])
                    if datetime.now(UTC) < expires:
                        sessions.append(session)
                    else:
                        await r.srem(f"admin_sessions:{admin_id}", sid)
        except (json.JSONDecodeError, ValueError, TypeError, ConnectionError, OSError, KeyError) as e:
            logger.debug("get_active_sessions_error", exc_info=True)
            logger.error(f"Get active sessions failed: {e}")
        return sessions

    @staticmethod
    async def destroy_all_sessions(admin_id: str) -> int:
        """Destroy all sessions for an admin (force logout everywhere)."""
        count = 0
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            session_ids = await r.smembers(f"admin_sessions:{admin_id}")
            for sid in session_ids:
                await r.delete(f"admin_session:{sid}")
                count += 1
            await r.delete(f"admin_sessions:{admin_id}")
        except (ConnectionError, OSError, ValueError) as e:
            logger.debug("destroy_all_sessions_error", exc_info=True)
            logger.error(f"Destroy all sessions failed: {e}")
        return count
