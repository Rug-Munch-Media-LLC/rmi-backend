#!/usr/bin/env python3
"""
RMI Supabase Database Client
============================
Persistent PostgreSQL storage via Supabase with Redis caching.
Write-through: Supabase first, then Redis.
Read-through: Redis first, Supabase on miss.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from dotenv import load_dotenv

# Load .env with override to ensure JWT keys win over stale Docker env vars
load_dotenv("/app/.env", override=True)

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Lazy imports to avoid import-time failures ──
try:
    from supabase import Client, create_client
except ImportError as _imp_err:
    create_client = None
    Client = None
    logger.warning(f"[DB] supabase client not installed: {_imp_err}")

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

# ── SQL to ensure missing tables exist ──
# Run these in the Supabase SQL Editor if the tables are missing.
ENSURE_TABLES_SQL = """
-- Users / Profiles (extends auth.users for wallet-auth users)
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    email TEXT,
    wallet_address TEXT UNIQUE,
    role TEXT DEFAULT 'USER',
    tier TEXT DEFAULT 'FREE',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    display_name TEXT,
    scans_remaining INTEGER DEFAULT 5,
    total_scans_used INTEGER DEFAULT 0,
    reputation_score INTEGER DEFAULT 0
);
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Profiles public read" ON profiles FOR SELECT USING (true);
CREATE POLICY "Profiles admin write" ON profiles FOR ALL USING (
    EXISTS (SELECT 1 FROM auth.users WHERE auth.users.id = auth.uid() AND auth.users.role = 'admin')
);

-- Contract Audits
CREATE TABLE IF NOT EXISTS contract_audits (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    address TEXT NOT NULL,
    chain TEXT DEFAULT 'solana',
    risk_score NUMERIC DEFAULT 0,
    findings JSONB DEFAULT '[]',
    ai_analysis JSONB DEFAULT '{}',
    audited_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE contract_audits ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Audits public read" ON contract_audits FOR SELECT USING (true);
CREATE POLICY "Audits admin write" ON contract_audits FOR ALL USING (
    EXISTS (SELECT 1 FROM auth.users WHERE auth.users.id = auth.uid() AND auth.users.role = 'admin')
);

-- Trenches Posts (community board)
CREATE TABLE IF NOT EXISTS trenches_posts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'discussion',
    author_id TEXT,
    upvotes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE trenches_posts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Posts public read" ON trenches_posts FOR SELECT USING (true);
CREATE POLICY "Posts owner write" ON trenches_posts FOR ALL USING (
    auth.uid()::text = author_id OR
    EXISTS (SELECT 1 FROM auth.users WHERE auth.users.id = auth.uid() AND auth.users.role = 'admin')
);

-- Gamification Profiles
CREATE TABLE IF NOT EXISTS gamification_profiles (
    user_id TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    badges TEXT[] DEFAULT '{}',
    scans_count INTEGER DEFAULT 0,
    posts_count INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE gamification_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Gamification public read" ON gamification_profiles FOR SELECT USING (true);
CREATE POLICY "Gamification owner write" ON gamification_profiles FOR ALL USING (
    auth.uid()::text = user_id OR
    EXISTS (SELECT 1 FROM auth.users WHERE auth.users.id = auth.uid() AND auth.users.role = 'admin')
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_profiles_wallet ON profiles(wallet_address);
CREATE INDEX IF NOT EXISTS idx_contract_audits_address ON contract_audits(address);
CREATE INDEX IF NOT EXISTS idx_trenches_posts_category ON trenches_posts(category);
CREATE INDEX IF NOT EXISTS idx_trenches_posts_created ON trenches_posts(created_at DESC);
"""


# ═══════════════════════════════════════════════════════════
# RETRY UTILITIES
# ═══════════════════════════════════════════════════════════


def _async_retry(max_retries: int = 3, delay: float = 0.5, backoff: float = 2.0):
    """Simple async retry decorator for Supabase operations."""

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            wait = delay
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt >= max_retries:
                        raise
                    logger.warning(f"[DB] Retry {attempt + 1}/{max_retries} for {func.__name__}: {exc}")
                    await asyncio.sleep(wait)
                    wait *= backoff
            raise last_exc

        return wrapper

    return decorator


# ═══════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════


class User(BaseModel):
    id: str
    email: str | None = None
    wallet_address: str | None = None
    role: str = "USER"
    tier: str = "FREE"
    created_at: str | None = None
    xp: int = 0
    level: int = 1


class InvestigationCase(BaseModel):
    id: str
    target: str
    type: str = "wallet"
    status: str = "open"
    evidence: list[str] = Field(default_factory=list)
    agents_assigned: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    risk_score: float = 0.0
    findings: dict[str, Any] = Field(default_factory=dict)


class WalletScan(BaseModel):
    id: str | None = None
    address: str
    chain: str = "solana"
    risk_score: float = 0.0
    findings: dict[str, Any] = Field(default_factory=dict)
    scanned_at: str | None = None
    user_id: str | None = None


class ContractAudit(BaseModel):
    id: str | None = None
    address: str
    chain: str = "solana"
    risk_score: float = 0.0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    ai_analysis: dict[str, Any] = Field(default_factory=dict)
    audited_at: str | None = None


class TrenchesPost(BaseModel):
    id: str | None = None
    title: str
    content: str
    category: str = "discussion"
    author_id: str | None = None
    upvotes: int = 0
    comments: int = 0
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None


class Alert(BaseModel):
    id: str | None = None
    token_address: str
    types: list[str] = Field(default_factory=list)
    webhook_url: str | None = None
    active: bool = True
    created_at: str | None = None


class GamificationProfile(BaseModel):
    user_id: str
    xp: int = 0
    level: int = 1
    badges: list[str] = Field(default_factory=list)
    scans_count: int = 0
    posts_count: int = 0
    updated_at: str | None = None


# ═══════════════════════════════════════════════════════════
# SUPABASE CLIENT
# ═══════════════════════════════════════════════════════════


class SupabaseClient:
    """Wrapped Supabase client with connection pooling, retries, and helpers."""

    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = url or os.getenv("SUPABASE_URL", "")
        # Fall back through env var variants
        self.key = (
            key or os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
        )
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            if create_client is None:
                raise RuntimeError("supabase package is not installed")
            if not self.url or not self.key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
            self._client = create_client(self.url, self.key)
        return self._client

    @property
    def client(self) -> Any:
        return self._ensure_client()

    # ── Auth helpers ──

    @_async_retry(max_retries=3)
    async def sign_up(self, email: str, password: str) -> dict[str, Any]:
        """Register a new user via Supabase Auth."""
        result = self.client.auth.sign_up({"email": email, "password": password})
        return result.model_dump() if hasattr(result, "model_dump") else dict(result)

    @_async_retry(max_retries=3)
    async def sign_in(self, email: str, password: str) -> dict[str, Any]:
        """Sign in via Supabase Auth."""
        result = self.client.auth.sign_in_with_password({"email": email, "password": password})
        return result.model_dump() if hasattr(result, "model_dump") else dict(result)

    @_async_retry(max_retries=3)
    async def get_user(self, token: str) -> dict[str, Any] | None:
        """Get user by JWT token."""
        result = self.client.auth.get_user(token)
        if result and result.user:
            return {
                "id": result.user.id,
                "email": result.user.email,
            }
        return None

    # ── Table access ──

    def table(self, name: str) -> Any:
        return self.client.table(name)

    # ── SQL execution (requires service role or elevated privileges) ──

    @_async_retry(max_retries=2)
    async def execute_sql(self, sql: str) -> Any:
        """Execute raw SQL via RPC (requires a stored procedure or service role)."""
        # Prefer using migrations; this is a fallback.
        return self.client.rpc("exec_sql", {"query": sql}).execute()

    @_async_retry(max_retries=2)
    async def ensure_tables(self) -> None:
        """Best-effort table creation. Requires exec_sql RPC or run SQL manually."""
        try:
            await self.execute_sql(ENSURE_TABLES_SQL)
        except Exception as exc:
            logger.warning(f"[DB] ensure_tables failed (tables may already exist or RPC missing): {exc}")


# ═══════════════════════════════════════════════════════════
# REPOSITORIES
# ═══════════════════════════════════════════════════════════


class _BaseRepo:
    def __init__(self, db: SupabaseClient):
        self.db = db

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _to_dict(self, model: BaseModel, exclude_none: bool = True) -> dict[str, Any]:
        d = model.model_dump()
        if exclude_none:
            d = {k: v for k, v in d.items() if v is not None}
        return d


class UserRepository(_BaseRepo):
    TABLE = "profiles"

    @_async_retry(max_retries=3)
    async def create(self, user: User) -> dict[str, Any]:
        data = self._to_dict(user)
        if not data.get("created_at"):
            data["created_at"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, user_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("id", user_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def get_by_wallet(self, wallet_address: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("wallet_address", wallet_address.lower()).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        result = self.db.table(self.TABLE).select("*").limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        result = self.db.table(self.TABLE).update(updates).eq("id", user_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def upsert(self, user: User) -> dict[str, Any]:
        data = self._to_dict(user)
        if not data.get("created_at"):
            data["created_at"] = self._now()
        result = self.db.table(self.TABLE).upsert(data, on_conflict="id").execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, user_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("id", user_id).execute()
        return True


class InvestigationCaseRepository(_BaseRepo):
    TABLE = "cases"

    @_async_retry(max_retries=3)
    async def create(self, case: InvestigationCase) -> dict[str, Any]:
        data = self._to_dict(case)
        if not data.get("created_at"):
            data["created_at"] = self._now()
        if not data.get("updated_at"):
            data["updated_at"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, case_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("id", case_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if status:
            query = query.eq("status", status)
        result = query.limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, case_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        updates["updated_at"] = self._now()
        result = self.db.table(self.TABLE).update(updates).eq("id", case_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, case_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("id", case_id).execute()
        return True


class WalletScanRepository(_BaseRepo):
    TABLE = "wallet_intel"

    @_async_retry(max_retries=3)
    async def create(self, scan: WalletScan) -> dict[str, Any]:
        data = self._to_dict(scan)
        # Map findings to transactions JSONB for schema compatibility
        if "findings" in data:
            data["transactions"] = data.pop("findings")
        if not data.get("scanned_at"):
            data["last_seen"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, scan_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("id", scan_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def get_by_address(self, address: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("address", address).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 100, chain: str | None = None) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if chain:
            query = query.eq("chain", chain)
        result = query.limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, scan_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        if "findings" in updates:
            updates["transactions"] = updates.pop("findings")
        updates["updated_at"] = self._now()
        result = self.db.table(self.TABLE).update(updates).eq("id", scan_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, scan_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("id", scan_id).execute()
        return True


class ContractAuditRepository(_BaseRepo):
    TABLE = "contract_audits"

    @_async_retry(max_retries=3)
    async def create(self, audit: ContractAudit) -> dict[str, Any]:
        data = self._to_dict(audit)
        if not data.get("audited_at"):
            data["audited_at"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, audit_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("id", audit_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 100, chain: str | None = None) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if chain:
            query = query.eq("chain", chain)
        result = query.limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, audit_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        result = self.db.table(self.TABLE).update(updates).eq("id", audit_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, audit_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("id", audit_id).execute()
        return True


class TrenchesPostRepository(_BaseRepo):
    TABLE = "trenches_posts"

    @_async_retry(max_retries=3)
    async def create(self, post: TrenchesPost) -> dict[str, Any]:
        data = self._to_dict(post)
        if not data.get("created_at"):
            data["created_at"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, post_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("id", post_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 50, category: str | None = None) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if category and category != "all":
            query = query.eq("category", category)
        result = query.order("created_at", desc=True).limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, post_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        result = self.db.table(self.TABLE).update(updates).eq("id", post_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, post_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("id", post_id).execute()
        return True


class AlertRepository(_BaseRepo):
    TABLE = "alerts"

    @_async_retry(max_retries=3)
    async def create(self, alert: Alert) -> dict[str, Any]:
        data = self._to_dict(alert)
        # Map types -> alert_types for schema compatibility
        if "types" in data:
            data["alert_types"] = data.pop("types")
        if not data.get("created_at"):
            data["created_at"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, alert_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("id", alert_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 100, active_only: bool = True) -> list[dict[str, Any]]:
        query = self.db.table(self.TABLE).select("*")
        if active_only:
            query = query.eq("active", True)
        result = query.limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, alert_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        if "types" in updates:
            updates["alert_types"] = updates.pop("types")
        result = self.db.table(self.TABLE).update(updates).eq("id", alert_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, alert_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("id", alert_id).execute()
        return True


class GamificationProfileRepository(_BaseRepo):
    TABLE = "gamification_profiles"

    @_async_retry(max_retries=3)
    async def create(self, profile: GamificationProfile) -> dict[str, Any]:
        data = self._to_dict(profile)
        if not data.get("updated_at"):
            data["updated_at"] = self._now()
        result = self.db.table(self.TABLE).insert(data).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def get(self, user_id: str) -> dict[str, Any] | None:
        result = self.db.table(self.TABLE).select("*").eq("user_id", user_id).execute()
        return result.data[0] if result.data else None

    @_async_retry(max_retries=3)
    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        result = self.db.table(self.TABLE).select("*").limit(limit).execute()
        return result.data or []

    @_async_retry(max_retries=3)
    async def update(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        updates["updated_at"] = self._now()
        result = self.db.table(self.TABLE).update(updates).eq("user_id", user_id).execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def upsert(self, profile: GamificationProfile) -> dict[str, Any]:
        data = self._to_dict(profile)
        if not data.get("updated_at"):
            data["updated_at"] = self._now()
        result = self.db.table(self.TABLE).upsert(data, on_conflict="user_id").execute()
        return result.data[0] if result.data else {}

    @_async_retry(max_retries=3)
    async def delete(self, user_id: str) -> bool:
        self.db.table(self.TABLE).delete().eq("user_id", user_id).execute()
        return True


# ═══════════════════════════════════════════════════════════
# UNIFIED DB INTERFACE
# ═══════════════════════════════════════════════════════════


class RmiDatabase:
    """Unified interface: Supabase persistence + Redis cache."""

    def __init__(self, supabase_url: str | None = None, supabase_key: str | None = None):
        self.db = SupabaseClient(url=supabase_url, key=supabase_key)
        self.users = UserRepository(self.db)
        self.cases = InvestigationCaseRepository(self.db)
        self.wallet_scans = WalletScanRepository(self.db)
        self.contract_audits = ContractAuditRepository(self.db)
        self.trenches_posts = TrenchesPostRepository(self.db)
        self.alerts = AlertRepository(self.db)
        self.gamification = GamificationProfileRepository(self.db)

    async def ensure_tables(self) -> None:
        await self.db.ensure_tables()


# ═══════════════════════════════════════════════════════════
# REDIS CACHE HELPERS
# ═══════════════════════════════════════════════════════════

_CACHE_TTL = 300  # 5 minutes default


async def _get_redis() -> Any | None:
    """Lazy Redis client (mirrors main.py pattern)."""
    if redis is None:
        return None
    try:
        import os

        host = os.getenv("REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD") or None
        db = int(os.getenv("REDIS_DB", "0"))
        return redis.Redis(host=host, port=port, password=password, db=db, decode_responses=True)
    except Exception as exc:
        logger.warning(f"[DB] Redis init failed: {exc}")
        return None


async def cache_set(key: str, value: Any, ttl: int = _CACHE_TTL) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(value), ex=ttl)
    except Exception as exc:
        logger.warning(f"[DB] cache_set failed: {exc}")


async def cache_get(key: str) -> Any | None:
    r = await _get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning(f"[DB] cache_get failed: {exc}")
        return None


async def cache_delete(key: str) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.delete(key)
    except Exception as exc:
        logger.warning(f"[DB] cache_delete failed: {exc}")


async def cache_hash_set(hash_name: str, field: str, value: Any) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.hset(hash_name, field, json.dumps(value))
    except Exception as exc:
        logger.warning(f"[DB] cache_hash_set failed: {exc}")


async def cache_hash_get_all(hash_name: str) -> dict[str, Any]:
    r = await _get_redis()
    if r is None:
        return {}
    try:
        raw = await r.hgetall(hash_name)
        return {k: json.loads(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning(f"[DB] cache_hash_get_all failed: {exc}")
        return {}


# ═══════════════════════════════════════════════════════════
# MIGRATION HELPER
# ═══════════════════════════════════════════════════════════


async def sync_redis_to_supabase() -> dict[str, int]:
    """
    Read all data from Redis hashes and upsert into Supabase tables.
    Returns counts of synced records per table.
    """
    counts = {"cases": 0, "trenches_posts": 0, "alerts": 0, "tasks": 0}
    r = await _get_redis()
    if r is None:
        logger.warning("[DB] Redis unavailable; nothing to migrate")
        return counts

    db = RmiDatabase()

    # ── Cases ──
    try:
        cases_raw = await r.hgetall("rmi:cases")
        for case_json in cases_raw.values():
            try:
                data = json.loads(case_json)
                case = InvestigationCase(
                    id=data.get("id", ""),
                    target=data.get("target", ""),
                    type=data.get("type", "wallet"),
                    status=data.get("status", "open"),
                    evidence=data.get("evidence") or data.get("evidence", []),
                    agents_assigned=data.get("agents_assigned") or data.get("agents_assigned", []),
                    created_at=data.get("created_at") or data.get("created"),
                    updated_at=data.get("updated_at") or data.get("updated") or data.get("created"),
                    risk_score=float(data.get("risk_score", 0)),
                    findings=data.get("findings") or {},
                )
                await db.cases.create(case)
                counts["cases"] += 1
            except Exception as exc:
                logger.warning(f"[DB] Failed to migrate case: {exc}")
    except Exception as exc:
        logger.warning(f"[DB] Cases migration error: {exc}")

    # ── Trenches Posts ──
    try:
        posts_raw = await r.hgetall("rmi:trenches:posts")
        for post_json in posts_raw.values():
            try:
                data = json.loads(post_json)
                post = TrenchesPost(
                    id=data.get("id"),
                    title=data.get("title", ""),
                    content=data.get("content", ""),
                    category=data.get("category", "discussion"),
                    author_id=data.get("author_id"),
                    upvotes=int(data.get("upvotes", 0)),
                    comments=int(data.get("comments", 0)),
                    tags=data.get("tags") or [],
                    created_at=data.get("created_at") or data.get("created"),
                )
                await db.trenches_posts.create(post)
                counts["trenches_posts"] += 1
            except Exception as exc:
                logger.warning(f"[DB] Failed to migrate post: {exc}")
    except Exception as exc:
        logger.warning(f"[DB] Posts migration error: {exc}")

    # ── Alerts ──
    try:
        alerts_raw = await r.hgetall("rmi:alerts")
        for alert_json in alerts_raw.values():
            try:
                data = json.loads(alert_json)
                alert = Alert(
                    id=data.get("id"),
                    token_address=data.get("token") or data.get("token_address", ""),
                    types=data.get("types") or data.get("alert_types") or [],
                    webhook_url=data.get("webhook") or data.get("webhook_url"),
                    active=data.get("active", True),
                    created_at=data.get("created_at") or data.get("created"),
                )
                await db.alerts.create(alert)
                counts["alerts"] += 1
            except Exception as exc:
                logger.warning(f"[DB] Failed to migrate alert: {exc}")
    except Exception as exc:
        logger.warning(f"[DB] Alerts migration error: {exc}")

    # ── Tasks (migrate as cases with type=task for traceability) ──
    try:
        tasks_raw = await r.hgetall("rmi:tasks")
        for task_json in tasks_raw.values():
            try:
                data = json.loads(task_json)
                case = InvestigationCase(
                    id=data.get("id", ""),
                    target=data.get("command", "task"),
                    type="task",
                    status=data.get("status", "open"),
                    evidence=[],
                    agents_assigned=[data.get("agent", "")] if data.get("agent") else [],
                    created_at=data.get("created") or data.get("created_at"),
                    updated_at=data.get("created") or data.get("created_at"),
                    risk_score=0.0,
                    findings={
                        "context": data.get("context", {}),
                        "priority": data.get("priority", "normal"),
                    },
                )
                await db.cases.create(case)
                counts["tasks"] += 1
            except Exception as exc:
                logger.warning(f"[DB] Failed to migrate task: {exc}")
    except Exception as exc:
        logger.warning(f"[DB] Tasks migration error: {exc}")

    logger.info(f"[DB] Migration complete: {counts}")
    return counts


# ═══════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════

_rmi_db: RmiDatabase | None = None


def get_db() -> RmiDatabase:
    global _rmi_db
    if _rmi_db is None:
        _rmi_db = RmiDatabase()
    return _rmi_db
