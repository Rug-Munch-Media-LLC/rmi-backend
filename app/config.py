"""Application configuration.

Pydantic-settings reads from environment variables (and .env in dev).
Import: `from app.config import settings`

CONSOLIDATION NOTICE (2026-07-24):
  All configuration values must live here. Do NOT use os.getenv() in
  non-config files. Import `settings` from this module and use the
  typed attribute. See `app/core/secrets.py` for secrets.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from typing import Literal
from urllib.parse import quote, urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _hostname_resolves(hostname: str) -> bool:
    """Best-effort DNS check for a hostname (no raise)."""
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except OSError:
        return False


def resolve_service_url(key: str, docker: str, local: str) -> str:
    """Resolve a service URL the way the process can actually reach it.

    Priority:
      1. Explicit env var wins (already set in systemd/compose).
      2. Docker-compose service name — used when it resolves from this
         process (i.e. we are inside the compose network).
      3. Host-mapped ``localhost`` URL — used when the Docker name fails
         DNS, which is the case for host-side systemd runs of the backend
         and bots (rugmunchbot.service / rmiadminbot.service).

    This mirrors ``app.rag.crawler._ensure_host_connectivity``: the RAG
    bridge (``scan_memory``, ``engine``, ``whale_indexer``) previously
    hardcoded ``rmi-qdrant:6333`` / ``qwen3-embedder:8004`` defaults and
    silently failed every host-side scan-to-RAG index (fail-open), leaving
    ``scanned_tokens`` empty.
    """
    val = os.getenv(key)
    if val:
        return val
    host = ""
    try:
        host = (urlparse(docker).hostname or "").lower()
    except ValueError:
        host = ""
    if host and _hostname_resolves(host):
        return docker
    return local


def _build_db_url_from_env() -> str | None:
    """Construct a PostgreSQL async URL from ``POSTGRES_*`` env vars.

    Returns ``None`` when the env vars are not set (caller should fall back).
    """
    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST")
    if not host:
        return None
    user = os.getenv("POSTGRES_USER", "rmi")
    password = os.getenv("POSTGRES_PASSWORD", "rmi")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "rmi")
    # URL-encode the password — production secrets contain '/' and '=' which
    # break the URL path/query otherwise (asyncpg rejects the malformed URL).
    password = quote(password, safe="")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


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

    # ── Bot / repo paths (generic defaults; set in production env) ──────
    bot_users_db_path: str = Field(
        default="data/bot_users.db",
        description="Path to the Telegram bot users SQLite DB. Set BOT_USERS_DB_PATH env var.",
    )
    bot_launcher_path: str = Field(
        default="secure_launcher.py",
        description="Path to the standalone bot secure launcher. Set BOT_LAUNCHER_PATH env var.",
    )
    repo_base_path: str = Field(
        default=".",
        description="Base directory containing checked-out repos. Set REPO_BASE_PATH env var.",
    )

    # ── Database / Cache ────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://rmi:rmi@localhost/rmi",
        description=(
            "PostgreSQL async connection string. "
            "Override with DATABASE_URL env var, or set POSTGRES_HOST/USER/PASSWORD/DB individually. "
            "In Docker, defaults to rmi-postgres:5432 when POSTGRES_HOST is set."
        ),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description=(
            "Redis connection string. "
            "MUST be overridden in production with auth. "
            "Set REDIS_URL env var (e.g. redis://:password@rmi-redis:6379/0)."
        ),
    )
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_cache_url: str = Field(
        default="redis://:__SET_REDIS_PASSWORD__@rmi-redis:6379/1",
        description="Separate Redis DB for LLM cache. Set REDIS_CACHE_URL env var.",
    )
    llm_cache_ttl: int = 3600
    osint_cache_ttl: int = Field(
        default=3600,
        description=(
            "TTL seconds for OSINT lookup cache (sherlock/maigret/theHarvester/"
            "Amass/SpiderFoot are expensive). Set OSINT_CACHE_TTL env var."
        ),
    )

    # ── Web search (self-hosted SearXNG) ─────────────────────────────────
    searxng_base_url: str = Field(
        default="http://127.0.0.1:8889",
        description=(
            "Self-hosted SearXNG endpoint (JSON API, no auth) used by the chat "
            "brain for current-affairs fallback and the bot web_search tool. "
            "Defaults to the actual running instance (127.0.0.1:8889). Set "
            "SEARXNG_BASE_URL env var to override."
        ),
    )

    # ── RAG store (Qdrant) ───────────────────────────────────────────────
    qdrant_url: str = Field(
        default_factory=lambda: resolve_service_url(
            "QDRANT_URL",
            docker="http://rmi-qdrant:6333",
            local="http://localhost:6333",
        ),
        description=(
            "Qdrant HTTP endpoint for RAG collections (scanned_tokens, "
            "whale_transfers, news_v2, ...). Set QDRANT_URL env var. Default "
            "auto-resolves: Docker-compose name inside the network, host-mapped "
            "localhost:6333 on host-side systemd runs."
        ),
    )

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
            "Set via secrets manager or JWT_SECRET env var."
        ),
    )
    jwt_algorithm: str = "RS256"
    jwt_private_key: str = Field(
        default="",
        description=(
            "RSA PRIVATE KEY in PEM format (for RS256 signing). "
            "Provide the key string directly, or a file path readable by the app. "
            "Generate with: openssl genrsa -out jwt-private.pem 2048. "
            "Set via secrets manager or JWT_PRIVATE_KEY env var."
        ),
    )
    jwt_public_key: str = Field(
        default="",
        description=(
            "RSA PUBLIC KEY in PEM format (for RS256 verification). "
            "Provide the key string directly, or a file path readable by the app. "
            "Derive from private key: openssl rsa -in jwt-private.pem -pubout -out jwt-public.pem. "
            "Set via secrets manager or JWT_PUBLIC_KEY env var."
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
    rmi_consumer_keys: dict[str, str] = Field(
        default={},
        description=(
            "Consumer ID -> API key mapping for per-consumer authentication "
            "(X-Consumer-ID header). Set RMI_CONSUMER_KEYS env var as JSON, "
            'e.g. {"dex-parity": "<key>"}.'
        ),
    )
    rmi_rag_api_key: str = Field(
        default="",
        description="API key for rmi-rag-api (X-API-Key header). Set RMI_RAG_API_KEY env var.",
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
    # No default: owner/operator ids must be provisioned explicitly via env.
    telegram_allowed_users: str = ""
    scanner_alerts_enabled: bool = Field(
        default=True,
        description=(
            "Master switch for scanner → Telegram alerts. Set SCANNER_ALERTS_ENABLED=false to disable alert noise."
        ),
    )

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
    # Ollama base URL. LLM-generation only (app.routers.ai_stream). RugBrain
    # embeddings/reranking use Qwen3 exclusively (``qwen_embed_url``).
    ollama_url: str = "http://localhost:11434"
    qwen_embed_url: str = Field(
        default_factory=lambda: resolve_service_url(
            "EMBEDDER_URL",
            docker="http://127.0.0.1:8004/v1",
            local="http://127.0.0.1:8004/v1",
        ),
        description=(
            "Qwen3 (OpenAI-compatible embedding/rerank server) base URL for the "
            "RugBrain (crypto-native RAG) pipeline: POST /v1/embeddings "
            "(qwen3-embedding-4b) and POST /rerank (qwen3-reranker-4b). "
            "2026-08-29: the 0.6B fast tier is retired — embeddings are "
            "4B-only (:8004). "
            "Set EMBEDDER_URL env var. Default auto-resolves: Docker-compose name inside "
            "the network, host-mapped localhost on host-side systemd runs."
        ),
    )
    # DeepInfra PRIMARY embedder (2026-08-29 consolidation). Same model family
    # as qwen_embed_url (:8004) ⇒ same 2560-dim vector space; fallback stays
    # index-compatible. Set DEEPINFRA_EMBED_URL / DEEPINFRA_EMBED_MODEL env vars.
    deepinfra_embed_url: str = Field(
        default="https://api.deepinfra.com/v1/embeddings",
        description="DeepInfra Qwen3-Embedding-4B embeddings endpoint (PRIMARY embed lane).",
    )
    deepinfra_embed_model: str = Field(
        default="Qwen/Qwen3-Embedding-4B",
        description="DeepInfra embedding model id (native 2560-dim).",
    )
    embedding_dim: int = Field(
        default=2560,
        description=(
            "Canonical corpus embedding dimension — 4B native 2560. Every "
            "rmi-qdrant-v2 collection is 2560-dim Cosine. Set RAG_EMBEDDING_DIM."
        ),
    )
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

    # ── Phoenix (Arize) — RAG observability ────────────────────────────
    # OTLP gRPC endpoint of the self-hosted Phoenix instance. Inside the
    # Docker network this is ``http://phoenix:4317``; the host port is
    # 4319 (4317 belongs to the otel-collector). UI at http://phoenix:6006.
    phoenix_enabled: bool = False
    phoenix_endpoint: str = "http://phoenix:4317"
    phoenix_project: str = "rugmunch-rag"
    phoenix_ui_url: str = "http://phoenix:6006"

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
    # RugBrain (crypto-native RAG engine) default collections. Full taxonomy:
    # 18 collections across security / code / market / news / shared domains
    # (see docs/RAG_ARCHITECTURE.md).
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
        description=(
            "Enable LlamaIndex-based legacy RAG pipeline (superseded by RugBrain). Set LLAMA_RAG_ENABLED env var."
        ),
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
    # Platform wallet (receives subscription payments)
    platform_wallet_address: str = Field(
        default="", description="Platform wallet for subscription fees. Set PLATFORM_WALLET_ADDRESS."
    )

    # ── Embedder ────────────────────────────────────────────────────────
    # (legacy deprecated URL field removed 2026-09-02 — superseded by
    # ``qwen_embed_url`` (:8004/v1, Qwen3-Embedding-4B) above.)
    embedder_primary: str = Field(
        default="qwen3",
        description=(
            "Primary embedding backend: qwen3 | openrouter. "
            "Set EMBEDDER_PRIMARY. Default is Qwen3 (OpenAI-compatible /embeddings)."
        ),
    )
    embedder_primary_model: str = Field(
        default="qwen3-embedding-4b",
        description=(
            "Model used by the primary embedder when EMBEDDER_PRIMARY=qwen3 "
            "(qwen3-embedding-4b, 2560-dim native — 2026-08-29: the 0.6B fast "
            "tier is retired; this is the only local embedding model). "
            "Set EMBEDDER_PRIMARY_MODEL."
        ),
    )
    embedder_fallback: str = Field(
        default="",
        description=(
            "DEPRECATED — Ollama fallback model name. RAG embedding now uses "
            "Qwen3 exclusively; retained for config compat only. "
            "Set EMBEDDER_FALLBACK."
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

    @field_validator("database_url", mode="before")
    @classmethod
    def _database_url_from_env(cls, v: str) -> str:
        """Construct DB URL from ``POSTGRES_*`` env vars when ``DATABASE_URL`` is not set.

        In Docker Compose, ``POSTGRES_HOST=rmi-postgres`` is set, allowing the
        async engine to locate the database container via Docker DNS instead
        of ``localhost`` which only works in local dev.
        """
        # If DATABASE_URL was explicitly set (not the default), use it directly.
        # Pydantic Settings passes the env var value directly through this
        # validator.  We detect the default by string equality.
        default = "postgresql+asyncpg://rmi:rmi@localhost/rmi"
        if v != default:
            return v

        # Try building from POSTGRES_* env vars
        built = _build_db_url_from_env()
        if built is not None:
            return built

        # Fall back to the default
        return v

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

    @model_validator(mode="after")
    def _jwt_secret_must_be_set_in_prod(self) -> Settings:
        """Reject unset / known-default JWT secrets in production.

        Uses the validated ``environment`` field (which defaults to "prod")
        rather than a separate os.getenv lookup — previously the guard read
        ``os.getenv("ENVIRONMENT", "dev")`` while the field default was "prod",
        so a prod deploy that omitted ENVIRONMENT bypassed the check entirely.
        """
        if self.environment != "prod":
            return self
        _dev_defaults = (
            "dev-secret-CHANGE-ME",
            "dev-secret-please-set-JWT_SECRET-env-var-32chars",
        )
        if not self.jwt_secret or self.jwt_secret in _dev_defaults or len(self.jwt_secret) < 32:
            raise ValueError(
                "jwt_secret must be set to a secure random value (>=32 chars) in production. "
                "Generate with: openssl rand -hex 32. "
                "Set via secrets manager or JWT_SECRET env var."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


# Module-level singleton for convenience.
settings = get_settings()
