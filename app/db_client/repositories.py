"""
RMI Database Client - Repositories
===================================
Data access layer with CRUD operations for each entity type.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .cache import _async_retry

if TYPE_CHECKING:
    from pydantic import BaseModel

    from .client import SupabaseClient
    from .models import (
        Alert,
        ContractAudit,
        GamificationProfile,
        InvestigationCase,
        TrenchesPost,
        User,
        WalletScan,
    )


class _BaseRepo:
    def __init__(self, db: SupabaseClient) -> None:
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
