# Architecture & Design Audit — 2026 Q3

> **Date:** 2026-07-24  
> **Scope:** rmi-backend, walletpress, pryscraper, degenfeed-web  
> **Audit areas:** API Design, Database Schema, Service Dependencies, Component Design, State Management

---

## Table of Contents

1. [API Design Audit](#1-api-design-audit)
2. [Database Schema Audit](#2-database-schema-audit)
3. [Service Dependency Map](#3-service-dependency-map)
4. [Summary & Priority Actions](#4-summary--priority-actions)

---

## 1. API Design Audit

### 1.1 RESTful Convention Comparison

| Aspect | ✅ Recommended | 🔴 rmi-backend | 🔴 walletpress | 🟡 pryscraper |
|--------|---------------|----------------|----------------|----------------|
| **Version prefix** | `/api/v1/` | ✅ `/api/v1/` (new) + root (legacy) | ❌ No versioning | ✅ `/v1/` |
| **Resource naming** | `/resource/{id}` | 🟡 Mixed — some `/{address}`, some `/me/` | ❌ Mix of `/action` and `/resource/action` | ✅ Consistent `/v1/resource/` |
| **HTTP methods** | GET read, POST create, PUT replace, PATCH partial, DELETE remove | 🟡 Some POST used for reads | ❌ `/alerts/delete` (POST) instead of `DELETE /alerts/{id}` | ✅ Generally correct |
| **Query params** | `?limit=N&offset=N&sort=field:asc` | 🟡 Inconsistent sort param naming | ❌ Mixed query patterns | 🟡 Generally OK |
| **Response envelope** | `{data, meta, errors}` | 🟡 Some `response_model`, some raw | ❌ No standard envelope | 🟡 Some endpoints |
| **Error format** | `{error, code, details}` | 🟡 Inconsistent | ❌ Inconsistent | 🟡 Mostly consistent |
| **Pagination** | `?cursor=base64` or `?offset=N` | 🟡 Cursor in some, offset in others | ❌ Not standardized | ✅ Cursor-based |

### 1.2 rmi-backend Route Structure

#### Dual routing system (migration in progress)

```
Legacy root routes (being deprecated):
  /analyze/{address}
  /label/{address}
  /token/{address}
  /health, /live, /ready

New V1 routes (strangler fig pattern):
  /api/v1/health/*
  /api/v1/version
  /api/v1/tokens/{address}
  /api/v1/tokens/{address}/risk
  /api/v1/analyze/{address}
  /api/v1/deployer/{address}
  /api/v1/contract/{address}
  /api/v1/entities/search
  /api/v1/alerts/*

Public API routes:
  /api/v1/public/token/{address}
  /api/v1/public/token/{address}/risk
  /api/v1/public/token/{address}/holders
  /api/v1/public/wallet/{address}
  /api/v1/public/wallet/{address}/labels
  /api/v1/public/wallet/{address}/history
  /api/v1/public/scanner/scan
```

#### 🔴 Issues found

1. **Stub routes**: Some v1 routes throw `ServiceUnavailableError` instead of returning proper error responses.
2. **Response model inconsistency**: Some routes use `response_model=TokenSummary` (Pydantic), others return raw dicts.
3. **Mixed naming**: `/me/alerts` (user-scoped) vs `/v1/providers/status` (system-scoped) — no consistent namespace convention.
4. **Action-as-path**: `/alerts/{alert_id}/dismiss` (good) but also `/cards/generate`, `/marketing/generate` (not RESTful — should be `POST /cards` with action in body).

### 1.3 walletpress Route Structure

```
No version prefix:
  /api-keys, /api-keys/revoke
  /alerts, /alerts/delete
  /webhooks, /webhooks/{id}
  /proof/commit, /proof/verify/{id}
  /admin/2fa/setup, /admin/2fa/status
  /vault/groups, /vault/group/{name}
  /wallet/{id}/compatibility
  /portfolio
  /audit-trail/export
  /tool/seed-repair
  /import/batch
```

#### 🔴 Issues found

1. **No API versioning** — all routes are at root level.
2. **Action-as-path**: `/alerts/delete` (POST) should be `DELETE /alerts/{id}`.
3. **Inconsistent resource hierarchy**: `/proof/commit` but `/proof/verify/{wallet_id}` — second path takes a param, first doesn't.
4. **No response_model** — all routes return raw JSON dicts.
5. **Mixed singular/plural**: `/api-keys` (plural) but `/audit-trail` (singular).

### 1.4 pryscraper Route Structure

```
Consistent /v1/ prefix:
  /v1/intel/snapshot
  /v1/breaker/status
  /v1/pipeline/hook
  /v1/pipeline/run
  /v1/transform
  /v1/pipe
  /v1/batch-file
  /v1/seo/analyze
  /v1/auth/credentials
  /v1/links
  /v1/schema
  /v1/extract/llm
  /v1/costing/dashboard
  /v1/pipelines (CRUD)
```

#### 🟡 Minor issues

1. Some routes use `/v1/{resource}/{action}` (fine) while others use `/v1/{action}-{modifier}` (`/v1/batch-file`, `/v1/pipe` — non-standard).
2. Some routes have no defined `response_model`.

### 1.5 degenfeed-web API (Frontend Proxy)

```
Next.js API route proxies:
  /api/feed/home?tab=&cursor=&limit=
  /api/feed/post/{id}
  /api/feed/post/{id}/like
  /api/feed/post/{id}/bookmark
  /api/preferences
  /api/moderation/report
  /api/publish
  /api/cross-reply
  /api/auth/* (via _proxy)
```

**Frontend client:** Uniform `ApiResponse<T>` envelope via `api.ts`:
```ts
interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  errors?: string[];
  meta?: Record<string, unknown>;
}
```

### 1.6 API Recommendations

**Standardize on across all apps:**

```yaml
URL format:     /api/v1/{resource}/{id}[/{subresource}]
HTTP methods:   GET=read, POST=create, PUT=replace, PATCH=partial, DELETE=remove
Query params:   ?limit=20&offset=0&sort=created_at:desc&filter=value
Response:       { data: T | T[], meta: { total, limit, offset, cursor }, errors: [] }
Error:          { error: string, code: string, details?: any }
Pagination:     Cursor-based for large sets, offset-based for admin UIs
```

**Immediate fixes:**

| App | Fix |
|-----|-----|
| walletpress | Add `/api/v1/` prefix to all routes, add Pydantic response models |
| rmi-backend | Replace stub routes with 501 Not Implemented, add `response_model` to all routes |
| rmi-backend | Remove action-as-path in `/marketing/generate` etc. |
| pryscraper | Standardize `/v1/batch-file` → `/v1/batch/file` |

---

## 2. Database Schema Audit

### 2.1 rmi-backend Models

#### Current models (7 models, 1 base class)

```
Base (DeclarativeBase)
├── TimestampMixin (created_at, updated_at)
├── User           → users
├── Token          → tokens
├── ScanResult     → scan_results
├── Alert          → alerts
├── AlertSubscription → alert_subscriptions
├── Label          → labels
└── Payment        → payments
```

#### 🔴 Critical: Missing Foreign Keys

Every model is a **standalone island**—no relationships, no foreign key constraints:

| Model | Has `owner_id`? | Has FK? | Has relationship? |
|-------|----------------|---------|-------------------|
| `Alert` | ✅ `owner_id: UUID` | ❌ | ❌ |
| `AlertSubscription` | ✅ `owner_id: UUID` | ❌ | ❌ |
| `ScanResult` | ❌ (but has `tier_required` ref) | ❌ | ❌ |
| `Payment` | ❌ (has `user_address: str`) | ❌ | ❌ |
| `Label` | ❌ | ❌ | ❌ |
| `Token` | ❌ | ❌ | ❌ |
| `User` | N/A | N/A | ❌ |

#### 🟡 Missing: Cascade Deletes

No model has `cascade="all, delete-orphan"` — deleting a User would leave orphaned Alerts, AlertSubscriptions, Payments.

#### 🟡 Missing: Composite Foreign Keys

`ScanResult` has `(token_address, chain)` but no FK to `Token(address, chain)`.

#### 🟡 Missing: Index on Foreign Key Columns

`owner_id` columns lack `index=True` on some models.

#### 🔴 Orphaned/Unreferenced Tables

No orphaned tables detected. However, `AlertSubscription.webhook_url` type is `String(2048)` — consider `Text` for long URLs.

### 2.2 walletpress — No ORM Models Found

walletpress backend uses **no SQLAlchemy ORM models**. The alembic migration (`001_initial_schema.py`) suggests there are database tables, but no Python model classes define them. This means:

- No type safety for DB operations
- No schema enforcement in code
- Potential migration drift between alembic and code

### 2.3 Database Schema Recommendations

#### P0 — Add Foreign Keys (rmi-backend)

```python
# app/models/alert.py
from sqlalchemy import ForeignKey

class Alert(Base, TimestampMixin):
    ...
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

# app/models/scan.py
class ScanResult(Base, TimestampMixin):
    ...
    # Add FK to tokens table
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tokens.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
```

#### P1 — Add Relationships

```python
# app/models/user.py
from sqlalchemy.orm import relationship

class User(Base, TimestampMixin):
    ...
    alerts: Mapped[list["Alert"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    subscriptions: Mapped[list["AlertSubscription"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
```

#### P2 — walletpress ORM Models

Create proper models in `walletpress/backend/core/` using SQLAlchemy 2.0 syntax to mirror the alembic schema.

---

## 3. Service Dependency Map

### 3.1 rmi-backend Dependency Graph

```
rmi-backend (FastAPI)
│
├── Internal Databases
│   ├── Redis (cache, pub/sub, rate limiting)
│   ├── PostgreSQL (primary ORM data)
│   ├── ClickHouse (time-series analytics)
│   ├── Neo4j (knowledge graph, entity resolution)
│   ├── Qdrant (vector embeddings)
│   └── MinIO (object storage, RAG documents)
│
├── Internal Services
│   ├── Redpanda (event streaming)
│   ├── n8n (intelligence workflows)
│   ├── MeiliSearch (full-text search)
│   └── FlareSolverr (Cloudflare bypass — optional)
│
├── External APIs (30+ connectors)
│   ├── Blockchain: Solana RPC, EVM RPC, Alchemy, Helius, QuickNode, Birdeye
│   ├── Security: GoPlus, TruffleHog, Semgrep
│   ├── Social: CoinGecko, DexScreener, DefiLlama, LunarCrush
│   ├── Analytics: Nansen, Arkham, Moralis, Blockchair
│   ├── AI: OpenAI, Anthropic, Ollama, Cerebras, Mistral
│   ├── Infra: Cloudflare R2, Supabase, Stripe
│   └── Other: Pryscraper (internal), GitHub, Etherscan
│
└── Consumers
    ├── rmi-frontend (Next.js web app)
    ├── rmi-rag (document RAG pipeline)
    ├── rmi-mcp-x402 (MCP server tools)
    ├── telegram-bot (alert delivery)
    └── n8n workflows
```

**Circular dependency check:**
- `rmi-backend` → `pryscraper` (HTTP client for scraping)
- `pryscraper` → `rmi-backend` ❓ (needs verification — agent system may call back)

### 3.2 walletpress Dependency Graph

```
walletpress (FastAPI)
│
├── Internal
│   ├── SQLite/File-based vault (encrypted key storage)
│   └── Local chain RPC (Solana, Bitcoin, EVM)
│
├── External APIs
│   ├── Solana RPC (Helius or public)
│   ├── Bitcoin RPC
│   └── EVM RPC (Infura/Alchemy)
│
└── Consumers
    ├── walletpress MCP server
    ├── walletpress CLI
    └── wp-plugin (WordPress)
```

**No circular dependencies.** walletpress is the most isolated service.

### 3.3 pryscraper Dependency Graph

```
pryscraper (FastAPI)
│
├── Optional Internal
│   ├── FlareSolverr (Cloudflare bypass)
│   └── Tor (anonymous crawling)
│
├── External APIs
│   ├── LLM Providers (OpenAI, Anthropic, Ollama, Cerebras)
│   ├── Apify (marketplace)
│   └── Various target websites
│
└── Consumers
    ├── rmi-backend (via pryscraper_bridge.py)
    ├── MCP servers (mcp_sse.py)
    └── x402-gateway
```

### 3.4 degenfeed-web Dependency Graph

```
degenfeed-web (Next.js)
│
├── Internal
│   ├── Cloudflare KV (cache, preferences)
│   └── Browser IndexedDB (identities, drafts, blocks)
│
├── External APIs (via proxy)
│   ├── Nostr relays (8+ WebSocket)
│   ├── Farcaster Hubs (hub.pinata.cloud)
│   ├── Lens API (api.lens.xyz)
│   ├── Bluesky API (public.api.bsky.app)
│   └── MeiliSearch (content search)
│
├── Internal Services (proxy)
│   └── rmi-backend (via Cloudflare Worker → API)
│
└── Consumers
    └── Browser (PWA, mobile, desktop)
```

### 3.5 Circular Dependency Risk

**⚠️ Potential cycle identified:**

```
rmi-backend ──HTTP──> pryscraper (scrape/extract)
pryscraper ──HTTP──> rmi-backend (via x402/agent??)
```

The `pryscraper_bridge.py` in rmi-backend calls pryscraper. If pryscraper's agent system calls back into rmi-backend endpoints, this creates a cycle. **Verify and break with async queue if found.**

### 3.6 Dependency Recommendations

| Priority | Action | App |
|----------|--------|-----|
| P0 | Verify no circular HTTP call between rmi-backend and pryscraper | Both |
| P1 | Add health check endpoints for all internal service dependencies | All |
| P1 | Implement circuit breakers for all 30+ external API connectors | rmi-backend |
| P2 | Document dependency env vars in each repo's `.env.example` | All |

---

## 4. Summary & Priority Actions

### Priority Matrix

| Priority | Area | Action | App(s) | Effort |
|----------|------|--------|--------|--------|
| **P0** | **DB** | Add foreign keys and relationships to all models | rmi-backend | 1d |
| **P0** | **API** | Standardize walletpress routes with `/api/v1/` prefix | walletpress | 2d |
| **P0** | **API** | Replace stub routes with proper NotImplemented | rmi-backend | 0.5d |
| **P0** | **Dep** | Verify no circular rmi-backend ↔ pryscraper calls | Both | 0.5d |
| **P1** | **Design** | Fix hardcoded colors in 7+ component files | degenfeed | 1d |
| **P1** | **State** | Add Zustand for shared feed/app state | degenfeed | 2d |
| **P1** | **Arch** | Split PostCard (1,578→~300 lines) | degenfeed | 1d |
| **P1** | **DB** | Add cascade deletes to all FK columns | rmi-backend | 0.5d |
| **P1** | **API** | Add `response_model` to all API routes | walletpress, pryscraper | 1d |
| **P2** | **Design** | Add Lucide or similar icon system | degenfeed | 1d |
| **P2** | **Arch** | Create `@degenfeed/theme` design token package | degenfeed | 1d |
| **P2** | **DB** | Create ORM models for walletpress | walletpress | 2d |
| **P2** | **API** | Add cursor-based pagination to list endpoints | rmi-backend, walletpress | 1d |
| **P2** | **Infra** | Add Storybook for component preview | degenfeed | 2d |

### Adoption Tracker

| App | REST Score | DB Score | Design Score | Dep Score | Overall |
|-----|-----------|---------|-------------|----------|---------|
| rmi-backend | 🟡 6/10 | 🔴 3/10 | N/A | 🟡 7/10 | 🟡 5/10 |
| walletpress | 🔴 3/10 | 🔴 2/10 | N/A | ✅ 8/10 | 🟡 4/10 |
| pryscraper | 🟡 7/10 | N/A | N/A | 🟡 7/10 | 🟡 7/10 |
| degenfeed-web | 🟡 7/10 | N/A | 🟡 6/10 | ✅ 8/10 | 🟡 7/10 |
