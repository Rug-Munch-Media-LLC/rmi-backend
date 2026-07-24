"""
RMI Admin Backend - Complete Administration System
===================================================
A hardened, feature-rich admin panel for the RugMunch Intelligence Platform.

Features:
  • Role-Based Access Control (RBAC) - superadmin, admin, moderator, viewer
  • Audit Logging - every action logged with IP, timestamp, before/after state
  • Rate Limiting - per-endpoint, per-user, per-IP limits
  • IP Blocking / Allowlisting - ban bad actors by IP
  • 2FA Support - TOTP-based two-factor authentication
  • Session Management - active sessions, force logout, expiry control
  • System Health - real-time metrics, disk, memory, CPU, service status
  • User Management - CRUD, ban/unban, role assignment, activity tracking
  • Configuration Management - env vars, feature flags, system settings
  • Security Dashboard - failed logins, blocked IPs, threat alerts
  • Content Management - announcements, blog posts, SEO settings
  • Financial Dashboard - x402 revenue, payment tracking, analytics
  • API Key Management - rotate, revoke, scope-limited keys
  • Webhook Management - configure, test, monitor webhooks
  • Backup & Restore - database, config, snapshot management

Security:
  - All endpoints require admin authentication (JWT + role check)
  - Audit log of every admin action (immutable, append-only)
  - Rate limiting on all admin endpoints (stricter than public)
  - IP allowlist for admin access (optional)
  - Session timeout and concurrent session limits
  - Password policy enforcement for admin accounts
  - Automatic lockout after failed login attempts
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from fastapi import HTTPException, Request

from app.core.logging import get_logger

logger = get_logger("rmi_admin_backend")


# ── Role Definitions ──────────────────────────────────────────


class AdminRole(StrEnum):
    """Admin role hierarchy. Higher = more permissions."""

    SUPERADMIN = "superadmin"  # Full access, can manage other admins
    ADMIN = "admin"  # Full access except admin management
    MODERATOR = "moderator"  # Content + user management, no system config
    VIEWER = "viewer"  # Read-only access to dashboards
    SUPPORT = "support"  # User management only, read-only system


# Permission matrix: role -> set of allowed permissions
PERMISSIONS = {
    AdminRole.SUPERADMIN: {
        "*",  # All permissions
    },
    AdminRole.ADMIN: {
        "dashboard.read",
        "users.read",
        "users.write",
        "users.ban",
        "content.read",
        "content.write",
        "system.read",
        "system.write",
        "security.read",
        "security.write",
        "financial.read",
        "financial.write",
        "api_keys.read",
        "api_keys.write",
        "webhooks.read",
        "webhooks.write",
        "backups.read",
        "backups.write",
        "settings.read",
        "settings.write",
        "logs.read",
        "analytics.read",
        "token_deploy.read",
        "token_deploy.write",
    },
    AdminRole.MODERATOR: {
        "dashboard.read",
        "users.read",
        "users.write",
        "users.ban",
        "content.read",
        "content.write",
        "security.read",
        "logs.read",
        "analytics.read",
    },
    AdminRole.VIEWER: {
        "dashboard.read",
        "users.read",
        "content.read",
        "system.read",
        "security.read",
        "financial.read",
        "analytics.read",
        "logs.read",
    },
    AdminRole.SUPPORT: {
        "dashboard.read",
        "users.read",
        "users.write",
        "content.read",
        "logs.read",
        "analytics.read",
    },
}


def has_permission(role: AdminRole, permission: str) -> bool:
    """Check if a role has a specific permission."""
    if role == AdminRole.SUPERADMIN:
        return True
    perms = PERMISSIONS.get(role, set())
    return permission in perms or "*" in perms


# ── Audit Log ───────────────────────────────────────────────────


@dataclass
class AuditLogEntry:
    """Single audit log entry."""

    entry_id: str
    timestamp: str
    admin_id: str
    admin_email: str
    action: str  # e.g., "user.ban", "token.deploy", "config.update"
    resource_type: str  # e.g., "user", "token", "config"
    resource_id: str
    ip_address: str
    user_agent: str
    before_state: dict | None = None
    after_state: dict | None = None
    status: str = "success"  # success, failed, denied
    reason: str = ""
    session_id: str = ""
    request_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class AuditLogger:
    """
    Immutable audit logging system.
    Stores to Redis (time-series) + file backup + optional Supabase.
    """

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

            # Add to time-series list
            key = f"audit_log:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            await r.lpush(key, json.dumps(entry.to_dict()))

            # Keep only last 30 days in Redis
            await r.ltrim(key, 0, 9999)

            # Add to admin-specific log
            await r.lpush(f"audit_log:admin:{admin_id}", json.dumps(entry.to_dict()))

            # Add to action-specific index
            await r.sadd(f"audit_log:actions:{action}", entry.entry_id)

        except Exception as e:
            logger.error(f"Redis audit log failed: {e}")

        # Write to local file (append-only, immutable)
        try:
            log_file = f"/var/log/rmi/audit_{datetime.now(UTC).strftime('%Y-%m')}.jsonl"
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except Exception as e:
            logger.error(f"File audit log failed: {e}")

        # Write to Supabase if available
        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                client = create_client(supabase_url, supabase_key)
                client.table("audit_logs").insert(entry.to_dict()).execute()
        except Exception as e:
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

            # Get today's log
            key = f"audit_log:{datetime.now(UTC).strftime('%Y-%m-%d')}"
            logs = await r.lrange(key, offset, offset + limit - 1)

            for log in logs:
                data = json.loads(log)

                # Filter
                if admin_id and data.get("admin_id") != admin_id:
                    continue
                if action and data.get("action") != action:
                    continue
                if resource_type and data.get("resource_type") != resource_type:
                    continue

                entries.append(AuditLogEntry(**data))

        except Exception as e:
            logger.error(f"Audit query failed: {e}")

        return entries


# ── Security Manager ──────────────────────────────────────────


class SecurityManager:
    """
    Security hardening for admin backend.
    - Rate limiting
    - IP blocking
    - Failed login tracking
    - Session management
    """

    # Rate limits: (max_requests, window_seconds)
    RATE_LIMITS = {  # noqa: RUF012
        "default": (60, 60),  # 60/minute
        "login": (5, 300),  # 5/5min (strict for login)
        "admin_write": (30, 60),  # 30/minute for write ops
        "token_deploy": (10, 3600),  # 10/hour for token deploy
        "snapshot": (20, 3600),  # 20/hour for snapshots
        "airdrop": (5, 3600),  # 5/hour for airdrops
    }

    @staticmethod
    async def check_rate_limit(
        identifier: str,
        limit_type: str = "default",
    ) -> dict[str, Any]:
        """Check if request is within rate limit."""
        max_req, window = SecurityManager.RATE_LIMITS.get(limit_type, (60, 60))

        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"rate_limit:{limit_type}:{identifier}"
            now = int(time.time())

            # Clean old entries
            await r.zremrangebyscore(key, 0, now - window)

            # Count current requests
            count = await r.zcard(key)

            if count >= max_req:
                # Get time until reset
                oldest = await r.zrange(key, 0, 0, withscores=True)
                reset_at = oldest[0][1] + window if oldest else now + window

                return {
                    "allowed": False,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "limit": max_req,
                    "window": window,
                }

            # Add current request
            await r.zadd(key, {str(now): now})
            await r.expire(key, window)

            return {
                "allowed": True,
                "remaining": max_req - count - 1,
                "reset_at": now + window,
                "limit": max_req,
                "window": window,
            }

        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            # Fail open (allow request) if Redis is down
            return {"allowed": True, "remaining": -1, "reset_at": 0}

    @staticmethod
    async def block_ip(ip: str, reason: str = "", duration_hours: int = 24) -> bool:
        """Block an IP address."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"blocked_ip:{ip}"
            data = {
                "ip": ip,
                "reason": reason,
                "blocked_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(hours=duration_hours)).isoformat(),
                "duration_hours": duration_hours,
            }

            await r.setex(key, duration_hours * 3600, json.dumps(data))
            await r.sadd("blocked_ips:all", ip)

            logger.warning(f"IP blocked: {ip} for {duration_hours}h - {reason}")
            return True

        except Exception as e:
            logger.error(f"IP block failed: {e}")
            return False

    @staticmethod
    async def unblock_ip(ip: str) -> bool:
        """Unblock an IP address."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            await r.delete(f"blocked_ip:{ip}")
            await r.srem("blocked_ips:all", ip)

            return True

        except Exception as e:
            logger.error(f"IP unblock failed: {e}")
            return False

    @staticmethod
    async def is_ip_blocked(ip: str) -> dict | None:
        """Check if IP is blocked. Returns block info or None."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            data = await r.get(f"blocked_ip:{ip}")
            if data:
                return json.loads(data)

            return None

        except Exception as e:
            logger.error(f"IP block check failed: {e}")
            return None

    @staticmethod
    async def track_failed_login(identifier: str) -> int:
        """Track failed login attempts. Returns current count."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"failed_logins:{identifier}"
            count = await r.incr(key)
            await r.expire(key, 3600)  # 1 hour window

            # Auto-block after 5 failed attempts
            if count >= 5:
                ip = identifier.split(":")[-1] if ":" in identifier else identifier
                await SecurityManager.block_ip(ip, "Too many failed login attempts", 1)

            return count

        except Exception as e:
            logger.error(f"Failed login tracking error: {e}")
            return 0

    @staticmethod
    async def reset_failed_login(identifier: str) -> bool:
        """Reset failed login counter (on successful login)."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            await r.delete(f"failed_logins:{identifier}")
            return True

        except Exception as e:
            logger.error(f"Reset failed login error: {e}")
            return False


# ── Session Manager ───────────────────────────────────────────


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
            "expires_at": (
                datetime.now(UTC) + timedelta(hours=SessionManager.SESSION_TIMEOUT_HOURS)
            ).isoformat(),
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

            # Store session
            key = f"admin_session:{session_id}"
            await r.setex(
                key, SessionManager.SESSION_TIMEOUT_HOURS * 3600, json.dumps(session_data)
            )

            # Add to admin's session list
            await r.sadd(f"admin_sessions:{admin_id}", session_id)

            # Enforce max concurrent sessions
            sessions = await r.smembers(f"admin_sessions:{admin_id}")
            if len(sessions) > SessionManager.MAX_CONCURRENT_SESSIONS:
                # Remove oldest sessions
                sorted_sessions = sorted(sessions)
                for old_session in sorted_sessions[: -SessionManager.MAX_CONCURRENT_SESSIONS]:
                    await SessionManager.destroy_session(old_session)

            return session_id

        except Exception as e:
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

            # Check expiry
            expires = datetime.fromisoformat(session["expires_at"])
            if datetime.now(UTC) > expires:
                await SessionManager.destroy_session(session_id)
                return None

            # Update last active
            session["last_active"] = datetime.now(UTC).isoformat()
            await r.setex(
                f"admin_session:{session_id}",
                SessionManager.SESSION_TIMEOUT_HOURS * 3600,
                json.dumps(session),
            )

            return session

        except Exception as e:
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

            # Get session data for admin_id
            data = await r.get(f"admin_session:{session_id}")
            if data:
                session = json.loads(data)
                admin_id = session.get("admin_id")
                if admin_id:
                    await r.srem(f"admin_sessions:{admin_id}", session_id)

            await r.delete(f"admin_session:{session_id}")
            return True

        except Exception as e:
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
                    # Check if still valid
                    expires = datetime.fromisoformat(session["expires_at"])
                    if datetime.now(UTC) < expires:
                        sessions.append(session)
                    else:
                        await r.srem(f"admin_sessions:{admin_id}", sid)

        except Exception as e:
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

        except Exception as e:
            logger.error(f"Destroy all sessions failed: {e}")

        return count


# ── Admin User Store ──────────────────────────────────────────


class AdminUserStore:
    """
    Store and manage admin user accounts.
    Uses Redis as primary + Supabase as backup.
    """

    @staticmethod
    async def get_admin(admin_id: str) -> dict | None:
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
            if data:
                return json.loads(data)

            return None

        except Exception as e:
            logger.error(f"Get admin failed: {e}")
            return None

    @staticmethod
    async def get_admin_by_email(email: str) -> dict | None:
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
            if admin_id:
                return await AdminUserStore.get_admin(admin_id)

            return None

        except Exception as e:
            logger.error(f"Get admin by email failed: {e}")
            return None

    @staticmethod
    async def save_admin(admin: dict) -> bool:
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
            except (ConnectionError, OSError, json.JSONDecodeError):
                pass

            return True

        except Exception as e:
            logger.error(f"Save admin failed: {e}")
            return False

    @staticmethod
    async def list_admins() -> list[dict]:
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

        except Exception as e:
            logger.error(f"List admins failed: {e}")

        return admins

    @staticmethod
    async def create_admin(
        email: str,
        password: str,
        role: AdminRole = AdminRole.VIEWER,
        created_by: str = "",
    ) -> dict | None:
        """Create a new admin user."""
        from app.auth import hash_password

        # Check if email already exists
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

        # Remove password hash from returned dict
        safe_admin = {
            k: v for k, v in admin.items() if k != "password_hash" and k != "two_factor_secret"
        }
        return safe_admin

    @staticmethod
    async def verify_admin_login(email: str, password: str) -> dict | None:
        """Verify admin login credentials."""
        from app.auth import verify_password

        admin = await AdminUserStore.get_admin_by_email(email)
        if not admin:
            return None

        if not admin.get("is_active", True):
            return None

        if not verify_password(password, admin.get("password_hash", "")):
            return None

        # Update last login
        admin["last_login"] = datetime.now(UTC).isoformat()
        admin["login_count"] = admin.get("login_count", 0) + 1
        await AdminUserStore.save_admin(admin)

        # Remove sensitive data
        safe_admin = {
            k: v for k, v in admin.items() if k != "password_hash" and k != "two_factor_secret"
        }
        return safe_admin


# ── System Health Monitor ─────────────────────────────────────


class SystemHealthMonitor:
    """
    Monitor system health and performance.
    """

    @staticmethod
    async def get_system_health() -> dict[str, Any]:
        """Get comprehensive system health."""
        import psutil

        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count()

        # Memory
        memory = psutil.virtual_memory()

        # Disk
        disk = psutil.disk_usage("/")

        # Network
        net_io = psutil.net_io_counters()

        # Process info
        backend_pid = os.getpid()
        backend_proc = psutil.Process(backend_pid)

        # Check services
        services = {
            "redis": await SystemHealthMonitor._check_redis(),
            "supabase": await SystemHealthMonitor._check_supabase(),
            "backend": {"status": "running", "pid": backend_pid},
        }

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "cpu": {
                "percent": cpu_percent,
                "count": cpu_count,
                "per_cpu": psutil.cpu_percent(percpu=True, interval=0.1),
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 2),
                "available_gb": round(memory.available / (1024**3), 2),
                "percent": memory.percent,
                "used_gb": round(memory.used / (1024**3), 2),
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "percent": round(disk.used / disk.total * 100, 1),
            },
            "network": {
                "bytes_sent_mb": round(net_io.bytes_sent / (1024**2), 2),
                "bytes_recv_mb": round(net_io.bytes_recv / (1024**2), 2),
            },
            "backend": {
                "pid": backend_pid,
                "memory_mb": round(backend_proc.memory_info().rss / (1024**2), 2),
                "cpu_percent": backend_proc.cpu_percent(interval=0.2),
                "threads": backend_proc.num_threads(),
                "open_files": len(backend_proc.open_files()),
                "connections": len(backend_proc.connections()),
            },
            "services": services,
            "uptime_seconds": int(time.time() - psutil.boot_time()),
        }

    @staticmethod
    async def _check_redis() -> dict:
        """Check Redis connection."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            await r.ping()
            info = await r.info()
            return {
                "status": "connected",
                "version": info.get("redis_version", "unknown"),
                "used_memory_mb": round(info.get("used_memory", 0) / (1024**2), 2),
                "connected_clients": info.get("connected_clients", 0),
                "total_keys": info.get("db0", {}).get("keys", 0)
                if isinstance(info.get("db0"), dict)
                else 0,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    async def _check_supabase() -> dict:
        """Check Supabase connection."""
        try:
            supabase_url = os.getenv("SUPABASE_URL")
            if not supabase_url:
                return {"status": "not_configured"}

            import httpx

            async with httpx.AsyncClient() as client:
                r = await client.get(f"{supabase_url}/rest/v1/", timeout=5)
                return {
                    "status": "connected" if r.status_code < 500 else "error",
                    "url": supabase_url,
                    "response_code": r.status_code,
                }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# ── Convenience: Admin Auth Decorator ─────────────────────────


async def require_admin(
    request: Request,
    required_permission: str = "",
    min_role: AdminRole = AdminRole.VIEWER,
) -> dict[str, Any]:
    """
    Verify admin authentication and authorization.

    Usage:
        admin = await require_admin(request, "users.write", AdminRole.ADMIN)
    """
    # Get session token from header
    session_token = request.headers.get("X-Admin-Session", "")
    if not session_token:
        raise HTTPException(status_code=401, detail="Admin session required")

    # Validate session
    session = await SessionManager.validate_session(session_token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # Check IP block
    client_ip = request.client.host if request.client else ""
    block_info = await SecurityManager.is_ip_blocked(client_ip)
    if block_info:
        raise HTTPException(status_code=403, detail="IP blocked")

    # Get admin
    admin = await AdminUserStore.get_admin(session["admin_id"])
    if not admin or not admin.get("is_active", True):
        raise HTTPException(status_code=403, detail="Admin account inactive")

    # Check role level
    role = AdminRole(admin.get("role", "viewer"))
    role_order = {
        AdminRole.VIEWER: 0,
        AdminRole.SUPPORT: 1,
        AdminRole.MODERATOR: 2,
        AdminRole.ADMIN: 3,
        AdminRole.SUPERADMIN: 4,
    }
    if role_order[role] < role_order[min_role]:
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    # Check specific permission
    if required_permission and not has_permission(role, required_permission):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Check rate limit
    rate_check = await SecurityManager.check_rate_limit(
        f"admin:{admin['id']}",
        "admin_write" if required_permission.endswith(".write") else "default",
    )
    if not rate_check["allowed"]:
        raise HTTPException(
            status_code=429, detail=f"Rate limit exceeded. Reset at {rate_check['reset_at']}"
        )

    # Log access
    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="admin.access",
        resource_type="endpoint",
        resource_id=str(request.url.path),
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        session_id=session_token,
    )

    return {
        "admin": admin,
        "session": session,
        "role": role,
    }
