# Cross-Chain Entity Tracking — Architecture & Design

> **Status:** Canonical · Updated 2026-07-30  
> **Owner:** RugMunch Engineering  
> **Covers:** W2-P4: Cross-chain entity tracking design & implementation

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Overview](#2-architecture-overview)
3. [Component Breakdown](#3-component-breakdown)
4. [Neo4j Schema](#4-neo4j-schema)
5. [Data Flow](#5-data-flow)
6. [Matching Strategies](#6-matching-strategies)
7. [API Reference](#7-api-reference)
8. [Implementation Plan](#8-implementation-plan)
9. [Chain-Indexer Integration](#9-chain-indexer-integration)
10. [Testing Strategy](#10-testing-strategy)

---

## 1. Problem Statement

The platform tracks 169K+ entities in Neo4j and has deployer events across 6 chains
(ethereum, bsc, arbitrum, base, polygon, tron, solana) via chain-indexer, but:

- **No cross-chain edges exist in Neo4j** — wallets on different chains are disconnected
- **Entity resolution is incomplete** — entity_resolver.py only uses EVM address portability
  (same address across EVM chains) but misses deployer-based matching
- **Bridge pattern detection is passive** — bridge_mappings exist but must be manually fed
- **No batch pipeline** — can't periodically discover new cross-chain links

### Existing State

| Component | What Exists | Gap |
|-----------|------------|-----|
| chain-indexer | `deployer_events` table in ClickHouse (6 chains) | No consumer of multi-chain deployer data |
| Neo4j | Wallet + Label + Entity nodes | No SAME_AS, BRIDGED_TO, BEHAVIORAL_MATCH edges |
| wallet_memory | bridge_mappings + wallet_entities in ClickHouse | No automatic deployer-based entity links |
| entity_resolver.py | EVM address portability + bridge lookups | No deployer matching integration |
| catalog/service.py | Cypher query for SAME_AS resolution | Schema defined but edges never created |
| cross_chain.py | Behavioral fingerprint stub | Not connected to real data |

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     DATA SOURCES                                  │
│                                                                   │
│  chain-indexer (TypeScript)                                       │
│  ┌─────────────────────────────────────────┐                     │
│  │ deployers.ts → ClickHouse:deployer_events│                     │
│  │   Chains: ethereum, bsc, arbitrum, base, │                     │
│  │          polygon, tron, solana            │                     │
│  └─────────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                CROSS-CHAIN TRACKING PACKAGE                        │
│  app/cross_chain_tracking/                                        │
│                                                                   │
│  clickhouse_reader.py  ────  deployer_matcher.py  ────  models.py│
│       ▲                          │         │                     │
│       │                          ▼         ▼                     │
│       │                   neo4j_writer.py  entity_tracker.py      │
│       │                          │                               │
│       └──────────────────────────┘                               │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     OUTPUT STORES                                 │
│                                                                   │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐    │
│  │  Neo4j       │    │  ClickHouse  │    │  Redis (cache)   │    │
│  │  SAME_AS,    │    │  wallet_     │    │  entity_cache    │    │
│  │  BRIDGED_TO, │◄───│  entities    │◄───│  bridge_links    │    │
│  │  DEPLOYED_ON │    │  bridge_     │    │  TTL 10min       │    │
│  │  edges       │    │  mappings    │    │                  │    │
│  └──────────────┘    └──────────────┘    └──────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      CONSUMERS                                    │
│                                                                   │
│  entity_resolver.py  ←  wallet_memory/router.py  ←  API layer    │
│  catalog/service.py  ←  MCP tools                ←  Agent layer  │
│  cross_chain.py      ←  investigation_narratives.py               │
│  sentinel scanner    ←  deployer reputation scoring               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Breakdown

### 3.1 `app/cross_chain_tracking/` Package (NEW)

| File | Responsibility | Key Functions |
|------|---------------|---------------|
| `__init__.py` | Package exports | `CrossChainEntityTracker`, `DeployerMatcher`, models |
| `models.py` | Pydantic v2 data models | `DeployerEvent`, `MultiChainDeployer`, `CrossChainProfile`, `CrossChainMatch`, `CrossChainSummary` |
| `clickhouse_reader.py` | Read chain-indexer ClickHouse | `fetch_multi_chain_deployers()`, `fetch_deployments_for_deployer()`, `fetch_deployment_counts_by_chain()` |
| `neo4j_writer.py` | Create Neo4j nodes + edges | `upsert_wallet()`, `upsert_entity()`, `create_same_as_edge()`, `create_bridged_to_edge()`, `ensure_schema()` |
| `deployer_matcher.py` | Same-deployer detection logic | `find_multi_chain_deployers()`, `resolve_deployer()`, `persist_multi_chain_deployer()`, `batch_persist()` |
| `entity_tracker.py` | Orchestrator | `resolve()`, `run_batch_pipeline()` |
| `cli.py` | CLI entry point | `python -m app.cross_chain_tracking.cli run/stats/resolve` |

### 3.2 Updated Modules

| Module | Changes |
|--------|---------|
| `app/cross_chain.py` | Deployer matching added to `resolve_cross_chain_identity()`; new `get_cross_chain_profile()` and `get_multi_chain_deployers()` convenience wrappers |
| `app/wallet_memory/entity_resolver.py` | Method 2 (same-deployer) added to `resolve()` between EVM portability and bridge mappings |
| `app/routers/v1/cross_chain.py` | Wired to real implementation: `GET /{address}`, `POST /deployers/discover`, `POST /persist` |

---

## 4. Neo4j Schema

### Node Types

```cypher
// Entity — a logical operator across chains (already exists)
(:Entity {
  entity_id: "ent_abc123def456",  // UUID
  label: "Multi-chain deployer: 0x1234...",
  aliases: ["mek_123", "0xabcd..."],
  tags: ["multi_chain_deployer", "deployer:ethereum", "deployer:bsc"],
  first_seen: datetime("2025-06-01T00:00:00Z"),
  last_seen: datetime("2026-07-30T00:00:00Z")
})

// Wallet — exists with {wallet_id, address, chain}
(:Wallet {
  wallet_id: "ethereum:0xabc...",   // "chain:address"
  address: "0xabc...",
  chain: "ethereum",
  is_deployer: true,
  chains_active: ["ethereum", "bsc", "polygon"],
  first_seen: datetime(),
  last_seen: datetime()
})
```

### Edge Types

```cypher
// SAME_AS — same entity, different chains (THE key cross-chain edge)
(:Wallet {wallet_id: "ethereum:0xabc..."})-[:SAME_AS {
  confidence: 0.95,
  method: "same_deployer",
  created_at: datetime()
}]-(:Wallet {wallet_id: "bsc:0xabc..."})

// BELONGS_TO — wallet → entity membership (already exists)
(:Wallet)-[:BELONGS_TO {confidence: 1.0}]->(:Entity)

// BRIDGED_TO — linked via bridge transaction
(:Wallet)-[:BRIDGED_TO {
  bridge_name: "stargate",
  tx_hash: "0xdef...",
  confidence: 0.8,
  created_at: datetime()
}]-(:Wallet)

// BEHAVIORAL_MATCH — matched via fingerprint similarity
(:Wallet)-[:BEHAVIORAL_MATCH {
  confidence: 0.72,
  evidence: "Temporal pattern match: preferred hours overlap 75%",
  created_at: datetime()
}]-(:Wallet)
```

### Indexes (created by `ensure_schema()`)

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (w:Wallet) REQUIRE w.wallet_id IS UNIQUE;
CREATE INDEX IF NOT EXISTS FOR (w:Wallet) ON (w.address, w.chain);
CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.label);
```

---

## 5. Data Flow

### 5.1 Batch Pipeline (Daily)

```
1. chain-indexer runs continuously, writing deployer_events → ClickHouse
2. cron/scheduler calls `python -m app.cross_chain_tracking.cli run`
3. clickhouse_reader.fetch_multi_chain_deployers(2+) → ClickHouse GROUP BY
4. For each multi-chain deployer:
   a. deployer_matcher determines confidence level
   b. neo4j_writer.upsert_entity() → creates Entity node
   c. neo4j_writer.upsert_wallet() → creates/updates Wallet per chain
   d. neo4j_writer.create_same_as_edge() → all-pairs SAME_AS edges
   e. wallet_memory.storage.save_entity_link() → ClickHouse wallet_entities
5. Summary returned with counts and duration
```

### 5.2 Real-Time Resolution

```
API: GET /api/v1/cross-chain/{address}?chain=ethereum
1. entity_resolver.resolve() called
2. Method 1: EVM address portability → same address on 8 EVM chains
3. Method 2 (NEW): DeployerMatcher.resolve_deployer() → chain-indexer query
4. Method 3: Bridge link storage lookup
5. Method 4: Existing entity membership (Neo4j or ClickHouse)
6. Results deduplicated, confidence-scored, cached in Redis
```

---

## 6. Matching Strategies

| Strategy | Confidence | Data Source | Method |
|----------|-----------|-------------|--------|
| **Same EVM deployer** | 0.95 | chain-indexer deployer_events | Same address deploys on 2+ EVM chains |
| **EVM address portability** | 0.90 | chain registry | Same address on any EVM chain |
| **Mixed-chain deployer** | 0.80 | chain-indexer deployer_events | Same address deploys on EVM + non-EVM |
| **Bridge mapping** | 0.80 | wallet_memory bridge_mappings | Known bridge tx linking addresses |
| **Solana deployer** | 0.70 | chain-indexer deployer_events | Deployer on Solana with EVM cross-signals |
| **Behavioral fingerprint** | 0.70 | Transaction history | Similar patterns (timing, frequency, DEX pref) |
| **Fund flow trace** | 0.60 | Transfer data | Funds move through bridges between addresses |
| **Entity membership** | 0.50 | Neo4j/ClickHouse | Pre-existing entity group |

---

## 7. API Reference

### `GET /api/v1/cross-chain/{address}`

Resolve a wallet to its cross-chain profile.

**Query params:** `chain` (default: "ethereum")

**Response:**
```json
{
  "entity_id": "ent_abc123def456",
  "primary_address": "0xabc...",
  "primary_chain": "ethereum",
  "addresses": [
    {"address": "0xabc...", "chain": "bsc", "confidence": 0.95, "method": "same_deployer", "evidence": "Deployed 12 tokens on bsc"},
    {"address": "0xabc...", "chain": "polygon", "confidence": 0.90, "method": "evm_address_portability", "evidence": ""}
  ],
  "total_addresses": 3,
  "total_chains": 3,
  "chains": ["ethereum", "bsc", "polygon"],
  "deployments": [],
  "total_deployments": 0
}
```

### `POST /api/v1/cross-chain/deployers/discover`

Discover deployers active on multiple chains.

**Query params:** `min_chains=2`, `chain_filter=""`, `limit=500`

### `POST /api/v1/cross-chain/persist`

Run batch persistence of multi-chain deployers to Neo4j + ClickHouse.

**Query params:** `min_chains=2`, `chain_filter=""`, `limit=200`, `dry_run=false`

---

## 8. Implementation Plan

### Phase 1: Foundation (DONE)
- [x] Create `app/cross_chain_tracking/` package structure
- [x] Define Pydantic models for all data types
- [x] Implement ClickHouse reader for deployer_events
- [x] Implement Neo4j writer with schema creation
- [x] Implement deployer matcher with confidence scoring
- [x] Implement entity tracker orchestrator
- [x] Create CLI entry point for batch operations
- [x] Update `app/cross_chain.py` with convenience wrappers
- [x] Update `entity_resolver.py` with deployer matching integration
- [x] Wire up `routers/v1/cross_chain.py` endpoints

### Phase 2: Bridge Detection (NEXT)
- [ ] `bridge_detector.py` — query bridge contracts for deposit/withdraw pairs
- [ ] Bridge transaction scanner using Alchemy/QuickNode historical data
- [ ] Store BRIDGED_TO edges in Neo4j
- [ ] Expand `entity_resolver.py` to include auto-detected bridge links

### Phase 3: Behavioral Fingerprinting (NEXT)
- [ ] `behavioral_matcher.py` — compare 8-dim fingerprints across chains
- [ ] Store BEHAVIORAL_MATCH edges in Neo4j
- [ ] FAISS index for fast similarity search across 169K+ wallets
- [ ] Adaptive confidence thresholds based on historical match rates

### Phase 4: Fund Flow Tracing (FUTURE)
- [ ] Flow graph traversal across chains via bridge contracts
- [ ] CEX deposit tracking (same CEX → different chains → same entity)
- [ ] Mixer/coinjoin address clustering
- [ ] Visualization API for fund flow graphs

---

## 9. Chain-Indexer Integration

### Schema (chain-indexer ClickHouse)

```sql
CREATE TABLE chain_indexer.deployer_events (
    deployer       String,
    token_address  String,
    chain          String,
    block_number   UInt64,
    block_timestamp DateTime,
    tx_hash        String,
    type           String,       -- 'token', 'pool', 'factory'
    factory        Nullable(String),
    ingested_at    DateTime
) ENGINE = MergeTree
ORDER BY (chain, block_number)
```

### Key Query

```sql
-- Find deployers active on 2+ chains
SELECT deployer, groupUniqArray(chain) AS chains,
       uniqExact(chain) AS chain_count, count() AS total_deployments
FROM chain_indexer.deployer_events
GROUP BY deployer
HAVING chain_count >= 2
ORDER BY total_deployments DESC
```

### Configuration

The chain-indexer's ClickHouse is accessed via the same CLICKHOUSE_* env vars
with database override `CHAIN_INDEXER_CLICKHOUSE_DB` (default: `chain_indexer`).

### Currently Indexed Chains

| Chain | Type | Subsquid | Deployer Tracking |
|-------|------|----------|-------------------|
| ethereum | EVM | ✅ | ✅ |
| bsc | EVM | ✅ | ✅ |
| arbitrum | EVM | ✅ | ✅ |
| base | EVM | ✅ | ✅ |
| polygon | EVM | ✅ | ✅ |
| solana | SVM | ✅ | ✅ (SPL tokens) |
| tron | TVM | ❌ | ✅ (TronGrid API) |

---

## 10. Testing Strategy

### Unit Tests

| Test | File | What It Tests |
|------|------|---------------|
| `test_models.py` | Pydantic validation, defaults, serialization |
| `test_deployer_matcher.py` | Confidence scoring, chain type detection |
| `test_neo4j_writer.py` | wallet_id format, edge creation queries |
| `test_entity_resolver_integration.py` | Deployer matching is called and deduplicated |

### Integration Tests

| Test | What It Verifies |
|------|------------------|
| ClickHouse connectivity | `fetch_multi_chain_deployers()` returns typed results |
| Neo4j edge creation | `create_same_as_edge()` creates bidirectional edge |
| End-to-end batch | `cli.py run --dry-run` executes without write errors |

### Test Fixtures

- Mock ClickHouse responses for deployer_events (fixtures in `tests/fixtures/deployer_events.json`)
- Test Neo4j database (ephemeral, docker-compose test profile)
- Pytest-asyncio for all async tests

---

## Appendix: Confidence Scoring Algorithm

```python
def calculate_confidence(chains: list[str]) -> tuple[float, str]:
    """Determine confidence that same-address across chains = same entity."""
    evm_count = sum(1 for c in chains if c in EVM_CHAINS)
    non_evm_count = sum(1 for c in chains if c in NON_EVM_CHAINS)

    if non_evm_count == 0:
        return 0.95, f"Same EVM deployer on {len(chains)} chains"
    elif evm_count > 0 and non_evm_count > 0:
        return 0.80, "Deployer active on EVM + non-EVM chains"
    else:
        return 0.60, "Same-name deployer on non-EVM chains"
```

EVM chains share the same 0x-prefixed 20-byte address format, so identical
addresses across EVM chains are nearly certain to be the same entity.
Solana uses base58 and Tron uses base58 with 'T' prefix, so same address
across different formats can't happen — mixed matches require additional
verification (same funding source, same deployment timing, same token names).
