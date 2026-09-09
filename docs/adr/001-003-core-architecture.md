# ═══════════════════════════════════════════════════════════════
# ADR-001: DataBus as the Single Data Layer
# ═══════════════════════════════════════════════════════════════
# Date: 2026-06-15
# Status: Accepted

## Context
RMI needs to serve data from 112 chains across 135 providers to multiple
products (RugCharts, RugMaps, News, SENTINEL, Telegram bots). Each product
has different access patterns, rate limits, and caching requirements.

## Decision
All data access goes through a single DataBus layer (`app/databus/`).
Products never call external APIs directly. DataBus handles:
- Provider selection and fallback (free → freemium → paid)
- 3-layer caching (L1 memory → L2 Redis → L3 R2 cold storage)
- Request deduplication (same query within 5s shares one API call)
- Circuit breakers (3 failures → 30s open)
- Credit-aware provider routing (prioritize free when quota low)

## Alternatives Considered
1. **Each product calls APIs directly** — rejected: duplicate caching logic,
   no credit pooling, harder to track API costs
2. **GraphQL federation** — rejected: overengineered for our scale, adds
   latency, 78 chains would be 78 subgraphs

## Consequences
- All new features must go through databus.fetch()
- Provider chains need 4-file sync when adding new chains
- Single bottleneck risk mitigated by caching and dedup

# ═══════════════════════════════════════════════════════════════
# ADR-002: Monolith over Microservices
# ═══════════════════════════════════════════════════════════════
# Date: 2026-06-15
# Status: Accepted

## Context
RMI backend started as a FastAPI monolith and has grown to 243K lines across
495 files. The question: should we split into microservices?

## Decision
Stay monolithic. Extract into well-organized modules (`app/routers/`,
`app/databus/`, `app/core/`) but keep a single deployable. Reasons:
- Team of 1 (solo dev). Microservices would multiply operational burden
- Shared DataBus layer means every service would depend on it anyway
- FastAPI + async already handles concurrency well
- Docker deployment is a single container — simpler CI/CD

## When to Revisit
- When the team grows to 3+ developers working on isolated domains
- When DataBus becomes a throughput bottleneck (unlikely with current caching)
- When we need independent scaling (scanner vs API vs bot)

## Consequences
- main.py needs continuous extraction (see ADR-003)
- All routers share the same process/memory space
- Deployment restarts affect all features simultaneously

# ═══════════════════════════════════════════════════════════════
# ADR-003: Redis for Hot Cache, Postgres for Cold Storage
# ═══════════════════════════════════════════════════════════════
# Date: 2026-06-15
# Status: Accepted

## Context
DataBus needs fast lookups for wallet labels (~140k curated entries), token prices,
and scam patterns. Write-heavy for webhooks (Arkham, Helius, Moralis).

## Decision
- **Redis** for L1/L2 caching: sub-ms lookups, TTL-based expiry, sorted sets
  for alerts and OHLCV candles. Protocol=2 for Redis 7.2 compatibility.
- **Postgres** for persistent data: wallet labels source of truth, scan
  results, user auth. Redis is loaded from Postgres on startup.
- **R2/S3** for cold storage: RAG document backups, model weights.

## Alternatives Considered
1. **Postgres-only** — rejected: too slow for real-time lookups, would need
   complex caching layer anyway
2. **Redis-only** — rejected: no persistence guarantees, data loss on restart
3. **Memcached** — rejected: simpler than Redis but lacks sorted sets needed
   for OHLCV and alert pipelines

## Consequences
- Dual-write pattern: every write goes to both Postgres and Redis
- Redis protocol=2 required for Python 3.11 + Redis 7.2 (RESP3 HELLO issue)
- Cache invalidation: TTL-based, not event-based. Accepts eventual consistency
