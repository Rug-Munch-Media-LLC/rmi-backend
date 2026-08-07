"""
RMI Supabase Database Client
============================
Persistent PostgreSQL storage via Supabase with Redis caching.
Write-through: Supabase first, then Redis.
Read-through: Redis first, Supabase on miss.
"""

from __future__ import annotations

from .cache import (
    _async_retry,
    cache_delete,
    cache_get,
    cache_hash_get_all,
    cache_hash_set,
    cache_set,
    sync_redis_to_supabase,
)
from .client import ENSURE_TABLES_SQL, SupabaseClient
from .models import (
    Alert,
    ContractAudit,
    GamificationProfile,
    InvestigationCase,
    TrenchesPost,
    User,
    WalletScan,
)
from .repositories import (
    AlertRepository,
    ContractAuditRepository,
    GamificationProfileRepository,
    InvestigationCaseRepository,
    TrenchesPostRepository,
    UserRepository,
    WalletScanRepository,
)

# ═══════════════════════════════════════════════════════════
# UNIFIED DB INTERFACE
# ═══════════════════════════════════════════════════════════


class RmiDatabase:
    """Unified interface: Supabase persistence + Redis cache."""

    def __init__(self, supabase_url: str | None = None, supabase_key: str | None = None) -> None:
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
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════

_rmi_db: RmiDatabase | None = None


def get_db() -> RmiDatabase:
    global _rmi_db
    if _rmi_db is None:
        _rmi_db = RmiDatabase()
    return _rmi_db


__all__ = [
    "ENSURE_TABLES_SQL",
    "Alert",
    "AlertRepository",
    "ContractAudit",
    "ContractAuditRepository",
    "GamificationProfile",
    "GamificationProfileRepository",
    "InvestigationCase",
    "InvestigationCaseRepository",
    # Unified interface
    "RmiDatabase",
    # Client
    "SupabaseClient",
    "TrenchesPost",
    "TrenchesPostRepository",
    # Models
    "User",
    # Repositories
    "UserRepository",
    "WalletScan",
    "WalletScanRepository",
    # Cache
    "_async_retry",
    "cache_delete",
    "cache_get",
    "cache_hash_get_all",
    "cache_hash_set",
    "cache_set",
    "get_db",
    "sync_redis_to_supabase",
]
