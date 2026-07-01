"""Application configuration.

Pydantic-settings reads from environment variables (and .env in dev).
Import: `from app.config import settings`
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    jwt_secret: str = Field(default="dev-secret-CHANGE-ME")
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


# Module-level singleton for convenience.
settings = get_settings()
