# RMI Supabase Integration — AI-First, Web3-Forward

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    RMI Backend (FastAPI)                 │
│                                                         │
│  supabase_router.py     supabase_oauth_router.py        │
│  supabase_auth_router.py  supabase_service.py           │
│  supabase_rag.py        db_client.py                    │
└──────────────┬──────────────────────────────────────────┘
               │ httpx + service_role key
               ▼
┌─────────────────────────────────────────────────────────┐
│                    Supabase                              │
│                                                         │
│  ┌───────────┐  ┌───────────┐  ┌───────────────────┐  │
│  │   Auth    │  │  Postgres │  │  Row Level         │  │
│  │ (JWT+OAuth)│  │ (Database)│  │  Security (RLS)    │  │
│  └───────────┘  └───────────┘  └───────────────────┘  │
│                                                         │
│  ┌───────────┐  ┌───────────┐  ┌───────────────────┐  │
│  │  Storage  │  │  Edge     │  │  Real-time         │  │
│  │ (Files)   │  │ Functions │  │  Subscriptions     │  │
│  └───────────┘  └───────────┘  └───────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Key Integration Points

### 1. Authentication (Web3 + Traditional)
- **JWT auth** via `supabase-auth` skill + `auth.py`
- **OAuth providers**: GitHub, Google (configured in `supabase_oauth_router.py`)
- **Wallet auth**: EVM + Solana wallet connection (non-custodial)
- **x402 trial tracking**: Device fingerprint + wallet-based quotas

### 2. Database (Postgres via Supabase)
- **User profiles**: `users` table with premium tiers, notification prefs
- **Intelligence data**: whale_movements, market_trending_tokens, scam_alerts
- **Content**: content posts, comments, upvotes, gamification events
- **x402 payments**: transaction logs, tool usage, trial tracking
- **Retention**: 90-day auto-cleanup for non-security data

### 3. RAG / Vector Store
- Redis-based vector store for crypto intelligence (`rag_service.py`)
- Lightweight SQLite+TF-IDF fallback (`rag_lightweight.py`)
- Collections: wallet_profiles, token_analysis, scam_patterns, forensic_reports, market_intel
- n8n workflow ingests news articles into RAG

### 4. Env Vars Required
```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=eyJh...
SUPABASE_SERVICE_KEY=eyJh...
SUPABASE_JWT_SECRET=...
```

## MCP (Model Context Protocol) Integration

- **`/api/v1/x402/tools-catalog`** — Full MCP catalog of 51 (44 MCP + 7 bundles) tools
- **`app/mcp/x402_mcp_server.py`** — MCP server implementation
- **`app/mcp_router.py`** — Routes MCP tool calls to backend functions
- **GitHub repo**: `Rug-Munch-Media-LLC/rug-munch-intelligence-mcp` (public)

## x402 Payment Protocol

- **`/.well-known/x402`** — Protocol discovery document
- **7 chains**: Solana (Facilitator), Base (Facilitator), ETH/BSC/ARB/OPT/POL (Self-verify)
- **Trial**: 1 free call (no wallet), 3 free calls (with wallet)
- **Payment**: USDC micropayments via HTTP 402
- **Repos**: `x402-gateway-solana`, `x402-gateway-base`, `x402-twitter-view`

## AI-Forward Architecture

```
User Request → Backend API → Orchestrator (9 agents)
                    │               │
                    ├── Supabase    ├── Wallet clustering
                    ├── Redis RAG   ├── Scam detection
                    ├── News Agg    ├── Threat intel
                    └── x402 Gate   └── Cross-chain analysis
```

## Web3 Best Practices Applied
- **Non-custodial**: No private keys stored server-side
- **RLS**: Row Level Security on all Supabase tables
- **Device fingerprinting**: Anti-abuse for trial system
- **On-chain verification**: Self-verify mode for 5 chains
- **Facilitator mode**: Cloudflare Workers for Base/Solana
