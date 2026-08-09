# Database Store Consolidation Plan

> **Date:** 2026-07-27
> **Author:** Data Pipeline Engineer
> **Status:** Assessment Complete — Recommendations Ready
> **Audit Ref:** PART A (usage metrics), PART B (recommendations), PART C (plan)

---

## Executive Summary

**Premise:** The original audit counted 7 stores and assumed ClickHouse + Neo4j were "aspirational stubs."

**Finding:** This premise was **incorrect**. After thorough codebase analysis:

| Claim | Reality |
|-------|---------|
| ClickHouse is a stub | **FALSE** — 50 files, 6 tables, 771-line storage layer, real queries daily |
| Neo4j is a stub | **FALSE** — 12 files, real Cypher queries, wallet entity resolution, running in prod |
| DuckDB is complex infra | **FALSE** — In-process (no container), 271-line embeddable engine |
| 7 stores are all always-on | **MISLEADING** — 3 always-on (Redis, Postgres, ClickHouse), 4 profile-gated |

**Revised target:** Keep all 7 logical store types but:
1. Document the **actual deployment profile** (3 always-on + 4 optional)
2. Clarify **store roles** to eliminate perceived overlap
3. Remove **Redpanda** entirely (event streaming aspirational, never used)
4. Evaluate **MinIO → local filesystem** simplification

---

## PART A: Store Usage Assessment

### 1. PostgreSQL — Primary OLTP Store

| Metric | Value |
|--------|-------|
| Files importing Postgres | **106** |
| SQLAlchemy model files | **10** (`app/models/*.py`) |
| Table models defined | **21** (User, Token, Alert, ScanResult, Payment, Label, analytics tables, live battle tables) |
| Alembic migrations | **7** |
| Primary responsibility | Users, tokens, alerts, payments, scan results, analytics, live battles |
| Docker profile | **Default** (always-on) |
| Dependencies | `depends_on` in docker-compose: `condition: service_healthy` |
| Status | **KEEP** — Non-negotiable, most important store |

### 2. DragonflyDB (Redis) — Cache + Pub/Sub + Rate Limiting

| Metric | Value |
|--------|-------|
| Files importing Redis | **240** |
| Primary responsibility | Hot cache (profiles TTL 5m, labels TTL 1h), rate limiting, pub/sub, session store, alert state, cron locks |
| Docker image | `dragonflydb/dragonfly` (Redis-compatible) |
| Docker profile | **Default** (always-on) |
| Degradation | InProcessLRU fallback (2048 entries, 5min TTL) |
| Status | **KEEP** — Non-negotiable, largest surface area |

### 3. ClickHouse — Analytical + Wallet Memory Store

| Metric | Value |
|--------|-------|
| Files importing ClickHouse | **50** |
| Tables defined | **6** in `wallet_memory/storage.py`: `token_transfers`, `wallet_entities`, `scam_addresses`, `wallet_profiles`, `deployer_history`, `bridge_mappings` |
| Code complexity | 771 lines in `storage.py`, 411 lines in `analytics_engine.py` |
| Primary responsibility | Wallet profiles, entity resolution, deployer history, bridge mappings, scam database, real-time metrics, trend analysis |
| Docker profile | **Default** (always-on) — `depends_on: condition: service_started` |
| Degradation chain | ClickHouse (3s) → DuckDB (2s) → empty |
| **Status** | **KEEP** — This is NOT a stub. It's the primary analytical persistence layer. |

> **Why ClickHouse and not Postgres for this data?**
> - Wallet transfers are append-only time-series (ideal for ClickHouse MergeTree)
> - Entity resolution requires fast aggregations across millions of addresses
> - Materialized views for real-time wallet profiles
> - TTL-based data retention (90d) without vacuum overhead

### 4. Neo4j — Graph Entity Resolution Store

| Metric | Value |
|--------|-------|
| Files importing Neo4j | **12** |
| Cypher queries | Real: `MATCH (w:Wallet {...}) RETURN w`, `MERGE (w:Wallet {...})`, `MATCH (d:Deployer) WHERE d.rug_count...`, `MATCH (start:Wallet)-[:SAME_AS|FUNDED_BY_SAME|CLONE_OF|BEHAVIORAL_MATCH*1..3]-(other:Wallet)` |
| CatalogService recipes using Neo4j | Recipe 1 (deployer history), Recipe 3 (risk score), Recipe 5 (entity resolution) |
| Models | `Entity`, `EntityLabel`, `Wallet`, `Deployer` (primary Neo4j store) |
| Docker profile | **`profiles: [full]`** — NOT in default startup |
| Actual production status | **Running** (container started 2h ago, healthy) |
| Degradation chain | Neo4j (2s) → Postgres (2s) → empty |
| **Status** | **KEEP** — Actively used for graph traversal. Not removable without feature loss. |

> **Why not replace Neo4j with ClickHouse?**
> - Cross-chain entity resolution requires variable-length path traversal (`*1..3`) which is a graph-native operation
> - The `entity_resolver.py` already works with ClickHouse for flat storage but uses deterministic hash-based IDs
> - Neo4j enables ad-hoc graph queries (shared funders, cluster detection, label propagation)
> - The `full` profile gate means it's optional — backend works without it via degradation

### 5. Qdrant — Vector Embeddings for RAG

| Metric | Value |
|--------|-------|
| Files importing Qdrant | **14** |
| Primary responsibility | RAG embeddings (scam intel, deployer history, wallet labels, contract audit, +9 more collections), news vector search, token similarity |
| Docker profile | **`profiles: [full]`** |
| Actual production status | **Running** (22h, healthy) |
| **Status** | **KEEP** — Essential for RAG. Already profile-gated. |

### 6. DuckDB — In-Process Analytical Engine

| Metric | Value |
|--------|-------|
| Files importing DuckDB | **11** |
| Code complexity | `core/duckdb_analytics.py`: 271 lines (embeddable engine), `databus/duckdb_analytics.py`: 673 lines (label analytics) |
| Docker container | **NONE** — In-process library, no infrastructure |
| Primary responsibility | ClickHouse fallback, Parquet querying, CSV label imports (Real-CATS 153K + MBAL 10M addresses), cross-source joins |
| Memory profile | In-memory by default, optional file-backed mode |
| **Status** | **KEEP** — Already zero-infrastructure. Used as graceful degradation fallback for ClickHouse and Postgres. |

> DuckDB is **not infrastructure** — it's a library loaded within the Python process. It does not count as a "store" in the operational sense (no container, no health check, no volume, no port).

### 7. MinIO — Object Storage

| Metric | Value |
|--------|-------|
| Files importing MinIO/S3 | **10** |
| Primary responsibility | News raw HTML (CatalogService), report markdown (ScanReport), RAG source docs |
| Docker profile | **`profiles: [full]`** |
| Data stored | Reports, news raw HTML, RAG source documents |
| **Status** | **KEEP LIGHTLY** — Could potentially simplify to filesystem, but low priority |

### 8. Redpanda — Event Streaming (Not in Original 7)

| Metric | Value |
|--------|-------|
| Files importing Redpanda | **0** (no grep matches) |
| Docker profile | **`profiles: [full]`** |
| Actual usage | **None** — Aspirational/never implemented |
| **Status** | **FREEZE/RETIRE** — Remove from docker-compose, document as removed |

> Redpanda was intended for event streaming (scam alerts, real-time updates) but was never wired up. The architecture uses Redis pub/sub + WebSocket instead.

---

## PART B: Store Consolidation Analysis

### Store Role Matrix

| Store | Type | Always-On? | Critical Path? | Degradation | Migration Needed? |
|-------|------|------------|----------------|-------------|-------------------|
| **PostgreSQL** | OLTP | ✅ Default | ✅ Yes | → DuckDB → empty | ❌ No |
| **Redis** | Cache | ✅ Default | ✅ Yes | → InProcessLRU | ❌ No |
| **ClickHouse** | Analytics | ✅ Default | ⚠️ Soft | → DuckDB → empty | ❌ No |
| **Neo4j** | Graph | ❌ Full profile | ⚠️ Soft | → Postgres → empty | ❌ No |
| **Qdrant** | Vector | ❌ Full profile | ⚠️ Soft | → empty results | ❌ No |
| **MinIO** | Object | ❌ Full profile | ⚠️ Soft | → stale cache → None | ⚠️ Evaluate |
| **DuckDB** | Embedded | N/A (in-process) | ⚠️ Fallback | N/A | ❌ No |
| **Redpanda** | Stream | ❌ Full profile | Never used | N/A | ✅ Remove |

### Identified Optimization Opportunities

1. **Redpanda → Remove entirely** (zero usage, alternatives exist in Redis pub/sub)
2. **MinIO → Evaluate local filesystem for non-backend deployments** (low data volume, file-based alternative)
3. **DuckDB ↔ ClickHouse overlap → DOCUMENT roles clearly** (not duplicate, complementary)
4. **Neo4j profile → Keep `full` profile, but add health-check documentation** (already correctly gated)
5. **ARCHITECTURE.md → Update to reflect actual usage** (ClickHouse as primary analytical store, not stub)

### What NOT To Do

| Bad Idea | Why |
|----------|-----|
| "Freeze ClickHouse" | Would break wallet memory, entity resolution, analytics, labels — 50 files depend on it |
| "Freeze Neo4j" | Would break entity resolution graph traversal, deployer reputation — 12 files, real Cypher |
| "Migrate ClickHouse → Postgres" | Time-series data in Postgres would bloat the OLTP store; ClickHouse is 10x more efficient for this workload |
| "Migrate DuckDB → ClickHouse" | DuckDB is in-process (no network), used for Parquet/CSV queries that ClickHouse doesn't support natively |
| "Remove DuckDB" | Would remove the degradation fallback for both ClickHouse and Postgres |

---

## PART C: Consolidation Plan

### Phase 1: Documentation & Freeze Unused (0-5 days)

| Step | Action | Owner | Effort |
|------|--------|-------|--------|
| 1.1 | Remove **Redpanda** service from `docker-compose.yml` | Infra | 1h |
| 1.2 | Remove Redpanda volume from docker-compose | Infra | 15min |
| 1.3 | Update `ARCHITECTURE.md` §Databases — correct ClickHouse/Neo4j descriptions | Docs | 2h |
| 1.4 | Update `catalog/models.py` docstring to match reality | Backend | 30min |
| 1.5 | Archive any Redpanda config (if exists) | Infra | 15min |
| 1.6 | **Risk**: Low — Redpanda has zero code dependencies, removing it changes nothing |

### Phase 2: Clarify Store Roles (5-10 days)

| Step | Action | Owner | Effort |
|------|--------|-------|--------|
| 2.1 | Add **store-role header comments** to every store connection file | Backend | 4h |
| 2.2 | Add **X-Store-Roles** health endpoint showing which stores are active | Backend | 2h |
| 2.3 | Document in `DATABASE.md` the clear boundary between ClickHouse (time-series) and DuckDB (Parquet/labels) | Docs | 2h |
| 2.4 | Add profile documentation to `DEPLOYMENT.md` explaining `--profile full` vs default | Docs | 1h |
| 2.5 | **Risk**: Low — Documentation only, no code behavior changes |

### Phase 3: Evaluate & Simplify MinIO (10-20 days)

| Step | Action | Owner | Effort |
|------|--------|-------|--------|
| 3.1 | Quantify MinIO data volume (reports + news HTML) | Infra | 1h |
| 3.2 | If < 500MB: implement local filesystem alternative for `reports/` | Backend | 4h |
| 3.3 | Add `CatalogService.report_store` abstraction (MinIO or filesystem) | Backend | 6h |
| 3.4 | Update `ScanReport.markdown_url` to support file:// references | Backend | 2h |
| 3.5 | **Risk**: Medium — Requires refactoring `app/domains/reports/generator.py` and `app/catalog/service.py` |

### Phase 4: Monitoring & Health (20-30 days)

| Step | Action | Owner | Effort |
|------|--------|-------|--------|
| 4.1 | Add `clickhouse_health` to `/health` endpoint (check `SELECT 1`) | Backend | 1h |
| 4.2 | Verify all degradation chains fire correctly in integration tests | Backend | 4h |
| 4.3 | Add store latency percentiles to Prometheus metrics | Backend | 3h |
| 4.4 | Create Grafana dashboard: "Store Health" with all 6 stores | Infra | 4h |
| 4.5 | Add alert: any store down > 5 minutes → n8n webhook | Infra | 2h |

---

## PART D: Risk Assessment

### What Breaks If We Stop Each Store

| Store Stop | Impact | Recovery |
|-----------|--------|----------|
| **Redis** | Authentication fails, cache misses, rate limits disabled, pub/sub breaks | Auto-recovery when Redis returns. InProcessLRU serves stale cache for critical paths (5min TTL). Login failures until restart. |
| **Postgres** | **Everything breaks** — tokens, alerts, users, payments, analytics | No graceful fallback for core data. DB must be restored from backup. |
| **ClickHouse** | Wallet profiles return empty, entity resolution degrades, analytics show no data | Degradation → DuckDB (for what it can serve) → empty. Wallet lookups become Redis-only (depends on what was cached). |
| **Neo4j** | Entity resolution returns "neo4j unavailable", deployer reputation uses Postgres fallback | Gracefully degrades to "no graph data" — cross-chain entity linking disabled but wallet risk scores still work. |
| **Qdrant** | RAG search returns empty, token similarity disabled | Gracefully degrades to empty results. News ingest continues but without embeddings. |
| **MinIO** | Report attachment downloads 404, news HTML unavailable | Gracefully degrades — metadata still in Postgres, just file content not accessible. |
| **DuckDB** | ClickHouse fallback disappears, label CSV import fails | DuckDB is in-process — if it's broken, the ClickHouse degradation chain skips to empty. Postgres fallback still available. |

### Current Degradation Coverage ✅

The codebase already has excellent graceful degradation:

- **DegradationRegistry** — Tracks health of all 5 container stores
- **DegradationChain** — Tries stores in order with configurable timeouts
- **X-Degraded-Stores header** — Informs clients which stores are degraded
- **InProcessLRU** — Serves stale cached data when Redis is down
- **Store-specific fallbacks**: ClickHouse→DuckDB, Neo4j→Postgres, Redis→InProcessLRU, MinIO→stale cache

---

## PART E: Target Architecture

### Current: 7 Store Types

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  CatalogService / WalletMemory / AnalyticsEngine    │
├──────────┬──────────┬──────────┬──────┬──────┬──────┤
│ Postgres │ ClickHouse│  Redis   │ Neo4j│Qdrant│MinIO │
│  (OLTP)   │ (Analytics)│ (Cache)  │(Graph)│(Vector│(Obj) │
└──────────┴──────────┴──────────┴──────┴──────┴──────┘
                         │
                    ┌────┴────┐
                    │ DuckDB  │ ← In-process fallback
                    └─────────┘
```

### Target: 6 Store Types (+ DuckDB in-process)

```
User Request
    │
    ▼
┌────────────────────────────────────────────────────────┐
│  CatalogService / WalletMemory / AnalyticsEngine       │
├──────────┬──────────┬──────────┬──────┬──────┬─────────┤
│ Postgres │ ClickHouse│  Redis   │ Neo4j│Qdrant│ MinIO/  │
│  (OLTP)   │ (Analytics)│ (Cache)  │(Graph)│(Vector│Filesys  │
└──────────┴──────────┴──────────┴──────┴──────┴─────────┘
                         │
                    ┌────┴────┐
                    │ DuckDB  │ ← In-process fallback
                    └─────────┘

REMOVED:
  ✗ Redpanda (zero usage, removed)
```

### Key Changes from Current

1. **Remove Redpanda** — Zero code usage, zero data, zero reason to keep
2. **Document ClickHouse** as the primary analytical persistence layer (not a stub)
3. **Document Neo4j** as profile-gated graph store with graceful degradation
4. **Document DuckDB** as in-process analytics engine (not infrastructure)
5. **Phase 3: Evaluate MinIO → local filesystem** for simplified deployments

---

## PART F: Usage Metrics Summary

| Store | Files Importing | Tables/Collections | Docker Container | Default Startup | Profile | Degradation | Verdict |
|-------|----------------|-------------------|-----------------|----------------|---------|-------------|---------|
| PostgreSQL | 106 | 21 | ✅ rmi-postgres | ✅ Default | — | → DuckDB | **KEEP** |
| Redis/DragonflyDB | 240 | N/A (key-value) | ✅ rmi-redis | ✅ Default | — | → InProcessLRU | **KEEP** |
| ClickHouse | 50 | 6 (wallet_memory) | ✅ rmi-clickhouse | ✅ Default | — | → DuckDB → empty | **KEEP** |
| Neo4j | 12 | 4 label types | ✅ rmi-neo4j | ❌ Profile | `full` | → Postgres → empty | **KEEP** |
| Qdrant | 14 | 13 collections | ✅ rmi-qdrant | ❌ Profile | `full` | → empty results | **KEEP** |
| MinIO | 10 | N/A (blobs) | ✅ rmi-minio | ❌ Profile | `full` | → stale cache | **EVAL** |
| DuckDB | 11 | 4 (label tables) | ❌ In-process | ✅ N/A | — | N/A (is the fallback) | **KEEP** |
| ~~Redpanda~~ | 0 | 0 | ✅ rmi-redpanda | ❌ Profile | `full` | N/A | **REMOVE** |

---

## Appendix: File-by-File Store Dependencies

### Files Using Multiple Stores (Integration Hotspots)

| File | Stores Used | Role |
|------|------------|------|
| `app/catalog/service.py` | Redis, Postgres, Neo4j, Qdrant, MinIO | Central catalog facade |
| `app/wallet_memory/storage.py` | ClickHouse, Redis | Wallet profile persistence |
| `app/wallet_memory/entity_resolver.py` | ClickHouse, Redis (+ Neo4j planned) | Cross-chain entity resolution |
| `app/core/degradation.py` | ALL stores | Degradation registry + chains |
| `app/mcp/server.py` | ALL stores | MCP tool implementations |
| `app/core/health_route.py` | ALL stores | Health endpoint |
| `app/core/duckdb_analytics.py` | DuckDB (+ Postgres via ATTACH) | Embeddable analytics engine |
| `app/databus/duckdb_analytics.py` | DuckDB | Label dataset analytics |
| `app/analytics_engine.py` | Redis, ClickHouse | Real-time metrics |
| `app/domains/labels/sources/clickhouse_labels.py` | ClickHouse, DuckDB | Label source resolution |

### Docker Compose Dependencies

```
rmi-backend depends_on:
  ✅ rmi-redis:     condition: service_healthy
  ✅ rmi-postgres:  condition: service_healthy
  ✅ rmi-clickhouse: condition: service_started
  ❌ rmi-neo4j:     NOT in depends_on (profile-gated)
  ❌ rmi-qdrant:    NOT in depends_on (profile-gated)
  ❌ rmi-minio:     NOT in depends_on (profile-gated)
```

---

*Document generated by automated codebase audit. All usage metrics from grep searches of `/srv/work/repos/rmi-backend/app/`.*
