"""Application configuration.

Pydantic-settings reads from environment variables (and .env in dev).
Import: `from app.config import settings`

CONSOLIDATION NOTICE (2026-07-24):
  All configuration values must live here. Do NOT use os.getenv() in
  non-config files. Import `settings` from this module and use the
  typed attribute. See `app/core/secrets.py` for secrets (gopass).
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

    # ── Runtime ──────────────────────────────────────────────────────────
    environment: Literal["dev", "staging", "prod", "development"] = "prod"
    log_level: str = "INFO"
    port: int = 8000
    backend_url: str = Field(
        default="http://localhost:8000",
        description="Self-referencing URL for internal API calls. Set BACKEND_URL env var.",
    )
    frontend_url: str = Field(
        default="https://rugmunch.io",
        description="Frontend URL for OAuth redirects and CORS. Set FRONTEND_URL env var.",
    )

    # ── Database / Cache ────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://rmi:rmi@localhost/rmi",
        description=("PostgreSQL async connection string. MUST be overridden in production. Set DATABASE_URL env var."),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description=(
            "Redis connection string. "
            "MUST be overridden in production with auth. "
            "Set REDIS_URL env var (e.g. redis://:password@rmi-redis:6379/0)."
        ),
    )
    redis_host: str = "rmi-redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_cache_url: str = Field(
        default="redis://:__SET_REDIS_PASSWORD__@rmi-redis:6379/1",
        description="Separate Redis DB for LLM cache. Set REDIS_CACHE_URL env var.",
    )
    llm_cache_ttl: int = 3600

    # ── ClickHouse (analytics / cost logging) ────────────────────────────
    clickhouse_host: str = "rmi-clickhouse"
    clickhouse_port: int = 9000
    clickhouse_user: str = "rmi"
    clickhouse_password: str = ""

    # ── Neo4j / Memgraph (graph storage) ─────────────────────────────────
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    memgraph_uri: str = Field(
        default="bolt://localhost:7687",
        description="Memgraph URI for knowledge graph. Set MEMGRAPH_URI env var.",
    )
    memgraph_user: str = ""
    memgraph_password: str = ""

    # ── Supabase ─────────────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_service_key: str = ""

    # ── Auth ────────────────────────────────────────────────────────────
    jwt_secret: str = Field(
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
    rmi_auth_token: str = Field(
        default="",
        description=(
            "Shared secret for X-API-Key header authentication. "
            "Must be set in production. Generate with: openssl rand -hex 32. "
            "Set via RMI_AUTH_TOKEN env var or .env file."
        ),
    )
    rmi_internal_key: str = Field(
        default="",
        description="Internal service-to-service API key. Set RMI_INTERNAL_KEY env var.",
    )
    dev_bypass_auth: bool = Field(
        default=False,
        description=(
            "Explicitly opt in to bypass authentication in development mode. "
            "Set DEV_BYPASS_AUTH=true in .env. NEVER set in production."
        ),
    )
    totp_issuer: str = "RugMunch Intelligence"

    # ── OAuth ────────────────────────────────────────────────────────────
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "https://rugmunch.io/api/v1/auth/google/callback"

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_allowed_users: str = "7075336557"

    # ── CORS ────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=[],
        description="List of allowed CORS origins. Must be set explicitly.",
    )

    # ── Rate limiting ───────────────────────────────────────────────────
    rate_limit_per_minute: int = 100
    rate_limiter_redis_db: int = Field(
        default=4,
        description="Redis database index for rate limiter state. Set RATE_LIMITER_REDIS_DB.",
    )
    rate_limit_salt: str = Field(
        default="",
        description="Salt for rate limit key hashing. Set RATE_LIMIT_SALT env var.",
    )

    # ── AI providers ────────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    openrouter_api_key: str = ""
    huggingface_token: str = ""
    litellm_api_base: str = Field(
        default="http://localhost:4000/v1",
        description="LiteLLM / OpenAI-compatible proxy base URL. Set LITELLM_API_BASE env var.",
    )

    # ── Observability ──────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3002"
    sentry_dsn: str = ""
    glitchtip_dsn: str = ""
    otel_enabled: bool = False
    sentry_traces_sample_rate: float = 0.1

    # ── External APIs ───────────────────────────────────────────────────
    coingecko_api_key: str = ""
    etherscan_api_key: str = ""
    birdeye_api_key: str = ""
    goplus_api_key: str = ""
    helius_api_key: str = Field(
        default="",
        description="Helius API key for Solana RPC access. Set via HELIUS_API_KEY env var.",
    )
    apify_api_token: str = ""
    basescan_api_key: str = ""
    moralis_api_key: str = ""
    bitquery_api_key: str = ""
    lunarcrush_api_key: str = ""
    chainabuse_api_key: str = ""
    webacy_api_key: str = ""
    gmgn_api_key: str = ""

    # ── Blockchain RPC ──────────────────────────────────────────────────
    reth_url: str = "https://ethereum-rpc.publicnode.com"
    alchemy_key: str = ""
    bscscan_key: str = ""

    # ── Solscan ──────────────────────────────────────────────────────────
    solscan_url: str = Field(
        default="https://api-v2.solscan.io/v2",
        description="Solscan API base URL. Set SOLSCAN_URL env var.",
    )

    # ── Redpanda ────────────────────────────────────────────────────────
    redpanda_broker: str = "rmi-redpanda:9092"

    # ── Pricing / Upsell ────────────────────────────────────────────────
    pricing_url: str = Field(
        default="https://rugmunch.io/pricing",
        description="Public pricing page URL shown in upsell messages. Set PRICING_URL env var.",
    )

    # ── GCloud ──────────────────────────────────────────────────────────
    gcloud_sa_vault_path: str = Field(
        default="/root/.secrets/google-sa-cryptorugmunch.json",
        description="Path to Google service account JSON key in vault. Set GCLOUD_SA_VAULT_PATH.",
    )

    # ── Prompts ├───────────────────────────────────────────────────────
    prompts_dir: str = Field(
        default="",
        description="Path to prompts directory. Auto-detected if empty.",
    )

    # ── Data paths ──────────────────────────────────────────────────────
    spam_data_dir: str = "/app/data/spam"
    real_cats_path: str = Field(
        default="",
        description="Path to Real-CATS dataset JSON. Auto-detected if empty.",
    )

    # ── Pryscraper ──────────────────────────────────────────────────────
    pryscraper_url: str = Field(
        default="http://pry:8002",
        description="Pryscraper base URL. Defaults to the `pry` container on rmi_network.",
    )
    pry_api_key: str = Field(
        default="",
        description="API key for Pryscraper authentication. Set PRY_API_KEY env var.",
    )
    pry_x402_offline: bool = Field(
        default=False,
        description="Enable offline x402 payment mode for internal use.",
    )
    x402_verify_url: str = Field(
        default="http://localhost:8000/api/v1/x402/verify",
        description="URL for x402 payment verification. Set X402_VERIFY_URL env var.",
    )

    # ── RMI-specific ────────────────────────────────────────────────────
    rag_collections: list[str] = [
        "scam_intel",
        "deployer_history",
        "wallet_labels",
        "contract_audit",
        "phishing_db",
    ]
    free_scan_limit: int = 5
    pro_scan_limit: int = 100
    elite_scan_limit: int = 0  # 0 = unlimited
    rag_embedding_dim: int = 0  # 0 = auto-detect
    wallet_vault_password: str = ""
    databus_url: str = Field(
        default="",
        description="DataBus URL for internal API calls. Auto-derived if empty.",
    )
    eth_labels_url: str = Field(
        default="",
        description="Ethereum labels API URL. Auto-derived if empty.",
    )
    llama_rag_enabled: bool = Field(
        default=False,
        description="Enable LlamaIndex-based RAG pipeline. Set LLAMA_RAG_ENABLED env var.",
    )

    # ── Newsletter System ───────────────────────────────────────────────
    # Ghost (headless CMS)
    ghost_url: str = Field(
        default="",
        description="Headless Ghost instance URL. Set GHOST_URL env var.",
    )
    ghost_admin_api_key: str = Field(
        default="",
        description="Ghost Admin API key. Set GHOST_ADMIN_API_KEY env var.",
    )
    # Cross-platform publishing
    mirror_api_key: str = Field(default="", description="Mirror.xyz API key. Set MIRROR_API_KEY.")
    paragraph_api_key: str = Field(default="", description="Paragraph.xyz API key. Set PARAGRAPH_API_KEY.")
    medium_api_token: str = Field(default="", description="Medium API token. Set MEDIUM_API_TOKEN.")
    medium_author_id: str = Field(default="", description="Medium author ID. Set MEDIUM_AUTHOR_ID.")
    substack_session_id: str = Field(default="", description="Substack session cookie. Set SUBSTACK_SESSION_ID.")
    substack_newsletter: str = Field(default="", description="Substack newsletter slug. Set SUBSTACK_NEWSLETTER.")
    # Nostr
    nostr_private_key: str = Field(default="", description="Nostr nsec hex. Set NOSTR_PRIVATE_KEY.")
    nostr_relays: str = Field(
        default="wss://relay.damus.io,wss://relay.nostr.band", description="Nostr relays. Set NOSTR_RELAYS."
    )
    # Farcaster
    farcaster_api_key: str = Field(default="", description="Neynar API key. Set FARCASTER_API_KEY.")
    farcaster_signer_uuid: str = Field(default="", description="Neynar signer UUID. Set FARCASTER_SIGNER_UUID.")
    # Bluesky
    bluesky_identifier: str = Field(default="", description="Bluesky handle. Set BLUESKY_IDENTIFIER.")
    bluesky_app_password: str = Field(default="", description="Bluesky app password. Set BLUESKY_APP_PASSWORD.")
    # Mastodon
    mastodon_access_token: str = Field(default="", description="Mastodon access token. Set MASTODON_ACCESS_TOKEN.")
    mastodon_instance_url: str = Field(default="", description="Mastodon instance URL. Set MASTODON_INSTANCE_URL.")
    # Email delivery
    newsletter_from_email: str = Field(
        default="newsletter@rugmunch.io", description="From address for newsletters. Set NEWSLETTER_FROM_EMAIL."
    )
    postmark_api_key: str = Field(default="", description="Postmark server token. Set POSTMARK_API_KEY.")
    mailgun_api_key: str = Field(default="", description="Mailgun API key. Set MAILGUN_API_KEY.")
    mailgun_domain: str = Field(default="", description="Mailgun sending domain. Set MAILGUN_DOMAIN.")
    aws_ses_key: str = Field(default="", description="AWS SES API key. Set AWS_SES_KEY.")
    # Platform wallet (receives subscription payments)
    platform_wallet_address: str = Field(
        default="", description="Platform wallet for subscription fees. Set PLATFORM_WALLET_ADDRESS."
    )

    # ── Embedder ────────────────────────────────────────────────────────
    bge_m3_url: str = Field(
        default="http://localhost:8001",
        description="BGE-M3 embedding service URL (Docker container). Set BGE_M3_URL env var.",
    )
    embedder_primary: str = Field(
        default="bge-m3",
        description="Primary embedding backend: bge-m3 | ollama | openrouter. Set EMBEDDER_PRIMARY.",
    )
    embedder_fallback: str = Field(
        default="nomic-embed-text",
        description=(
            "Fallback model name for Ollama when primary backend fails. "
            "Set EMBEDDER_FALLBACK. Examples: nomic-embed-text, qwen3-embed:4b"
        ),
    )
    embedder_cache_ttl: int = Field(
        default=86400,
        description="TTL (seconds) for cached embedding vectors. Default 24h. Set EMBEDDER_CACHE_TTL.",
    )
    embedder_cache_maxsize: int = Field(
        default=10000,
        description="Max entries in the in-memory embedding cache. Set EMBEDDER_CACHE_MAXSIZE.",
    )

    # ── Validators ──────────────────────────────────────────────────────

    @field_validator("cors_origins")
    @classmethod
    def _cors_origins_must_be_safe(cls, v: list[str]) -> list[str]:
        """Reject dangerous CORS configurations."""
        env = os.getenv("ENVIRONMENT", "dev")
        if (v == ["*"] or not v) and env == "prod":
            raise ValueError(
                "cors_origins must be an explicit non-empty list of origins in production. Set CORS_ORIGINS env var."
            )
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_must_be_set_in_prod(cls, v: str) -> str:
        """Reject the dev default in production."""
        _dev_defaults = (
            "dev-secret-CHANGE-ME",
            "dev-secret-please-set-JWT_SECRET-env-var-32chars",
        )
        if v in _dev_defaults or len(v) < 32:
            env = os.getenv("ENVIRONMENT", "dev")
            if env == "prod":
                raise ValueError(
                    "jwt_secret must be set to a secure random value (>=32 chars) in production. "
                    "Generate with: openssl rand -hex 32. "
                    "Set via gopass (rmi/prod/jwt_secret) or JWT_SECRET env var."
                )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


# Module-level singleton for convenience.
settings = get_settings()
