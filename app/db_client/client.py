"""
RMI Database Client - Supabase Client
======================================
Wrapped Supabase client with connection pooling, retries, and helpers.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

from .cache import _async_retry

logger = get_logger(__name__)

# ── Lazy imports to avoid import-time failures ──
try:
    from supabase import (
        Client,
        create_client,
    )
except ImportError:
    create_client = None  # type: ignore[assignment]
    Client = None  # type: ignore[assignment, misc]

# ── SQL to ensure missing tables exist ──
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


class SupabaseClient:
    """Wrapped Supabase client with connection pooling, retries, and helpers."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self.url = url or os.getenv("SUPABASE_URL", "")
        # Fall back through env var variants
        from app.core.secrets import get_secret_or_none

        self.key = (
            key
            or get_secret_or_none("SUPABASE_SERVICE_KEY", "")
            or get_secret_or_none("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
            or os.getenv("SUPABASE_ANON_KEY", "")
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
        return self.client.rpc("exec_sql", {"query": sql}).execute()

    @_async_retry(max_retries=2)
    async def ensure_tables(self) -> None:
        """Best-effort table creation. Requires exec_sql RPC or run SQL manually."""
        try:
            await self.execute_sql(ENSURE_TABLES_SQL)
        except Exception as exc:
            logger.warning(f"[DB] ensure_tables failed (tables may already exist or RPC missing): {exc}")
