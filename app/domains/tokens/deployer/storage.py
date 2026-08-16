"""Deployment storage (Redis + Supabase)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.tokens.deployer.models import TokenDeployment

logger = get_logger("darkroom_token_deployer.storage")


class DeploymentStorage:
    """Store and retrieve deployment records. Uses Redis as primary, Supabase as backup."""

    def __init__(self) -> None:
        self.redis: Any = None
        self.supabase: Any = None
        self._init_stores()

    def _init_stores(self) -> None:
        try:
            import redis.asyncio as redis_lib

            self.redis = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
        except (ConnectionError, OSError, ValueError) as e:
            logger.debug("_init_stores_error", exc_info=True)
            logger.warning(f"Redis not available for deployment storage: {e}")

        try:
            from supabase import create_client  # PTB None-able fields

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                self.supabase = create_client(supabase_url, supabase_key)
        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.debug("_init_stores_error", exc_info=True)
            logger.warning(f"Supabase not available for deployment storage: {e}")

    async def save(self, deployment: TokenDeployment) -> bool:
        """Save deployment record."""
        data = deployment.to_dict()
        key = f"token_deployment:{deployment.deployment_id}"
        success = False
        if self.redis:
            try:
                await self.redis.set(key, json.dumps(data))
                await self.redis.sadd("token_deployments:all", deployment.deployment_id)
                await self.redis.sadd(f"token_deployments:chain:{deployment.chain}", deployment.deployment_id)
                success = True
            except (json.JSONDecodeError, ConnectionError, OSError, ValueError) as e:
                logger.debug("save_error", exc_info=True)
                logger.error(f"Redis save failed: {e}")
        if self.supabase:
            try:
                self.supabase.table("token_deployments").upsert(data).execute()
                success = True
            except (ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("save_error", exc_info=True)
                logger.error(f"Supabase save failed: {e}")
        return success

    async def get(self, deployment_id: str) -> TokenDeployment | None:
        if self.redis:
            try:
                data = await self.redis.get(f"token_deployment:{deployment_id}")
                if data:
                    return TokenDeployment(**json.loads(data))
            except (json.JSONDecodeError, ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
                logger.debug("get_error", exc_info=True)
                logger.error(f"Redis get failed: {e}")
        if self.supabase:
            try:
                result = (
                    self.supabase.table("token_deployments").select("*").eq("deployment_id", deployment_id).execute()
                )
                if result.data:
                    return TokenDeployment(**result.data[0])
            except (ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
                logger.debug("get_error", exc_info=True)
                logger.error(f"Supabase get failed: {e}")
        return None

    async def list_all(self, chain: str | None = None, limit: int = 100) -> list[TokenDeployment]:
        deployments = []
        if self.redis:
            try:
                if chain:
                    ids = await self.redis.smembers(f"token_deployments:chain:{chain}")
                else:
                    ids = await self.redis.smembers("token_deployments:all")
                for dep_id in list(ids)[:limit]:
                    dep = await self.get(dep_id)
                    if dep:
                        deployments.append(dep)
            except (ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
                logger.debug("list_all_error", exc_info=True)
                logger.error(f"Redis list failed: {e}")
        if not deployments and self.supabase:
            try:
                query = self.supabase.table("token_deployments").select("*").limit(limit)
                if chain:
                    query = query.eq("chain", chain)
                result = query.execute()
                for row in result.data:
                    deployments.append(TokenDeployment(**row))
            except (ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("list_all_error", exc_info=True)
                logger.error(f"Supabase list failed: {e}")
        return deployments

    async def update_status(self, deployment_id: str, status: str, extra: dict[str, Any] | None = None) -> bool:
        dep = await self.get(deployment_id)
        if not dep:
            return False
        dep.status = status
        dep.updated_at = datetime.now(UTC).isoformat()
        if extra:
            dep.extra.update(extra)
        return await self.save(dep)
