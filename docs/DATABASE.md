# RMI Backend — Database Architecture

> **Last updated:** 2026-07-23  
> **Scope:** All 6 data stores in the RMI production fleet

---

## 1. Multi-Database Strategy

RMI uses **5 specialized database engines** + **1 object store**, each chosen for a specific workload pattern. No single database serves all use cases.

| Engine | Workload | Why This Engine |
|--------|----------|----------------|
| **PostgreSQL 16** | OLTP — relational transactions | Mature, reliable, SQLAlchemy ORM, excellent migration tooling (Alembic) |
| **ClickHouse** | Time-series analytics | Column-oriented, 10-100x faster for analytical queries than Postgres, built for event data |
| **Neo4j 5 Enterprise** | Graph traversals | Native property graph, efficient multi-hop queries (wallet clustering, fund flow) |
| **Qdrant** | Vector similarity | Purpose-built for embeddings, HNSW index, payload filtering |
| **DragonflyDB** | In-memory cache | Redis-compatible, higher throughput than Redis, multi-threaded |
| **MinIO** | Object storage | S3-compatible, self-hosted, no egress costs |

```mermaid
graph TB
    APP[Application]
    APP --> PG[(PostgreSQL<br/>Source of Truth)]
    APP --> CH[(ClickHouse<br/>Analytics)]
    APP --> NEO[(Neo4j<br/>Graph)]
    APP --> QD[(Qdrant<br/>Vectors)]
    APP --> DRAG[(DragonflyDB<br/>Cache)]
    APP --> MIN[(MinIO<br/>Objects)]
    
    PG --> PGB[PgBouncer<br/>Port 6432]
    DRAG -.->|Seed on startup| PG
```

---

## 2. PostgreSQL (Primary OLTP)

**Container:** `rmi-postgres` — `postgres:16-alpine`  
**Port:** 5432  
**Database:** `rmi`  
**User:** `rmi`  
**Connection string:** `postgresql+asyncpg://rmi:${POSTGRES_PASSWORD}@rmi-postgres:5432/rmi`  
**Connection pooler:** PgBouncer (`rmi-pgbouncer`, port 6432, 200 max clients, transaction pooling)

### Tables

All tables use:
- **UUID primary keys** (generated via `uuid.uuid4()`)
- **`TimestampMixin`** — `created_at` (auto-set on insert) + `updated_at` (auto-updated on change)

#### `users` — Platform users

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK, default `uuid4()` |
| `email` | `VARCHAR(320)` | UNIQUE, INDEX, nullable |
| `wallet_address` | `VARCHAR(128)` | UNIQUE, INDEX, nullable |
| `role` | `VARCHAR(32)` | DEFAULT `'USER'`, NOT NULL |
| `tier` | `VARCHAR(32)` | DEFAULT `'free'`, NOT NULL |
| `xp` | `INTEGER` | DEFAULT `0`, NOT NULL |
| `level` | `INTEGER` | DEFAULT `1`, NOT NULL |

#### `tokens` — Token contract registry

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK |
| `address` | `VARCHAR(256)` | INDEX, NOT NULL |
| `chain` | `VARCHAR(32)` | DEFAULT `'solana'`, NOT NULL |
| `name` | `VARCHAR(256)` | DEFAULT `''` |
| `symbol` | `VARCHAR(64)` | INDEX, DEFAULT `''` |
| `decimals` | `INTEGER` | DEFAULT `9` |
| `total_supply` | `FLOAT` | DEFAULT `0.0` |
| `circulating_supply` | `FLOAT` | DEFAULT `0.0` |
| `holders` | `INTEGER` | DEFAULT `0` |
| `verified` | `BOOLEAN` | DEFAULT `false` |
| `risk_score` | `INTEGER` | DEFAULT `0` |
| `risk_level` | `VARCHAR(16)` | DEFAULT `'low'` |
| `extra` | `JSON` | nullable |

**Constraints:** `UNIQUE(chain, address)` — `uq_token_chain_address`

#### `scan_results` — Token safety scan results

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK |
| `token_address` | `VARCHAR(256)` | INDEX |
| `chain` | `VARCHAR(32)` | NOT NULL |
| `symbol` | `VARCHAR(64)` | DEFAULT `''` |
| `name` | `VARCHAR(256)` | DEFAULT `''` |
| `safety_score` | `INTEGER` | DEFAULT `50` |
| `risk_score` | `INTEGER` | DEFAULT `0` |
| `risk_level` | `VARCHAR(16)` | DEFAULT `'low'` |
| `risk_flags` | `JSON` | NOT NULL (array of strings) |
| `warnings` | `JSON` | NOT NULL (array of strings) |
| `modules_run` | `JSON` | NOT NULL (array of strings) |
| `extra` | `JSON` | nullable |
| `scanned_at` | `TIMESTAMPTZ` | NOT NULL |
| `tier_required` | `VARCHAR(16)` | DEFAULT `'free'` |
| `confidence` | `INTEGER` | DEFAULT `0` |

**Indexes:** `IX(token_address, chain)`, `IX(scanned_at)`

#### `alerts` — Fired security alerts

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK |
| `token_address` | `VARCHAR(256)` | INDEX |
| `chain` | `VARCHAR(32)` | NOT NULL |
| `alert_type` | `VARCHAR(64)` | NOT NULL |
| `severity` | `VARCHAR(16)` | DEFAULT `'info'` |
| `payload` | `JSON` | nullable |
| `fired_at` | `TIMESTAMPTZ` | NOT NULL |
| `owner_id` | `UUID` | INDEX, nullable |

#### `alert_subscriptions` — User alert subscriptions

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK |
| `token_address` | `VARCHAR(256)` | NOT NULL |
| `chain` | `VARCHAR(32)` | NOT NULL |
| `alert_types` | `JSON` | NOT NULL |
| `webhook_url` | `VARCHAR(2048)` | nullable |
| `active` | `BOOLEAN` | DEFAULT `true` |
| `owner_id` | `UUID` | INDEX, nullable |

**Indexes:** `IX(token_address, chain)`

#### `payments` — Payment records

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK |
| `tool` | `VARCHAR(128)` | NOT NULL |
| `chain` | `VARCHAR(32)` | NOT NULL |
| `tx_hash` | `VARCHAR(256)` | INDEX, nullable |
| `amount_paid` | `JSON` | nullable |
| `status` | `VARCHAR(32)` | DEFAULT `'pending'` |
| `user_address` | `VARCHAR(128)` | INDEX, nullable |

#### `labels` — Wallet labels

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `UUID` | PK |
| `address` | `VARCHAR(256)` | INDEX |
| `chain` | `VARCHAR(32)` | NOT NULL |
| `label_type` | `VARCHAR(64)` | NOT NULL |
| `label_value` | `VARCHAR(256)` | NOT NULL |
| `source` | `VARCHAR(64)` | NOT NULL |
| `confidence` | `FLOAT` | DEFAULT `0.5` |
| `entity` | `VARCHAR(256)` | nullable |
| `entity_category` | `VARCHAR(64)` | nullable |
| `attributes` | `JSON` | nullable |
| `verified_at` | `TIMESTAMPTZ` | nullable |

**Constraints:** `UNIQUE(address, chain, label_type, label_value)` — `uq_label`  
**Indexes:** `IX(label_type)`, `IX(entity)`

---

## 3. ClickHouse (Analytics)

**Container:** `rmi-clickhouse` — `clickhouse/clickhouse-server:26.6.2.81`  
**Port:** 8123 (HTTP), 9000 (native TCP)  
**Database:** `rmi`  
**Connection string:** `http://rmi:${CLICKHOUSE_PASSWORD}@rmi-clickhouse:8123`

All tables use the **MergeTree engine** with `ORDER BY` timestamp for time-range pruning.

#### `x402_revenue` — Payment revenue tracking

```sql
CREATE TABLE rmi.x402_revenue (
    tx_hash       String,
    tool_id       String,
    agent_id      String,
    amount_usd    Float64,
    chain         String,
    paid_at       DateTime,
    tier          String
) ENGINE = MergeTree() ORDER BY paid_at;
```

#### `mcp_calls` — MCP tool call metrics

```sql
CREATE TABLE rmi.mcp_calls (
    call_id       String,
    tool_id       String,
    agent_id      String,
    latency_ms    UInt32,
    success       UInt8,
    called_at     DateTime
) ENGINE = MergeTree() ORDER BY called_at;
```

#### `indexer_lag` — Blockchain indexer lag

```sql
CREATE TABLE rmi.indexer_lag (
    chain         String,
    block_lag     UInt32,
    last_block    UInt64,
    updated_at    DateTime
) ENGINE = MergeTree() ORDER BY (chain, updated_at);
```

#### `reputation_deltas` — Deployer reputation changes

```sql
CREATE TABLE rmi.reputation_deltas (
    deployer_id   String,
    old_alpha     UInt32,
    old_beta      UInt32,
    new_alpha     UInt32,
    new_beta      UInt32,
    event_type    String,
    updated_at    DateTime
) ENGINE = MergeTree() ORDER BY (deployer_id, updated_at);
```

#### `cost_attribution` — Per-request cost breakdown

```sql
CREATE TABLE rmi.cost_attribution (
    ts                    DateTime,
    tool_id               String,
    agent_id              String,
    llm_tokens_cents      UInt32,
    rpc_requests_cents    UInt32,
    storage_reads_cents   UInt32,
    compute_cents         UInt32,
    price_charged_cents   UInt32
) ENGINE = MergeTree()
      ORDER BY (ts, tool_id, agent_id)
      PARTITION BY toYYYYMM(ts);
```

Schema is managed via `infra/clickhouse_schema.sql` — idempotent (all `CREATE IF NOT EXISTS`). Apply with:

```bash
docker exec rmi-clickhouse clickhouse-client < infra/clickhouse_schema.sql
```

---

## 4. Neo4j (Entity Graph)

**Container:** `rmi-neo4j` — `neo4j:5-enterprise`  
**Port:** 7474 (HTTP), 7687 (Bolt)  
**Connection string:** `bolt://rmi-neo4j:7687`

### Node Labels

| Label | Properties | Purpose |
|-------|-----------|---------|
| `Wallet` | address, chain, first_seen, last_seen, tag | Individual blockchain wallets |
| `Token` | address, chain, symbol, name, created_at | Token contracts |
| `Entity` | name, category, confidence, source | Real-world entity behind addresses |
| `Cluster` | id, label, size, confidence, created_at | Wallet cluster (linked addresses) |
| `Transaction` | hash, chain, timestamp, value, from, to | On-chain transaction record |
| `Deployer` | address, chain, first_seen, total_deployed, scam_count | Contract deployer |
| `ThreatActor` | name, category, source, first_seen | Known threat actor group |

### Relationships

| Type | Direction | Purpose |
|------|-----------|---------|
| `SENT_TO` | Wallet → Wallet | Direct transfer |
| `RECEIVED_FROM` | Wallet → Wallet | Incoming transfer |
| `CONTROLS` | Entity → Wallet | Entity owns wallet |
| `MEMBER_OF` | Wallet → Cluster | Wallet belongs to cluster |
| `INTERACTS_WITH` | Wallet → Token | Wallet traded/used token |
| `DEPLOYED_BY` | Token → Deployer | Deployer created token |
| `FUNDED_BY` | Wallet → Wallet | Funding source chain |
| `ASSOCIATED_WITH` | Wallet → ThreatActor | Suspected connection |

Neo4j is primarily used for:
- Wallet clustering (linking addresses controlled by same entity)
- Fund flow tracing (multi-hop transaction chains)
- Threat actor mapping (linking wallets to known bad actors)
- Entity resolution (mapping addresses to real-world identities)

Schema is managed **programmatically** through `app/domains/wallet/clustering.py` and related modules.

---

## 5. Qdrant (Vector DB)

**Container:** `rmi-qdrant` — `qdrant/qdrant:v1.18.3`  
**Port:** 6333 (gRPC + HTTP)  
**Connection string:** `http://rmi-qdrant:6333`

### Collections

| Collection | Vector Size | Distance | Purpose |
|-----------|-------------|----------|---------|
| `rag_docs` | 768 | Cosine | RAG document embeddings |
| `code_patterns` | 768 | Cosine | Code pattern similarity search |
| `token_embeddings` | 768 | Cosine | Token metadata embeddings |

### Usage

- **RAG semantic search**: Embed user queries with BGE-M3, search `rag_docs` for relevant context
- **Code pattern matching**: Find similar scam patterns across known contracts
- **Token similarity**: Match tokens with similar metadata profiles

---

## 6. DragonflyDB (Cache)

**Container:** `rmi-redis` — `dragonflydb/dragonfly:v1.39.0`  
**Port:** 6379  
**Connection string:** `redis://rmi-redis:6379/0`

### Cache Layers

| Layer | Location | Latency | TTL Range |
|-------|----------|---------|-----------|
| **L1** | Python dict (in-process) | < 0.1ms | Seconds |
| **L2** | DragonflyDB | < 1ms | Seconds to hours |

### Key Patterns

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `price:{chain}:{address}` | 8s | Token price cache |
| `token:{chain}:{address}` | 60s | Token metadata |
| `wallet:{address}:balance` | 120s | Wallet balance |
| `scan:{chain}:{address}` | 300s | Scan result cache |
| `abi:{chain}:{address}` | 3600s | Contract ABI |
| `session:{user_id}` | 86400s | User session |
| `ratelimit:{ip}` | sliding | Rate limit bucket |
| `label:{address}` | 3600s | Wallet labels |

### Pub/Sub Channels

| Channel | Purpose |
|---------|---------|
| `alerts:*` | Real-time alert broadcasts |
| `scanner:results` | Scanner completion notifications |
| `system:degraded` | Store degradation events |

---

## 7. MinIO (Object Storage)

**Container:** `rmi-minio` — `minio/minio:RELEASE.2025-09-07`  
**Port:** 9092  
**Connection string:** `http://rmi-minio:9000`

### Buckets

| Bucket | Purpose |
|--------|---------|
| `rmi-backups` | Database backups (Restic/R2 sync) |
| `rmi-rag-docs` | RAG document source files |
| `rmi-uploads` | User-uploaded files |
| `rmi-static` | Static assets |

---

## 8. Migration Strategy

### PostgreSQL (Alembic)

```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply pending migrations
alembic upgrade head

# Roll back one step
alembic downgrade -1

# Roll back to specific revision
alembic downgrade <revision_id>

# View history
alembic history

# Check current version
alembic current
```

Migration files in `alembic/versions/`:
- `0001_initial.py` — Initial schema
- `75c982fccbb8_add_missing_tables_2026_07_20.py` — Additional tables

**Config:** `alembic.ini` with `sqlalchemy.url` overridden at runtime by `ALEMBIC_DATABASE_URL` env var.

### ClickHouse

Schema is managed via `infra/clickhouse_schema.sql`. Apply idempotently:

```bash
docker cp infra/clickhouse_schema.sql rmi-clickhouse:/tmp/
docker exec rmi-clickhouse clickhouse-client --queries-file /tmp/clickhouse_schema.sql
```

### Neo4j / Qdrant

Schema is managed **programmatically** through application code at startup or on first use. No migration tooling.

---

## 9. Backup Strategy

### Schedule

| Backup | Frequency | Target | Retention |
|--------|-----------|--------|-----------|
| PostgreSQL (pg_dump) | Daily 02:00 | R2 (MinIO) | 30 days |
| PostgreSQL (WAL) | Continuous | Local archive | 7 days |
| ClickHouse | Daily 03:00 | R2 | 30 days |
| Neo4j | Daily 04:00 | R2 | 30 days |
| DragonflyDB (snapshot) | Daily 05:00 | R2 | 7 days |
| MinIO (Restic) | Daily 06:00 | Backblaze B2 | 90 days |
| Full system | Monthly | Hugging Face | Permanent |

### Pipeline

```
Local Disk → MinIO/R2 → Backblaze B2 → Hugging Face (monthly permanent)
```

### Scripts

| Script | Location | What It Does |
|--------|----------|-------------|
| `backup-postgres.sh` | `/root/scripts/` | pg_dump → R2 |
| `backup-monitor.sh` | `/root/scripts/` | Hourly health check, n8n alert on failure |
| `backup-restore-test.sh` | `/root/scripts/` | Weekly restore validation |
| `backup-quick-check.sh` | `/root/scripts/` | Daily backup existence check |

### Monitoring

- 20 active cron jobs
- Hourly monitoring via `backup-monitor.sh`
- n8n webhook alerts on failures
- All logs at `/var/log/` and `/backups/`
- Weekly restore test: 5/5 passing

---

## 10. Connection Pooling

| Database | Pooler | Pool Config | Max Connections |
|----------|--------|-------------|----------------|
| PostgreSQL | PgBouncer | Transaction pooling, port 6432 | 200 |
| PostgreSQL (app) | SQLAlchemy async | `pool_size=10`, `max_overflow=20` | 30 (per instance) |
| DragonflyDB | redis.asyncio | Default pool | N/A (single pool) |
| ClickHouse | httpx | Connection pool | 10 (per instance) |
| Neo4j | neo4j driver | Default | 50 sessions |
| Qdrant | qdrant-client | gRPC pool | 10 (per instance) |

---

## 11. Data Flow Patterns

### Read Path

```
Request → L1 Cache (dict) → L2 Cache (DragonflyDB) → Primary DB (Postgres/ClickHouse/Neo4j)
```

Each layer checks the next on cache miss, populates upstream on hit.

### Write Path

```
Client → API → Write to Primary DB → Invalidate/Update Cache → Log to ClickHouse
```

### Cache Invalidation

- **TTL-based** (not event-driven) — Accepts eventual consistency
- **Short TTLs** for price data (8s)
- **Longer TTLs** for static data (contract ABI: 1hr)
- **Manual purge** via `/api/v1/x402/cache/clear` (authenticated)
