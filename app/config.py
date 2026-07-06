"""Application configuration.

Pydantic-settings reads from environment variables (and .env in dev).
Import: `from app.config import settings`
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Add new env vars here as needed."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Runtime ──────────────────────────────────────────────────────
    environment: Literal["dev", "staging", "prod"] = "prod"
    log_level: str = "INFO"
    port: int = 8000

    # ── Database / Cache ────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://rmi:rmi@localhost/rmi"
    redis_url: str = "redis://localhost:6379/0"

    # ── Auth ────────────────────────────────────────────────────────
    jwt_secret: str = Field(  # required, no default, fail-fast in prod
        min_length=32,
        description=(
            "HMAC secret for JWT. Required. Must be >= 32 chars. "
            "Generate with: openssl rand -hex 32. "
            "Set via gopass (rmi/prod/jwt_secret) or JWT_SECRET env var."
        ),
    )
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: list[str] = ["*"]

    # ── Rate limiting ───────────────────────────────────────────────
    rate_limit_per_minute: int = 100

    # ── AI providers ────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    openrouter_api_key: str = ""
    huggingface_token: str = ""

    # ── Langfuse v4 (observability) ─────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3002"

    # ── External APIs ───────────────────────────────────────────────
    coingecko_api_key: str = ""
    etherscan_api_key: str = ""
    birdeye_api_key: str = ""
    goplus_api_key: str = ""

    # ── RMI-specific ────────────────────────────────────────────────
    rag_collections: list[str] = [
        "scam_intel",
        "deployer_history",
        "wallet_labels",
        "contract_audit",
        "phishing_db",
    ]

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_must_be_set_in_prod(cls, v: str) -> str:
        """Reject the dev default in production.

        Pydantic-settings binds v from env (or rejects via min_length above).
        Here we only enforce the dev placeholder cannot leak into a prod boot.
        """
        if v == "dev-secret-CHANGE-ME" and os.getenv("ENVIRONMENT", "dev") == "prod":
            raise ValueError(
                "jwt_secret must be set to a non-default value in production. "
                "Set via gopass (rmi/prod/jwt_secret) or JWT_SECRET env var."
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


# Module-level singleton for convenience.
settings = get_settings()
