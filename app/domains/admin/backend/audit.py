"""
Admin Backend — Audit Logger
=============================
Immutable audit logging system. Stores to Redis (time-series) + file backup + optional Supabase.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domains.admin.backend.models import AuditLogEntry

logger = get_logger("rmi_admin_backend")


class AuditLogger:
    """Immutable audit logging system."""

    @staticmethod
    async def log(
        admin_id: str,
        admin_email: str,
        action: str,
        resource_type: str,
        resource_id: str,
        ip_address: str = "",
        user_agent: str = "",
        before_state: dict | None = None,
        after_state: dict | None = None,
        status: str = "success",
        reason: str = "",
        session_id: str = "",
        request_id: str = "",
    ) -> bool:
        """Log an admin action."""
        entry = AuditLogEntry(
            entry_id=f"audit_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
            timestamp=datetime.now(UTC).isoformat(),
            admin_id=admin_id,
            admin_email=admin_email,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            before_state=before_state,
            after_state=after_state,
            status=status,
            reason=reason,
            session_id=session_id,
            request_id=request_id,
        )

        # Write to Redis (time-series list)
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"audit_log:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            await r.lpush(key, json.dumps(entry.to_dict()))
            await r.ltrim(key, 0, 9999)
            await r.lpush(f"audit_log:admin:{admin_id}", json.dumps(entry.to_dict()))
            await r.sadd(f"audit_log:actions:{action}", entry.entry_id)

        except (json.JSONDecodeError, ValueError, TypeError, ConnectionError, OSError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Redis audit log failed: {e}")

        # Write to local file (append-only, immutable)
        try:
            log_file = f"/var/log/rmi/audit_{datetime.now(UTC).strftime('%Y-%m')}.jsonl"
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except (json.JSONDecodeError, ValueError, TypeError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"File audit log failed: {e}")

        # Write to Supabase if available
        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                client = create_client(supabase_url, supabase_key)
                client.table("audit_logs").insert(entry.to_dict()).execute()
        except (ConnectionError, TimeoutError, ValueError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Supabase audit log failed: {e}")

        return True

    @staticmethod
    async def query(
        admin_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """Query audit logs."""
        entries = []
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"audit_log:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            logs = await r.lrange(key, offset, offset + limit - 1)

            for log in logs:
                data = json.loads(log)
                if admin_id and data.get("admin_id") != admin_id:
                    continue
                if action and data.get("action") != action:
                    continue
                if resource_type and data.get("resource_type") != resource_type:
                    continue
                entries.append(AuditLogEntry(**data))

        except (json.JSONDecodeError, ValueError, TypeError, ConnectionError, OSError, KeyError) as e :
            logger.debug("query_error", exc_info=True)
            logger.error(f"Audit query failed: {e}")

        return entries
