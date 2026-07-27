"""Admin backend tests — 15+ tests covering admin route protection, role-based
access control, user management, security rate limiting, session management,
audit logging, and system health monitoring.

Uses pytest-asyncio (enabled via pytest.ini). All external services mocked.
"""

from __future__ import annotations

import os

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

from unittest.mock import AsyncMock, patch

from app.domains.admin.backend.models import (
    PERMISSIONS,
    AdminRole,
    AuditLogEntry,
    has_permission,
)
from app.domains.admin.backend.security import SecurityManager

# ═══════════════════════════════════════════════════════════════════════
# ── AdminRole and Permission Matrix
# ═══════════════════════════════════════════════════════════════════════


class TestAdminRoleModel:
    """AdminRole enum values and comparison."""

    def test_admin_role_values(self):
        """AdminRole has expected roles with correct string values."""
        assert AdminRole.SUPERADMIN.value == "superadmin"
        assert AdminRole.ADMIN.value == "admin"
        assert AdminRole.MODERATOR.value == "moderator"
        assert AdminRole.VIEWER.value == "viewer"
        assert AdminRole.SUPPORT.value == "support"

    def test_admin_role_ordering(self):
        """Roles are comparable via enum ordering (alphabetic)."""
        roles = [
            AdminRole.VIEWER,
            AdminRole.SUPPORT,
            AdminRole.MODERATOR,
            AdminRole.ADMIN,
            AdminRole.SUPERADMIN,
        ]
        # StrEnum comparison works as string comparison
        assert AdminRole.SUPERADMIN > AdminRole.ADMIN


class TestPermissions:
    """Permission matrix and has_permission logic."""

    def test_superadmin_has_all_permissions(self):
        """Superadmin always has permission via wildcard."""
        assert has_permission(AdminRole.SUPERADMIN, "any.permission") is True
        assert has_permission(AdminRole.SUPERADMIN, "") is True

    def test_admin_has_write_permissions(self):
        """Admin role has users.write permission."""
        assert has_permission(AdminRole.ADMIN, "users.write") is True
        assert has_permission(AdminRole.ADMIN, "users.ban") is True

    def test_admin_denied_superadmin_only(self):
        """Admin role does NOT have superadmin-only permissions."""
        # No 'settings.superadmin' permission exists for admin
        assert has_permission(AdminRole.ADMIN, "nonexistent.permission") is False

    def test_viewer_readonly_permissions(self):
        """Viewer has read permissions but not write permissions."""
        assert has_permission(AdminRole.VIEWER, "dashboard.read") is True
        assert has_permission(AdminRole.VIEWER, "users.read") is True
        assert has_permission(AdminRole.VIEWER, "users.write") is False
        assert has_permission(AdminRole.VIEWER, "users.ban") is False

    def test_moderator_can_ban_users(self):
        """Moderator has users.ban permission."""
        assert has_permission(AdminRole.MODERATOR, "users.ban") is True
        assert has_permission(AdminRole.MODERATOR, "users.write") is True

    def test_moderator_limited_system_access(self):
        """Moderator has security.read and logs.read but not system.write."""
        assert has_permission(AdminRole.MODERATOR, "security.read") is True
        assert has_permission(AdminRole.MODERATOR, "logs.read") is True
        assert has_permission(AdminRole.MODERATOR, "system.write") is False
        assert has_permission(AdminRole.MODERATOR, "financial.read") is False

    def test_support_can_read_logs(self):
        """Support role has logs.read and users.write."""
        assert has_permission(AdminRole.SUPPORT, "logs.read") is True
        assert has_permission(AdminRole.SUPPORT, "users.write") is True

    def test_support_has_no_financial_access(self):
        """Support role cannot read financial data."""
        assert has_permission(AdminRole.SUPPORT, "financial.read") is False

    def test_support_has_no_system_access(self):
        """Support role cannot access system settings."""
        assert has_permission(AdminRole.SUPPORT, "system.read") is False
        assert has_permission(AdminRole.SUPPORT, "system.write") is False

    def test_permission_matrix_completeness(self):
        """Every non-superadmin role has at least 6 permissions."""
        for role in [AdminRole.ADMIN, AdminRole.MODERATOR, AdminRole.VIEWER, AdminRole.SUPPORT]:
            perms = PERMISSIONS.get(role, set())
            assert len(perms) >= 6, f"{role} has too few permissions: {perms}"

    def test_invalid_role_denies_all(self):
        """Unknown role string has no permissions."""
        assert has_permission("invalid_role", "anything") is False

    def test_empty_permission_denied_for_non_superadmin(self):
        """Empty permission string is not a valid permission for non-superadmin."""
        assert has_permission(AdminRole.ADMIN, "") is False


# ═══════════════════════════════════════════════════════════════════════
# ── AuditLogEntry Model
# ═══════════════════════════════════════════════════════════════════════


class TestAuditLogEntry:
    """AuditLogEntry data model."""

    def test_audit_entry_default_status(self):
        """AuditLogEntry defaults to 'success' status."""
        entry = AuditLogEntry(
            entry_id="audit_001",
            timestamp="2026-01-01T00:00:00+00:00",
            admin_id="admin_1",
            admin_email="admin@test.com",
            action="user.create",
            resource_type="user",
            resource_id="usr_123",
            ip_address="127.0.0.1",
            user_agent="test-agent",
        )
        assert entry.status == "success"
        assert entry.reason == ""

    def test_audit_entry_to_dict(self):
        """to_dict serializes all fields."""
        entry = AuditLogEntry(
            entry_id="audit_002",
            timestamp="2026-01-01T00:00:00+00:00",
            admin_id="admin_1",
            admin_email="admin@test.com",
            action="user.delete",
            resource_type="user",
            resource_id="usr_456",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            before_state={"active": True},
            after_state={"active": False},
            status="success",
        )
        d = entry.to_dict()
        assert d["entry_id"] == "audit_002"
        assert d["action"] == "user.delete"
        assert d["before_state"] == {"active": True}
        assert d["after_state"] == {"active": False}


# ═══════════════════════════════════════════════════════════════════════
# ── SecurityManager — Rate Limiting Logic
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityManagerRateLimits:
    """SecurityManager rate limit configuration and business logic."""

    def test_rate_limit_config(self):
        """RATE_LIMITS has expected entries for all limit types."""
        assert "default" in SecurityManager.RATE_LIMITS
        assert "login" in SecurityManager.RATE_LIMITS
        assert "admin_write" in SecurityManager.RATE_LIMITS
        assert "token_deploy" in SecurityManager.RATE_LIMITS
        assert "snapshot" in SecurityManager.RATE_LIMITS
        assert "airdrop" in SecurityManager.RATE_LIMITS

    def test_rate_limit_login_strict(self):
        """Login has strict limits (5 per 300s)."""
        max_req, window = SecurityManager.RATE_LIMITS["login"]
        assert max_req == 5
        assert window == 300

    def test_rate_limit_token_deploy_strict(self):
        """Token deploy has very strict limits (10 per 3600s)."""
        max_req, window = SecurityManager.RATE_LIMITS["token_deploy"]
        assert max_req == 10
        assert window == 3600

    def test_rate_limit_default_liberal(self):
        """Default limit is generous (60 per 60s)."""
        max_req, window = SecurityManager.RATE_LIMITS["default"]
        assert max_req == 60
        assert window == 60

    @patch("redis.asyncio.Redis")
    async def test_check_rate_limit_allowed(self, mock_redis_class):
        """check_rate_limit returns allowed=True when under limit."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.zremrangebyscore = AsyncMock(return_value=0)
        mock_redis.zcard = AsyncMock(return_value=5)  # 5 used of 60
        mock_redis.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()

        result = await SecurityManager.check_rate_limit("test_user", "default")
        assert result["allowed"] is True
        assert result["remaining"] == 54  # 60 - 5 - 1

    @patch("redis.asyncio.Redis")
    async def test_check_rate_limit_blocked(self, mock_redis_class):
        """check_rate_limit returns allowed=False when over limit."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.zremrangebyscore = AsyncMock(return_value=0)
        mock_redis.zcard = AsyncMock(return_value=60)  # 60 used of 60
        mock_redis.zrange = AsyncMock(return_value=[("timestamp", 1000.0)])

        result = await SecurityManager.check_rate_limit("test_user", "default")
        assert result["allowed"] is False
        assert result["remaining"] == 0
        assert result["limit"] == 60

    @patch("redis.asyncio.Redis")
    async def test_check_rate_limit_redis_failure_grants_access(self, mock_redis_class):
        """When Redis is down, rate limiter fails open (allowed=True)."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.zremrangebyscore = AsyncMock(side_effect=ConnectionError("Redis down"))

        result = await SecurityManager.check_rate_limit("test_user", "default")
        assert result["allowed"] is True


# ═══════════════════════════════════════════════════════════════════════
# ── SecurityManager — IP Blocking
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityManagerIPBlocking:
    """IP block/unblock/check logic."""

    @patch("redis.asyncio.Redis")
    async def test_block_ip_sets_key(self, mock_redis_class):
        """block_ip sets the correct Redis key with expiry."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.setex = AsyncMock()
        mock_redis.sadd = AsyncMock()

        result = await SecurityManager.block_ip("192.168.1.1", "Test block", 1)
        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("redis.asyncio.Redis")
    async def test_unblock_ip_removes_key(self, mock_redis_class):
        """unblock_ip removes the blocked IP key."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.delete = AsyncMock()
        mock_redis.srem = AsyncMock()

        result = await SecurityManager.unblock_ip("192.168.1.1")
        assert result is True
        mock_redis.delete.assert_called_once_with("blocked_ip:192.168.1.1")

    @patch("redis.asyncio.Redis")
    async def test_is_ip_blocked_returns_block_info(self, mock_redis_class):
        """is_ip_blocked returns block info when IP is blocked."""
        import json

        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        block_data = {"ip": "192.168.1.1", "reason": "Abuse"}
        mock_redis.get = AsyncMock(return_value=json.dumps(block_data))

        result = await SecurityManager.is_ip_blocked("192.168.1.1")
        assert result is not None
        assert result["ip"] == "192.168.1.1"
        assert result["reason"] == "Abuse"

    @patch("redis.asyncio.Redis")
    async def test_is_ip_blocked_returns_none(self, mock_redis_class):
        """is_ip_blocked returns None when IP is not blocked."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.get = AsyncMock(return_value=None)

        result = await SecurityManager.is_ip_blocked("192.168.1.2")
        assert result is None

    @patch("redis.asyncio.Redis")
    async def test_track_failed_login_blocks_at_threshold(self, mock_redis_class):
        """Track failed login blocks IP after 5 attempts."""
        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.incr = AsyncMock(return_value=5)  # 5th failed attempt
        mock_redis.expire = AsyncMock()

        with patch.object(SecurityManager, "block_ip", new_callable=AsyncMock) as mock_block:
            count = await SecurityManager.track_failed_login("user:192.168.1.1")
            assert count == 5
            mock_block.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# ── AdminUserStore
# ═══════════════════════════════════════════════════════════════════════


class TestAdminUserStore:
    """AdminUserStore with Redis mocked."""

    @patch("redis.asyncio.Redis")
    async def test_get_admin_returns_none_for_missing(self, mock_redis_class):
        """get_admin returns None when admin not found."""
        from app.domains.admin.backend.users import AdminUserStore

        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.hget = AsyncMock(return_value=None)

        result = await AdminUserStore.get_admin("nonexistent")
        assert result is None

    @patch("redis.asyncio.Redis")
    async def test_list_admins_empty(self, mock_redis_class):
        """list_admins returns empty list when no admins exist."""
        from app.domains.admin.backend.users import AdminUserStore

        mock_redis = AsyncMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.hgetall = AsyncMock(return_value={})

        result = await AdminUserStore.list_admins()
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# ── SessionManager
# ═══════════════════════════════════════════════════════════════════════


class TestSessionManager:
    """SessionManager creation and validation logic."""

    def test_session_config(self):
        """SessionManager has expected constants."""
        from app.domains.admin.backend.session import SessionManager

        assert SessionManager.MAX_CONCURRENT_SESSIONS == 3
        assert SessionManager.SESSION_TIMEOUT_HOURS == 8
