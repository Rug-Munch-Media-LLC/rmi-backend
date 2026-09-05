"""
Admin Backend — Admin User Store
==================================
Store and manage admin user accounts. Uses Redis as primary + Supabase as backup.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, cast

from app.core.logging import get_logger
from app.domains.admin.backend.models import AdminRole

logger = get_logger("rmi_admin_backend")


class AdminUserStore:
    """Store and manage admin user accounts."""

    @staticmethod
    async def get_admin(admin_id: str) -> dict[str, Any] | None:
        """Get admin by ID."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            data = await r.hget("rmi:admins", admin_id)
            return json.loads(data) if data else None
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError) as e:
            logger.debug("get_admin_error", exc_info=True)
            logger.error(f"Get admin failed: {e}")
            return None

    @staticmethod
    async def get_admin_by_email(email: str) -> dict[str, Any] | None:
        """Get admin by email."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            admin_id = await r.hget("rmi:admins:email", email.lower())
            return await AdminUserStore.get_admin(cast("str", admin_id)) if admin_id else None
        except (ConnectionError, OSError, ValueError) as e:
            logger.debug("get_admin_by_email_error", exc_info=True)
            logger.error(f"Get admin by email failed: {e}")
            return None

    @staticmethod
    async def save_admin(admin: dict[str, Any]) -> bool:
        """Save admin user."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            await r.hset("rmi:admins", admin["id"], json.dumps(admin))
            await r.hset("rmi:admins:email", admin["email"].lower(), admin["id"])

            # Also save to Supabase
            try:
                from supabase import create_client

                supabase_url = os.getenv("SUPABASE_URL")
                supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
                if supabase_url and supabase_key:
                    client = create_client(supabase_url, supabase_key)
                    client.table("admin_users").upsert(admin).execute()
            except (ConnectionError, OSError, json.JSONDecodeError) as e:
                logger.warning(f"save_admin failed: {e}")

            return True
        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.debug("save_admin_error", exc_info=True)
            logger.error(f"Save admin failed: {e}")
            return False

    @staticmethod
    async def list_admins() -> list[dict[str, Any]]:
        """List all admin users."""
        admins = []
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            all_admins = await r.hgetall("rmi:admins")
            for data in all_admins.values():
                admins.append(json.loads(data))
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError) as e:
            logger.debug("list_admins_error", exc_info=True)
            logger.error(f"List admins failed: {e}")
        return admins

    @staticmethod
    async def create_admin(
        email: str,
        password: str,
        role: AdminRole = AdminRole.VIEWER,
        created_by: str = "",
    ) -> dict[str, Any] | None:
        """Create a new admin user."""
        from app.auth import hash_password

        existing = await AdminUserStore.get_admin_by_email(email)
        if existing:
            return None

        admin_id = hashlib.sha256(email.lower().encode()).hexdigest()[:16]
        admin = {
            "id": admin_id,
            "email": email,
            "password_hash": hash_password(password),
            "role": role.value,
            "is_active": True,
            "created_at": datetime.now(UTC).isoformat(),
            "created_by": created_by,
            "last_login": None,
            "login_count": 0,
            "two_factor_enabled": False,
            "two_factor_secret": None,
            "ip_allowlist": [],
            "metadata": {},
        }
        await AdminUserStore.save_admin(admin)
        return {k: v for k, v in admin.items() if k not in ("password_hash", "two_factor_secret")}

    @staticmethod
    async def verify_admin_login(email: str, password: str) -> dict[str, Any] | None:
        """Verify admin login credentials."""
        from app.auth import verify_password

        admin = await AdminUserStore.get_admin_by_email(email)
        if not admin or not admin.get("is_active", True):
            return None
        if not verify_password(password, admin.get("password_hash", "")):
            return None

        admin["last_login"] = datetime.now(UTC).isoformat()
        admin["login_count"] = admin.get("login_count", 0) + 1
        await AdminUserStore.save_admin(admin)

        return {k: v for k, v in admin.items() if k not in ("password_hash", "two_factor_secret")}
