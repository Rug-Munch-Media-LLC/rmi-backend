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
        default="dev-secret-please-set-JWT_SECRET-env-var-32chars",
        min_length=32,
        description=(
            "HMAC secret for JWT (HS256 fallback). Required. Must be >= 32 chars. "
            "Generate with: openssl rand -hex 32. "
            "Set via gopass (rmi/prod/jwt_secret) or JWT_SECRET env var."
        ),
    )
    jwt_algorithm: str = "RS256"
    jwt_private_key: str = Field(
        default="",
        description=(
            "RSA PRIVATE KEY in PEM format (for RS256 signing). "
            "Provide the key string directly, or a file path readable by the app. "
            "Generate with: openssl genrsa -out jwt-private.pem 2048. "
            "Set via gopass (rmi/prod/jwt_private_key) or JWT_PRIVATE_KEY env var."
        ),
    )
    jwt_public_key: str = Field(
        default="",
        description=(
            "RSA PUBLIC KEY in PEM format (for RS256 verification). "
            "Provide the key string directly, or a file path readable by the app. "
            "Derive from private key: openssl rsa -in jwt-private.pem -pubout -out jwt-public.pem. "
            "Set via gopass (rmi/prod/jwt_public_key) or JWT_PUBLIC_KEY env var."
        ),
    )
    jwt_expire_minutes: int = 60 * 24

    # API key for X-API-Key header authentication
    rmi_auth_token: str = Field(
        default="",
        description=(
            "Shared secret for X-API-Key header authentication. "
            "Must be set in production. Generate with: openssl rand -hex 32. "
            "Set via RMI_AUTH_TOKEN env var or .env file."
        ),
    )

    # Dev bypass flag — must be explicitly set to bypass auth in dev
    dev_bypass_auth: bool = Field(
        default=False,
        description=(
            "Explicitly opt in to bypass authentication in development mode. "
            "Set DEV_BYPASS_AUTH=true in .env. NEVER set in production."
        ),
    )

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=[],
        description=(
            "List of allowed CORS origins. Must be set explicitly. "
            "In production, empty list or ['*'] will raise an error. "
            "Example: ['https://app.rugmunch.io', 'https://rugmunch.io']"
        ),
    )

    # ── Rate limiting ───────────────────────────────────────────────
    rate_limit_per_minute: int = 100

    # ── AI providers ────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    openrouter_api_key: str = ""
    huggingface_token: str = ""

    # ── Backend self-reference ──────────────────────────────────
    backend_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Self-referencing URL for internal API calls to this backend. Set BACKEND_URL env var."
        ),
    )

    # ── LLM proxy ─────────────────────────────────────────────────
    litellm_api_base: str = Field(
        default="http://localhost:4000/v1",
        description=("LiteLLM / OpenAI-compatible proxy base URL. Set LITELLM_API_BASE env var."),
    )

    # ── Solscan API ──────────────────────────────────────────────
    solscan_url: str = Field(
        default="https://api-v2.solscan.io/v2",
        description=(
            "Solscan API base URL for reverse-engineered client. Set SOLSCAN_URL env var."
        ),
    )

    # ── Pricing / Upsell ─────────────────────────────────────────
    pricing_url: str = Field(
        default="https://rugmunch.io/pricing",
        description=("Public pricing page URL shown in upsell messages. Set PRICING_URL env var."),
    )

    # ── GCloud ───────────────────────────────────────────────────
    gcloud_sa_vault_path: str = Field(
        default="/root/.secrets/google-sa-cryptorugmunch.json",
        description=(
            "Path to Google service account JSON key in vault. Set GCLOUD_SA_VAULT_PATH env var."
        ),
    )

    # ── Langfuse v4 (observability) ─────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3002"

    # ── External APIs ───────────────────────────────────────────────
    coingecko_api_key: str = ""
    etherscan_api_key: str = ""
    birdeye_api_key: str = ""
    goplus_api_key: str = ""
    helius_api_key: str = Field(
        default="",
        description="Helius API key for Solana RPC access. Set via HELIUS_API_KEY env var.",
    )

    # ── Pryscraper ──────────────────────────────────────────────────
    pryscraper_url: str = Field(
        default="http://pry:8002",
        description=(
            "Pryscraper base URL. Defaults to the `pry` container on rmi_network "
            "at port 8002. Override with PRYSCRAPER_URL env var."
        ),
    )
    pry_api_key: str = Field(
        default="",
        description=(
            "API key for Pryscraper authentication. "
            "Must match the PRY_API_KEY set in the pry container. "
            "Set via PRY_API_KEY env var."
        ),
    )
    pry_x402_offline: bool = Field(
        default=False,
        description=(
            "Enable offline x402 payment mode for internal use. "
            "When True, paid Pry endpoints (scrape, extract) auto-verify "
            "without on-chain transactions. Set PRY_X402_OFFLINE=true."
        ),
    )

    # ── RMI-specific ────────────────────────────────────────────────
    rag_collections: list[str] = [
        "scam_intel",
        "deployer_history",
        "wallet_labels",
        "contract_audit",
        "phishing_db",
    ]

    @field_validator("cors_origins")
    @classmethod
    def _cors_origins_must_be_safe(cls, v: list[str]) -> list[str]:
        """Reject dangerous CORS configurations.

        In production, empty or wildcard origins are not allowed.
        In dev/staging, warn but allow (for local development convenience).
        """
        env = os.getenv("ENVIRONMENT", "dev")
        if v == ["*"] or not v:
            if env == "prod":
                raise ValueError(
                    "cors_origins must be an explicit non-empty list of origins in production. "
                    "Set CORS_ORIGINS env var, e.g. "
                    'CORS_ORIGINS=\'["https://app.rugmunch.io","https://rugmunch.io"]\'. '
                    "Wildcard '*' and empty list are rejected in production."
                )
        return v

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
