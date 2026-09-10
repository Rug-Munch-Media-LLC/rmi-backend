# Environment Variable Inventory — rmi-backend + bots
Inventory of every env var the backend and the Telegram bots read.
- **os.getenv() vars (app-wide source scan): 369 unique**
- **Pydantic-settings fields (`app/config.py`): 127**
- Source of truth for the working `.env`: `.env.example` / `.env.production.example`.
- Secrets: NEVER in `.env` — see `docs/OPERATIONS.md` for gopass layout.

`[REVIEW]` marks vars whose purpose could not be confirmed from a quick scan.

## 1. Pydantic-settings fields (app/config.py)
Env name = field name uppercased. All fields read via `settings.<field>`; default shown as declared.
| Field | Env var | Purpose | Default | Required |
|---|---|---|---|---|
| `environment` | `ENVIRONMENT` | Runtime env: dev|staging|prod|development (default prod) | `"prod"` | no |
| `log_level` | `LOG_LEVEL` | Logging level (INFO/DEBUG/...) | `"INFO"` | no |
| `port` | `PORT` | HTTP port for the FastAPI app | `8000` | no |
| `backend_url` | `BACKEND_URL` | Self-referencing URL for internal API calls | `"http://localhost:8000"` | no |
| `frontend_url` | `FRONTEND_URL` | Frontend URL for OAuth redirects/CORS | `"https://rugmunch.io"` | no |
| `database_url` | `DATABASE_URL` | PostgreSQL async connection string | `"postgresql+asyncpg://rmi:rmi@localhost/rmi"` | no |
| `redis_url` | `REDIS_URL` | Redis connection string | `"redis://localhost:6379/0"` | no |
| `redis_host` | `REDIS_HOST` | Redis host (Docker: rmi-redis) | `"rmi-redis"` | no |
| `redis_port` | `REDIS_PORT` | Redis port | `6379` | no |
| `redis_password` | `REDIS_PASSWORD` | Redis password (must be set in prod) | `""` | no |
| `redis_cache_url` | `REDIS_CACHE_URL` | Redis DB for LLM cache | `"redis://:__SET_REDIS_PASSWORD__@rmi-redis:6379/1"` | no |
| `llm_cache_ttl` | `LLM_CACHE_TTL` | LLM response cache TTL (seconds) | `3600` | no |
| `osint_cache_ttl` | `OSINT_CACHE_TTL` | TTL seconds for OSINT lookup cache | `3600` | no |
| `searxng_base_url` | `SEARXNG_BASE_URL` | Self-hosted SearXNG endpoint (JSON API) | `"http://127.0.0.1:8889"` | no |
| `qdrant_url` | `QDRANT_URL` | Qdrant HTTP endpoint for RAG collections | `(auto-resolve)` | no |
| `clickhouse_host` | `CLICKHOUSE_HOST` | ClickHouse host (Docker: rmi-clickhouse) | `"rmi-clickhouse"` | no |
| `clickhouse_port` | `CLICKHOUSE_PORT` | ClickHouse TCP port (native, 9000) | `9000` | no |
| `clickhouse_user` | `CLICKHOUSE_USER` | ClickHouse user | `"rmi"` | no |
| `clickhouse_password` | `CLICKHOUSE_PASSWORD` | ClickHouse password | `""` | no |
| `neo4j_uri` | `NEO4J_URI` | Neo4j bolt URI | `""` | no |
| `neo4j_user` | `NEO4J_USER` | Neo4j user | `"neo4j"` | no |
| `neo4j_password` | `NEO4J_PASSWORD` | Neo4j password | `""` | no |
| `memgraph_uri` | `MEMGRAPH_URI` | Memgraph URI for knowledge graph | `"bolt://localhost:7687"` | no |
| `memgraph_user` | `MEMGRAPH_USER` | Memgraph user | `""` | no |
| `memgraph_password` | `MEMGRAPH_PASSWORD` | Memgraph password | `""` | no |
| `supabase_url` | `SUPABASE_URL` | Supabase project URL | `""` | no |
| `supabase_key` | `SUPABASE_KEY` | Supabase anon key | `""` | no |
| `supabase_service_key` | `SUPABASE_SERVICE_KEY` | Supabase service-role key | `""` | no |
| `jwt_secret` | `JWT_SECRET` | HMAC secret for JWT (HS256 fallback), >=32 chars | `"dev-secret-please-set-JWT_SECRET-env-var-32chars"` | ⚠ prod |
| `jwt_algorithm` | `JWT_ALGORITHM` | JWT algorithm | `"RS256"` | no |
| `jwt_private_key` | `JWT_PRIVATE_KEY` | RSA PRIVATE KEY PEM (RS256 signing) | `""` | no |
| `jwt_public_key` | `JWT_PUBLIC_KEY` | RSA PUBLIC KEY PEM (RS256 verification) | `""` | no |
| `jwt_expire_minutes` | `JWT_EXPIRE_MINUTES` | JWT expiry minutes | `(empty)` | no |
| `rmi_auth_token` | `RMI_AUTH_TOKEN` | Shared X-API-Key header secret | `""` | ⚠ prod |
| `rmi_internal_key` | `RMI_INTERNAL_KEY` | Internal service-to-service API key | `""` | ⚠ prod |
| `rmi_consumer_keys` | `RMI_CONSUMER_KEYS` | Consumer ID -> API key mapping (JSON) | `{}` | no |
| `rmi_rag_api_key` | `RMI_RAG_API_KEY` | API key for rugbrain-api | `""` | no |
| `dev_bypass_auth` | `DEV_BYPASS_AUTH` | Bypass auth in dev ONLY | `False` | no |
| `totp_issuer` | `TOTP_ISSUER` | TOTP issuer name | `"RugMunch Intelligence"` | no |
| `google_client_id` | `GOOGLE_CLIENT_ID` | Google OAuth client id | `""` | no |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | `""` | no |
| `google_redirect_uri` | `GOOGLE_REDIRECT_URI` | Google OAuth redirect URI | `"https://rugmunch.io/api/v1/auth/google/callback"` | no |
| `telegram_bot_token` | `TELEGRAM_BOT_TOKEN` | Main bot token | `""` | no |
| `telegram_allowed_users` | `TELEGRAM_ALLOWED_USERS` | Owner/operator ids (fail-closed) | `""` | no |
| `scanner_alerts_enabled` | `SCANNER_ALERTS_ENABLED` | Scanner → Telegram alerts master switch | `True` | no |
| `cors_origins` | `CORS_ORIGINS` | Allowed CORS origins | `[]` | no |
| `rate_limit_per_minute` | `RATE_LIMIT_PER_MINUTE` | Rate limit per minute | `100` | no |
| `rate_limiter_redis_db` | `RATE_LIMITER_REDIS_DB` | Redis DB for rate limiter | `4` | no |
| `rate_limit_salt` | `RATE_LIMIT_SALT` | Salt for rate-limit key hashing | `""` | no |
| `ollama_url` | `OLLAMA_URL` | Ollama base URL (LLM generation) | `"http://localhost:11434"` | no |
| `infinity_url` | `INFINITY_URL` | Infinity embedding/rerank server base URL | `(auto-resolve)` | no |
| `openrouter_api_key` | `OPENROUTER_API_KEY` | OpenRouter API key | `""` | no |
| `huggingface_token` | `HUGGINGFACE_TOKEN` | HuggingFace token | `""` | no |
| `litellm_api_base` | `LITELLM_API_BASE` | LiteLLM / OpenAI-compatible proxy base URL | `"http://localhost:4000/v1"` | no |
| `langfuse_public_key` | `LANGFUSE_PUBLIC_KEY` | Langfuse public key | `""` | no |
| `langfuse_secret_key` | `LANGFUSE_SECRET_KEY` | Langfuse secret key | `""` | no |
| `langfuse_host` | `LANGFUSE_HOST` | Langfuse host | `"http://localhost:3002"` | no |
| `sentry_dsn` | `SENTRY_DSN` | Sentry DSN | `""` | no |
| `glitchtip_dsn` | `GLITCHTIP_DSN` | GlitchTip DSN | `""` | no |
| `otel_enabled` | `OTEL_ENABLED` | OpenTelemetry on/off | `False` | no |
| `sentry_traces_sample_rate` | `SENTRY_TRACES_SAMPLE_RATE` | Sentry trace sample rate | `0.1` | no |
| `phoenix_enabled` | `PHOENIX_ENABLED` | Phoenix on/off | `False` | no |
| `phoenix_endpoint` | `PHOENIX_ENDPOINT` | Phoenix OTLP gRPC endpoint | `"http://phoenix:4317"` | no |
| `phoenix_project` | `PHOENIX_PROJECT` | Phoenix project | `"rugmunch-rag"` | no |
| `phoenix_ui_url` | `PHOENIX_UI_URL` | Phoenix UI URL | `"http://phoenix:6006"` | no |
| `coingecko_api_key` | `COINGECKO_API_KEY` | CoinGecko API key | `""` | no |
| `etherscan_api_key` | `ETHERSCAN_API_KEY` | Etherscan API key | `""` | no |
| `birdeye_api_key` | `BIRDEYE_API_KEY` | Birdeye API key | `""` | no |
| `goplus_api_key` | `GOPLUS_API_KEY` | GoPlus API key | `""` | no |
| `helius_api_key` | `HELIUS_API_KEY` | Helius API key for Solana RPC | `""` | no |
| `apify_api_token` | `APIFY_API_TOKEN` | Apify token | `""` | no |
| `basescan_api_key` | `BASESCAN_API_KEY` | Basescan API key | `""` | no |
| `moralis_api_key` | `MORALIS_API_KEY` | Moralis API key | `""` | no |
| `bitquery_api_key` | `BITQUERY_API_KEY` | Bitquery API key | `""` | no |
| `lunarcrush_api_key` | `LUNARCRUSH_API_KEY` | LunarCrush API key | `""` | no |
| `chainabuse_api_key` | `CHAINABUSE_API_KEY` | Chainabuse API key | `""` | no |
| `webacy_api_key` | `WEBACY_API_KEY` | Webacy API key | `""` | no |
| `gmgn_api_key` | `GMGN_API_KEY` | GMGN API key | `""` | no |
| `reth_url` | `RETH_URL` | Reth (Ethereum RPC) endpoint | `"https://ethereum-rpc.publicnode.com"` | no |
| `alchemy_key` | `ALCHEMY_KEY` | Alchemy key | `""` | no |
| `bscscan_key` | `BSCSCAN_KEY` | BscScan key | `""` | no |
| `solscan_url` | `SOLSCAN_URL` | Solscan API base URL | `"https://api-v2.solscan.io/v2"` | no |
| `redpanda_broker` | `REDPANDA_BROKER` | Redpanda broker address | `"rmi-redpanda:9092"` | no |
| `pricing_url` | `PRICING_URL` | Public pricing page URL | `"https://rugmunch.io/pricing"` | no |
| `prompts_dir` | `PROMPTS_DIR` | Path to prompts directory | `""` | no |
| `spam_data_dir` | `SPAM_DATA_DIR` | Spam data directory | `"/app/data/spam"` | no |
| `real_cats_path` | `REAL_CATS_PATH` | Real-CATS dataset JSON path | `""` | no |
| `pryscraper_url` | `PRYSCRAPER_URL` | Pryscraper base URL | `"http://pry:8002"` | no |
| `pry_api_key` | `PRY_API_KEY` | Pryscraper auth key | `""` | no |
| `pry_x402_offline` | `PRY_X402_OFFLINE` | Offline x402 mode | `False` | no |
| `x402_verify_url` | `X402_VERIFY_URL` | x402 payment verification URL | `"http://localhost:8000/api/v1/x402/verify"` | no |
| `rag_collections` | `RAG_COLLECTIONS` | RugBrain default collections | `(empty)` | no |
| `free_scan_limit` | `FREE_SCAN_LIMIT` | Free scan limit | `5` | no |
| `pro_scan_limit` | `PRO_SCAN_LIMIT` | Pro tier scan limit | `100` | no |
| `elite_scan_limit` | `ELITE_SCAN_LIMIT` | Elite tier scan limit (0=unlimited) | `0` | no |
| `rag_embedding_dim` | `RAG_EMBEDDING_DIM` | RAG embedding dim (0=auto) | `0` | no |
| `wallet_vault_password` | `WALLET_VAULT_PASSWORD` | Wallet vault password | `""` | no |
| `databus_url` | `DATABUS_URL` | DataBus URL (auto-derived if empty) | `""` | no |
| `eth_labels_url` | `ETH_LABELS_URL` | Ethereum labels API URL | `""` | no |
| `llama_rag_enabled` | `LLAMA_RAG_ENABLED` | Legacy LlamaIndex RAG switch | `False` | no |
| `ghost_url` | `GHOST_URL` | Headless Ghost instance URL | `""` | no |
| `ghost_admin_api_key` | `GHOST_ADMIN_API_KEY` | Ghost Admin API key | `""` | no |
| `mirror_api_key` | `MIRROR_API_KEY` | Mirror.xyz key | `""` | no |
| `paragraph_api_key` | `PARAGRAPH_API_KEY` | Paragraph key | `""` | no |
| `medium_api_token` | `MEDIUM_API_TOKEN` | Medium token | `""` | no |
| `medium_author_id` | `MEDIUM_AUTHOR_ID` | Medium author id | `""` | no |
| `substack_session_id` | `SUBSTACK_SESSION_ID` | Substack session cookie | `""` | no |
| `substack_newsletter` | `SUBSTACK_NEWSLETTER` | Substack newsletter slug | `""` | no |
| `nostr_private_key` | `NOSTR_PRIVATE_KEY` | Nostr nsec hex | `""` | no |
| `nostr_relays` | `NOSTR_RELAYS` | Nostr relays | `"wss://relay.damus.io,wss://relay.nostr.band"` | no |
| `farcaster_api_key` | `FARCASTER_API_KEY` | Neynar key | `""` | no |
| `farcaster_signer_uuid` | `FARCASTER_SIGNER_UUID` | Neynar signer UUID | `""` | no |
| `bluesky_identifier` | `BLUESKY_IDENTIFIER` | Bluesky handle | `""` | no |
| `bluesky_app_password` | `BLUESKY_APP_PASSWORD` | Bluesky app password | `""` | no |
| `mastodon_access_token` | `MASTODON_ACCESS_TOKEN` | Mastodon token | `""` | no |
| `mastodon_instance_url` | `MASTODON_INSTANCE_URL` | Mastodon instance | `""` | no |
| `newsletter_from_email` | `NEWSLETTER_FROM_EMAIL` | Newsletter from email | `"newsletter@rugmunch.io"` | no |
| `postmark_api_key` | `POSTMARK_API_KEY` | Postmark token | `""` | no |
| `mailgun_api_key` | `MAILGUN_API_KEY` | Mailgun key | `""` | no |
| `mailgun_domain` | `MAILGUN_DOMAIN` | Mailgun domain | `""` | no |
| `platform_wallet_address` | `PLATFORM_WALLET_ADDRESS` | Platform wallet for subscription fees | `""` | no |
| `embedder_primary` | `EMBEDDER_PRIMARY` | Primary embedding backend (qwen3\|openrouter) | `"qwen3"` | no |
| `embedder_primary_model` | `EMBEDDER_PRIMARY_MODEL` | Model for primary embedder | `"qwen3-embedding-4b"` | no |
| `embedder_fallback` | `EMBEDDER_FALLBACK` | DEPRECATED Ollama fallback | `""` | no |
| `embedder_cache_ttl` | `EMBEDDER_CACHE_TTL` | Embedding cache TTL | `86400` | no |
| `embedder_cache_maxsize` | `EMBEDDER_CACHE_MAXSIZE` | Embedding cache maxsize | `10000` | no |
## 2. Bot-domain vars (app/domains/telegram/**)
Read directly with `os.getenv()` inside the bot domain (`secure_launcher`, `rugmunchbot`, `adminbot`).
| Var | Purpose | Default | Required |
|---|---|---|---|
| `ALERT_BOT_TOKEN_KEY` | Env-key override for alert bot token (default BOT_TOKEN) | optional env key name | no |
| `ALERT_TARGETS` | Alert routing map "<chat_id>:<label>,..." (alert_hooks.py) | empty | no |
| `BIRDEYE_API_KEY` | Birdeye Solana token data key | empty | no |
| `BOT_DB_PATH` | Override bot SQLite path | package data dir | no |
| `BOT_FLOOD_REDIS_DB` | Redis DB for flood-control cache | 7 | no |
| `BRIDGE_CONTEXT_ENDPOINT` | Bridge chat context endpoint | http://127.0.0.1:8095/chat/context | no |
| `DOCKER_CONTAINER` | Truthy when running inside Docker | unset | no |
| `FLARESOLVERR_URL` | FlareSolverr Cloudflare-bypass instance | see code | no |
| `GHOST_ADMIN_API_KEY` | Ghost Admin API key (<id>:<secret>) | empty | no |
| `GHOST_ADMIN_API_URL` | Ghost Admin API URL | https://blog.rugmunch.io | no |
| `LOG_LEVEL` | Logging level | INFO | no |
| `REDIS_HOST` | Redis host (legacy split) | empty | no |
| `REDIS_PASSWORD` | Redis password (legacy split) | empty | no |
| `REDIS_PORT` | Redis port (legacy split) | 6379 | no |
| `REDIS_URL` | Redis URL (preferred) | empty | no |
| `RMI_BACKEND_URL` | Bot→backend base URL | localhost:8000 / rmi-backend:8000 in Docker | no |
| `RMI_NEWS_TITLE_REWRITE_ENABLED` | LLM title rewrite for swept TG news | false | no |
| `RMI_PERSONAL_CHANNEL_ID` | Personal channel chat id | empty | no |
| `RUGMUNCH_SCAM_DENYLIST` | Known-scam address denylist (anti_abuse.py) | empty | no |
| `SCANNER_ALERTS_ENABLED` | Scanner→Telegram alerts master switch | true | no |
| `SENTINEL_URL` | Deep forensic scan endpoint override | {RMI_BACKEND_URL}/api/v1/x402-tools/sentinel_scan | no |
| `TELEGRAM_ALLOWED_USERS` | Owner/operator ids, comma-separated — FAIL-CLOSED | empty = no owner commands | ⚠ if owner cmds needed |
| `TELEGRAM_BOT_TOKEN` | Main bot token (gopass: [secrets]/rugmunchbot/bot_token) | empty | ⚠ YES |

## 3. App-wide os.getenv() vars (all of app/)
Full unique set from the source scan — includes aliases and domain-specific knobs. Purpose is best-effort; `[REVIEW]` = unconfirmed.
| Scope | Var count |
|---|---|
| AUTH | 1 |
| BOT (telegram domain) | 14 |
| CLICKHOUSE | 13 |
| OTHER | 197 |
| POSTGRES / DATABASE | 9 |
| REDIS | 6 |
| RMI-SPECIFIC | 22 |
| SECRETS / EXTERNAL APIS | 105 |

---

### AUTH (1)
| Var | Purpose |
|---|---|
| `TOTP_ISSUER` | TOTP issuer name |

### BOT (telegram domain) (14)
| Var | Purpose |
|---|---|
| `ALERT_BOT_TOKEN_KEY` | Env-key override for alert bot token (default BOT_TOKEN) |
| `ALERT_TARGETS` | Alert routing map "<chat_id>:<label>,..." (alert_hooks.py) |
| `BOT_DB_PATH` | Override bot SQLite path |
| `BOT_FLOOD_REDIS_DB` | Redis DB for flood-control cache |
| `BRIDGE_CONTEXT_ENDPOINT` | Bridge context endpoint for chat |
| `DOCKER_CONTAINER` | Truthy when running inside Docker |
| `RMI_BACKEND_URL` | Bot→backend base URL |
| `RMI_NEWS_TITLE_REWRITE_ENABLED` | LLM title rewrite for swept TG news (default off) |
| `RMI_PERSONAL_CHANNEL_ID` | Personal channel chat id |
| `RUGMUNCH_SCAM_DENYLIST` | Known-scam address denylist (anti_abuse.py) |
| `RUGMUNCHBOT_METRICS_HOST` | Prometheus metrics bind host for the bot metrics server (observability.py, default 127.0.0.1) |
| `RUGMUNCHBOT_METRICS_PORT` | Prometheus metrics bind port (observability.py, default 9191) |
| `SCANNER_ALERTS_ENABLED` | Master switch for scanner→Telegram alerts |
| `SENTINEL_URL` | Deep forensic scan endpoint override |
| `TELEGRAM_ALLOWED_USERS` | Owner/operator Telegram user ids (fail-closed) |
| `TELEGRAM_BOT_TOKEN` | Main bot token from @BotFather |

### CLICKHOUSE (13)
| Var | Purpose |
|---|---|
| `CH_DATABASE` | ClickHouse database (alias) |
| `CH_HOST` | ClickHouse host (alias) |
| `CH_PASSWORD` | ClickHouse password (alias) |
| `CH_PORT` | ClickHouse port (alias) |
| `CH_USER` | ClickHouse user (alias) |
| `CLICKHOUSE_DATABASE` | ClickHouse database (alias) |
| `CLICKHOUSE_DB` | ClickHouse database (alias) |
| `CLICKHOUSE_HOST` | ClickHouse host (config.py clickhouse_host) |
| `CLICKHOUSE_PASS` | ClickHouse password (alias) |
| `CLICKHOUSE_PASSWORD` | ClickHouse password (config.py clickhouse_password) |
| `CLICKHOUSE_PORT` | ClickHouse port (config.py clickhouse_port) |
| `CLICKHOUSE_RETENTION_DAYS` | ClickHouse retention in days |
| `CLICKHOUSE_USER` | ClickHouse user (config.py clickhouse_user) |

### OTHER (197)
| Var | Purpose |
|---|---|
| `AGENT_DEFAULT_CHAIN` | Default chain for agents (app/agents/config.py) |
| `AGENT_HTTP_TIMEOUT` | HTTP timeout for agent tool calls |
| `AGENT_LLM_BASE_URL` | LLM base URL for agents |
| `AGENT_LLM_MODEL` | Agent LLM model |
| `AGENT_TEMPERATURE` | Agent LLM temperature |
| `ALCHEMY_ARB_WS` | Alchemy Arbitrum websocket |
| `ALCHEMY_BASE_WS` | Alchemy Base websocket |
| `ALCHEMY_ETH_WS` | Alchemy Ethereum websocket |
| `ALCHEMY_OPT_WS` | Alchemy Optimism websocket |
| `API_BASE` | Base URL for internal API calls |
| `APP_VERSION` | Deployed app version label |
| `ARBITRUM_RPC` | Arbitrum RPC endpoint |
| `ARB_RPC` | Arbitrum RPC (alias) |
| `ARB_RPC_URL` | Arbitrum RPC URL (alias) |
| `ASTERPAY_EUR_OFFRAMP_URL` | AsterPay EUR offramp URL |
| `ASTERPAY_SEPA_IBAN` | AsterPay SEPA IBAN |
| `ASTERPAY_SETTLE_URL` | AsterPay settle URL |
| `ASTERPAY_VERIFY_URL` | AsterPay verify URL |
| `AVALANCHE_RPC` | Avalanche RPC |
| `AVAX_RPC_URL` | Avalanche RPC URL |
| `BACKEND_DIR` | Path to backend repo (ops scripts) |
| `BACKEND_URL` | Self-referencing API base URL (config.py backend_url) |
| `BACKUP_DIR` | Backup directory path |
| `BACKUP_LOG` | Backup log path |
| `BACKUP_RETENTION_DAYS` | Backup retention in days |
| `BASE_RPC` | Base RPC endpoint |
| `BASE_RPC_URL` | Base RPC URL |
| `BLOCKSCOUT_URL` | Blockscout instance URL |
| `BRIGHTDATA_PROXY_URL` | BrightData residential proxy URL |
| `BSC_RPC` | BSC RPC endpoint |
| `BSC_RPC_URL` | BSC RPC URL |
| `CERTSTREAM_ENABLED` | Certstream feed on/off |
| `CERTSTREAM_URL` | Certstream websocket URL |
| `CHAIN_INDEXER_CLICKHOUSE_DB` | ClickHouse DB for chain-indexer |
| `CHANNEL_ALERTS` | Telegram alerts channel id |
| `CHANNEL_NEWS` | Telegram news channel id |
| `CHAT_DB_PATH` | SQLite path for chat history |
| `CHAT_GUARDRAILS_ALERT_THRESHOLD` | Guardrails alert threshold |
| `CHAT_GUARDRAILS_ALERT_WINDOW` | Guardrails alert window |
| `CHAT_GUARDRAILS_LOG` | Guardrails log path |
| `CHAT_GUARDRAILS_LOG_MESSAGE_CHARS` | Guardrails log truncation chars |
| `CHAT_RATE_LIMIT_PER_HOUR` | Chat rate limit per hour |
| `CHAT_RATE_LIMIT_PER_MINUTE` | Chat rate limit per minute |
| `CHAT_RATE_LIMIT_REDIS_DB` | Redis DB for chat rate limit |
| `CNS_REGISTRY_ADDRESS` | CNS registry contract address |
| `COINBASE_CDP_NETWORK` | Coinbase CDP network |
| `CONTEXT_CHUNK_MODEL` | LLM for context chunking |
| `COORDINATED_MIN_KOLS` | Min KOLs for coordinated shill detection |
| `COORDINATED_WINDOW_H` | Window hours for coordinated detection |
| `DARKWEB_FEED_URL` | Darkweb feed URL |
| `DEGENFEED_HEALTH_CHECK_ENABLED` | Degenfeed health check on/off |
| `DEGENFEED_URL` | Degenfeed instance URL |
| `DELETED_ALERT_WEIGHT` | Weight for deleted-alert signals |
| `DEXSCREENER_URL` | DexScreener API base URL |
| `DIFY_URL` | Dify instance URL |
| `DOCKER_HOST_IP` | Docker host gateway IP for host services |
| `DUNE_QUERY_ID` | Dune query id |
| `ENS_RESOLVER_URL` | ENS resolver RPC URL |
| `ENVIRONMENT` | Runtime env: dev|staging|prod|development (config.py environment) |
| `ETH_RPC` | Ethereum RPC endpoint |
| `ETH_RPC_URL` | Ethereum RPC URL (alias) |
| `FACILITATOR_HEALTH_TTL` | Facilitator health TTL |
| `FACILITATOR_MAX_RETRIES` | Facilitator max retries |
| `FACILITATOR_VERIFY_TIMEOUT` | Facilitator verify timeout |
| `FAISS_DATA_DIR` | FAISS index data dir |
| `FLARESOLVERR_URL` | FlareSolverr instance URL |
| `FRONTEND_URL` | Frontend URL for OAuth/CORS (config.py frontend_url) |
| `FRONT_RUN_SELL_WINDOW_D` | Front-run sell window days |
| `FRONT_RUN_WINDOW_H` | Front-run window hours |
| `GHOST_ADMIN_API_URL` | Ghost Admin API URL |
| `GHOST_URL` | Ghost CMS instance URL |
| `GITHUB_CLIENT_ID` | GitHub OAuth client id |
| `GITHUB_REDIRECT_URI` | GitHub OAuth redirect URI |
| `GLITCHTIP_DSN` | GlitchTip error-tracking DSN (Sentry-compatible) |
| `GNN_MODEL_DIR` | GNN model directory |
| `GOOGLE_CLIENT_ID` | Google OAuth client id |
| `GOOGLE_REDIRECT_URI` | Google OAuth redirect URI |
| `HEIMDALL_BIN` | Heimdall binary path (static analysis) |
| `HELIOS_BIN` | Helios binary path |
| `HELIUS_RPC_URL` | Helius RPC URL |
| `HELIUS_WS` | Helius websocket URL |
| `HONEYDB_API_ID` | HoneyDB API id |
| `IDENTITY_DRIFT_THRESHOLD` | Identity drift threshold |
| `INDEXER_DEMAND_DIR` | Indexer demand directory |
| `INFINITY_URL` | Infinity embedding/rerank server base URL |
| `INFINITY_URL_FALLBACK` | Infinity fallback URL |
| `JS_BLOG_CHROME_CHANNEL` | Chrome channel for JS blog scraper |
| `JS_BLOG_SCRAPER_ALLOW_JS_RENDER` | Allow JS render for blog scraper |
| `JS_BLOG_SCRAPER_ENABLED` | JS blog scraper master switch |
| `JS_BLOG_SCRAPER_MAX_ARTICLES_PER_SOURCE` | Max articles per source |
| `JS_BLOG_SCRAPER_SOURCES` | JS blog sources |
| `JUPITER_QUOTE_URL` | Jupiter quote API URL |
| `KNOWN_SCAMMERS_PATH` | Known scammers file path |
| `LANGFUSE_HOST` | Langfuse host |
| `LANGFUSE_SAMPLING_RATE` | Langfuse sampling rate |
| `LISTMONK_PASS` | Listmonk API password |
| `LISTMONK_URL` | Listmonk API URL |
| `LISTMONK_USER` | Listmonk API user |
| `LITELLM_BASE_URL` | LiteLLM base URL (alias) |
| `LITELLM_DEFAULT_MODEL` | LiteLLM default model |
| `LITELLM_URL` | LiteLLM URL (alias) |
| `LLM_BASE_URL` | Generic LLM base URL |
| `LLM_CACHE_TTL` | LLM response cache TTL |
| `LLM_MODEL` | Generic LLM model |
| `LOG_LEVEL` | Logging level (INFO/DEBUG/...) |
| `LOOKONCHAIN_API_URL` | LookOnChain API URL |
| `MARKET_CONTEXT_CACHE_TTL` | Market context cache TTL |
| `MATRIX_BOT_USER` | Matrix bot user id |
| `MATRIX_HOMESERVER` | Matrix homeserver URL |
| `MATRIX_SERVER_NAME` | Matrix server name |
| `MCP_HOME` | MCP config home |
| `MEMGRAPH_URI` | Memgraph bolt URI (config.py memgraph_uri) |
| `MEMGRAPH_USER` | Memgraph user |
| `MINIO_ENDPOINT` | MinIO endpoint |
| `MIN_SELL_USD` | Min sell threshold USD |
| `NEWS_PROXY_HOST` | Public proxy IP for RSS feed rotation |
| `NEWS_STALE_AFTER_HOURS` | News staleness cutoff hours |
| `OLLAMA_BASE_URLS` | Ollama base URLs list |
| `OLLAMA_ENDPOINT` | Ollama endpoint (alias) |
| `OLLAMA_URL` | Ollama URL (alias) |
| `OPTIMISM_RPC` | Optimism RPC endpoint |
| `OPT_RPC` | Optimism RPC (alias) |
| `OTEL_ENABLED` | OpenTelemetry export on/off |
| `PAIR_CACHE_DIR` | Pair cache directory |
| `PAYAI_SETTLE_URL` | PayAI settle URL |
| `PAYAI_VERIFY_URL` | PayAI verify URL |
| `PHOENIX_ENABLED` | Arize Phoenix RAG observability on/off |
| `PHOENIX_ENDPOINT` | Phoenix OTLP gRPC endpoint |
| `PHOENIX_PROJECT` | Phoenix project name |
| `POLYGON_RPC` | Polygon RPC endpoint |
| `POLY_RPC_URL` | Polygon RPC URL |
| `PREFER_OWN_DATA` | Prefer own data over third-party |
| `PRIMEV_AGENT_ID` | PrimeV agent id |
| `PRIMEV_RPC_URL` | PrimeV RPC URL |
| `PROMPTS_DIR` | Prompts directory |
| `PROXY_LIST` | Proxy list |
| `PROXY_SCRAPER_ENABLED` | Proxy scraper switch |
| `PROXY_SCRAPER_MAX_BYTES` | Proxy scraper max bytes |
| `PROXY_SCRAPER_TIMEOUT_DIRECT` | Proxy scraper direct timeout |
| `PROXY_SCRAPER_TIMEOUT_FLARE` | Proxy scraper FlareSolverr timeout |
| `PROXY_SCRAPER_TIMEOUT_PROXY` | Proxy scraper proxy timeout |
| `PROXY_URL` | Proxy URL |
| `PRYSCRAPER_URL` | Pryscraper base URL |
| `QDRANT_URL` | Qdrant HTTP endpoint |
| `QUERY_TRANSFORM_MODEL` | Query transform LLM |
| `QWEN_MODEL` | Qwen model name |
| `RAG_ANALYSIS_MODEL` | RAG analysis LLM |
| `RAG_API_URL` | RAG API URL |
| `RAG_EVAL_FAITHFULNESS_MIN` | RAG faithfulness floor |
| `RAG_EVAL_HIT_RATE_MIN` | RAG hit-rate floor |
| `RAG_INGEST_URL` | RAG ingest URL |
| `RAG_QDRANT_COLLECTION` | RAG Qdrant collection |
| `RAG_QDRANT_URL` | RAG Qdrant URL |
| `RAG_RERANK_MODEL` | RAG rerank model |
| `RAG_STORE` | RAG store backend |
| `RATE_LIMITER_REDIS_DB` | Redis DB for rate limiter state |
| `RATE_LIMIT_SALT` | Salt for rate-limit key hashing |
| `REAL_CATS_PATH` | Real-CATS dataset path |
| `REDPANDA_BROKER` | Redpanda broker address |
| `RETH_URL` | Reth (Ethereum RPC) endpoint |
| `RGM_SEED_TABLE` | RGM seed table |
| `RIP_KNOWN_LP_LOCKERS` | Known LP locker denylist |
| `SCANNER_API_URL` | Scanner API URL |
| `SCANNER_ARCHIVE_RPC_URL` | Archive RPC for scanners |
| `SCANNER_CLICKHOUSE_ENABLED` | Scanner ClickHouse logging switch |
| `SCANNER_DATA_ACCESS_MODE` | Scanner data access mode |
| `SCAN_HARD_TIMEOUT_S` | Scan hard timeout seconds |
| `SCAN_STATUS_TTL` | Scan status TTL |
| `SENTRY_DSN` | Sentry DSN |
| `SENTRY_TRACES_SAMPLE_RATE` | Sentry trace sample rate |
| `SHILL_PRE_BUY_WINDOW_D` | Shill pre-buy window days |
| `SHILL_RUG_HISTORY_SELL_FACTOR` | Shill rug-history sell factor |
| `SHILL_SELL_WINDOW_H` | Shill sell window hours |
| `SLITHER_BIN` | Slither binary path |
| `SOLANATRACKER_SECURE_URL` | SolanaTracker secure URL |
| `SOLANA_RPC` | Solana RPC endpoint |
| `SOLANA_RPC_URL` | Solana RPC URL |
| `SOL_PRICE_USD` | SOL price override USD |
| `SPAM_DATA_DIR` | Spam data directory |
| `SUPABASE_URL` | Supabase project URL |
| `TELEGRAM_ALERT_CHAT_ID` | Alert chat id |
| `TEMPORAL_HOST` | Temporal host |
| `THREAT_BLOCKLIST_PATH` | Threat blocklist path |
| `TWEET_ARCHIVE_DUMP_THRESHOLD` | Tweet archive dump threshold |
| `VAULT_ADDR` | HashiCorp Vault address |
| `WALLET_ALERT_TTL` | Wallet alert TTL |
| `WALLET_BURST_THRESHOLD` | Wallet burst threshold |
| `WALLET_CORRELATION_PATH` | Wallet correlation file path |
| `WALLET_EMBEDDINGS_COLLECTION` | Wallet embeddings collection |
| `WALLET_EMBEDDINGS_DIM` | Wallet embeddings dim |
| `WALLET_LARGE_TRANSFER_USD` | Large transfer threshold USD |
| `WALLET_VAULT_PATH` | Wallet vault path |
| `WALLET_VELOCITY_WINDOW` | Wallet velocity window |
| `WEBHOOK_ENGINE_ENABLED` | Poll-based webhook engine master switch |
| `WEBHOOK_TIMEOUT` | Webhook timeout |
| `X_CLIENT_ID` | X OAuth client id |
| `X_REDIRECT_URI` | X OAuth redirect URI |

### POSTGRES / DATABASE (9)
| Var | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async connection string (config.py database_url) |
| `DB_HOST` | Postgres host (alias, used by _build_db_url_from_env) |
| `PG_URL` | Postgres URL (alias) |
| `POSTGRES_DB` | Postgres database name |
| `POSTGRES_HOST` | Postgres host |
| `POSTGRES_PASSWORD` | Postgres password |
| `POSTGRES_PORT` | Postgres port |
| `POSTGRES_RETENTION_DAYS` | Postgres retention days |
| `POSTGRES_USER` | Postgres user |

### REDIS (6)
| Var | Purpose |
|---|---|
| `REDIS_CACHE_URL` | Redis URL for LLM cache (DB 1) |
| `REDIS_DB` | Redis DB index |
| `REDIS_HOST` | Redis host |
| `REDIS_PASSWORD` | Redis password |
| `REDIS_PORT` | Redis port |
| `REDIS_URL` | Redis connection string |

### RMI-SPECIFIC (22)
| Var | Purpose |
|---|---|
| `RMI_API_URL` | RMI API URL |
| `RMI_AUTH_TOKEN` | Shared X-API-Key auth secret |
| `RMI_BRIEFING_LLM_WRITER` | Daily brief LLM writer model |
| `RMI_BRIEFING_MAX_TOKENS` | Daily brief max tokens |
| `RMI_CHAT_MAX_TOKENS` | Chat max tokens |
| `RMI_DATA_DIR` | RMI data directory |
| `RMI_FAST_MODEL` | Fast model name |
| `RMI_FREE_API_KEY` | Free-tier API key |
| `RMI_INTERNAL_KEY` | Internal service-to-service API key |
| `RMI_JWT_ALGORITHM` | JWT algorithm |
| `RMI_JWT_PRIVATE_KEY` | JWT RSA private key |
| `RMI_JWT_PUBLIC_KEY` | JWT RSA public key |
| `RMI_JWT_SECRET` | JWT secret (alias) |
| `RMI_RAG_API_KEY` | rugbrain-api X-API-Key |
| `RMI_SMTP_FROM` | SMTP From address |
| `RMI_SMTP_HOST` | SMTP host (rmi-mail) |
| `RMI_SMTP_PASS` | SMTP password |
| `RMI_SMTP_PORT` | SMTP port |
| `RMI_SMTP_USER` | SMTP user |
| `RMI_WALLET_MASTER_PASSWORD` | Wallet master password |
| `RMI_WEBHOOK_COOLDOWN_SECONDS` | Webhook cooldown seconds |
| `RMI_WEBHOOK_MIN_USD` | Webhook min USD |

### SECRETS / EXTERNAL APIS (105)
| Var | Purpose |
|---|---|
| `ADMIN_API_KEY` | Admin API key for backend admin endpoints |
| `AGENT_LLM_API_KEY` | LLM key for agents (falls back to OPENROUTER_BACKUP_API_KEY) |
| `ALCHEMY_API_KEY` | Alchemy EVM RPC key |
| `ALCHEMY_KEY` | Alchemy EVM RPC key (config.py alchemy_key) |
| `ALCHEMY_SOLANA_KEY` | Alchemy Solana key |
| `APIFY_API_TOKEN` | Apify API token |
| `ARBISCAN_API_KEY` | Arbiscan API key |
| `ARBISCAN_KEY` | Arbiscan API key (alias) |
| `ARKHAM_API_KEY` | Arkham Intelligence API key |
| `ARKHAM_WS_KEY` | Arkham websocket key |
| `ASTERPAY_API_KEY` | AsterPay offramp API key |
| `ASTERPAY_API_SECRET` | AsterPay API secret |
| `BASESCAN_API_KEY` | Basescan API key |
| `BASESCAN_KEY` | Basescan API key (alias) |
| `BIRDEYE_API_KEY` | Birdeye Solana token data API key |
| `BIRDEYE_KEY` | Birdeye API key (alias) |
| `BITQUERY_API_KEY` | Bitquery GraphQL API key |
| `BITQUERY_OAUTH_TOKEN` | Bitquery OAuth token |
| `BLOCKSCOUT_API_KEY` | Blockscout API key |
| `BSCSCAN_API_KEY` | BscScan API key (alias) |
| `BSCSCAN_KEY` | BscScan API key (config.py bscscan_key) |
| `CEREBRAS_API_KEY` | Cerebras inference API key |
| `CHAINABUSE_API_KEY` | Chainabuse API key |
| `COINBASE_CDP_API_KEY` | Coinbase CDP API key |
| `COINBASE_CDP_API_SECRET` | Coinbase CDP API secret |
| `COINDESK_API_KEY` | Coindesk API key |
| `COINGECKO_API_KEY` | CoinGecko price/market API key |
| `COINGECKO_API_KEY_PRO` | CoinGecko Pro API key |
| `COINMARKETCAP_API_KEY` | CoinMarketCap API key |
| `CRYPTOCOMPARE_API_KEY` | CryptoCompare API key |
| `CRYPTOPANIC_API_KEY` | CryptoPanic news API key |
| `DASHSCOPE_API_KEY` | DashScope (Qwen) API key |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `DIFY_API_KEY` | Dify workflow API key |
| `DUNE_API_KEY` | Dune Analytics API key |
| `EMAILREP_API_KEY` | EmailRep API key |
| `ETHERSCAN_API_KEY` | Etherscan API key |
| `ETHERSCAN_KEY` | Etherscan API key (alias) |
| `FTMSCAN_API_KEY` | FantomScan API key |
| `GHOST_ADMIN_API_KEY` | Ghost Admin API key |
| `GHOST_CONTENT_API_KEY` | Ghost Content API key |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth client secret |
| `GMAIL_APP_PASSWORD` | [DEPRECATED] Gmail SMTP app password |
| `GMGN_API_KEY` | GMGN API key |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `GOPLUS_API_KEY` | GoPlus token-security API key |
| `GROQ_API_KEY` | Groq API key |
| `HELIUS_API_KEY` | Helius Solana RPC/webhook API key |
| `HF_TOKEN` | HuggingFace token (alias) |
| `HIBP_API_KEY` | HaveIBeenPwned API key |
| `HONEYDB_API_KEY` | HoneyDB API key |
| `HUGGINGFACE_TOKEN` | HuggingFace token |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key |
| `LENS_API_KEY` | Lens Protocol API key |
| `LITELLM_API_KEY` | LiteLLM proxy API key |
| `LLM_API_KEY` | Generic LLM API key |
| `LOOKONCHAIN_API_KEY` | LookOnChain API key |
| `LUNARCRUSH_API_KEY` | LunarCrush social sentiment API key |
| `MATRIX_ADMIN_TOKEN` | Matrix admin token |
| `MATRIX_BOT_PASSWORD` | Matrix bot password |
| `MATRIX_BOT_TOKEN` | Matrix bot access token |
| `MBAL_API_KEY` | MBAL API key |
| `MEMGRAPH_PASSWORD` | Memgraph password |
| `MERX_TRON_API_KEY` | Merx TRON API key |
| `MESSARI_API_KEY` | Messari API key |
| `METASLEUTH_LABELS_API_KEY` | MetaSleuth labels API key |
| `MINIO_ACCESS_KEY` | MinIO access key |
| `MINIO_SECRET_KEY` | MinIO secret key |
| `MISTRAL_API_KEY` | Mistral API key |
| `MORALIS_API_KEY` | Moralis API key |
| `NVIDIA_API_KEY` | NVIDIA NIM API key |
| `OLLAMA_API_KEY` | Ollama API key |
| `ONEINCH_API_KEY` | 1inch API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_BACKUP_API_KEY` | Backup OpenRouter key (llm_config.py fallback) |
| `PIEVERSE_API_KEY` | Pieverse API key |
| `POLYGONSCAN_API_KEY` | PolygonScan API key |
| `POLYGONSCAN_KEY` | PolygonScan key (alias) |
| `PRIMEV_API_KEY` | PrimeV API key |
| `PRY_API_KEY` | Pryscraper API key |
| `QDRANT_API_KEY` | Qdrant API key |
| `QUICKNODE_KEY` | QuickNode RPC key |
| `RAG_API_KEY` | RAG API key |
| `RAG_CONTEXT_MAX_TOKENS` | RAG context max tokens |
| `SANTIMENT_API_KEY` | Santiment API key |
| `SATOSHI_API_KEY` | Satoshi API key |
| `SCANNER_API_KEY` | Scanner API key |
| `SNOWTRACE_API_KEY` | SnowTrace API key |
| `SOLANATRACKER_API_KEY` | SolanaTracker API key |
| `SOLANA_TRACKER_API_KEY` | Solana Tracker key (alias) |
| `SOLSCAN_API_KEY` | Solscan API key |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key |
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook secret |
| `SUPABASE_ANON_KEY` | Supabase anon key |
| `SUPABASE_KEY` | Supabase anon key (config.py) |
| `SUPABASE_SERVICE_KEY` | Supabase service-role key |
| `TWITTER_BEARER_TOKEN` | X/Twitter bearer token |
| `VIRUSTOTAL_API_KEY` | VirusTotal API key |
| `WALLET_VAULT_PASSWORD` | Wallet vault password |
| `WEBACY_API_KEY` | Webacy API key |
| `WP_ADMIN_KEY` | Walletpress admin key |
| `X_API_KEY` | X API key |
| `X_CLIENT_SECRET` | X OAuth client secret |
