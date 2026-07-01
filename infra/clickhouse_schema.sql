-- RMI v5.0 ClickHouse Schema (T07)
-- Apply: docker exec rmi-clickhouse clickhouse-client < infra/clickhouse_schema.sql
-- Idempotent: all CREATE statements use IF NOT EXISTS.

CREATE DATABASE IF NOT EXISTS rmi;

USE rmi;

CREATE TABLE IF NOT EXISTS x402_revenue (
    tx_hash       String,
    tool_id       String,
    agent_id      String,
    amount_usd    Float64,
    chain         String,
    paid_at       DateTime,
    tier          String
) ENGINE = MergeTree() ORDER BY paid_at;

CREATE TABLE IF NOT EXISTS mcp_calls (
    call_id       String,
    tool_id       String,
    agent_id      String,
    latency_ms    UInt32,
    success       UInt8,
    called_at     DateTime
) ENGINE = MergeTree() ORDER BY called_at;

CREATE TABLE IF NOT EXISTS indexer_lag (
    chain         String,
    block_lag     UInt32,
    last_block    UInt64,
    updated_at    DateTime
) ENGINE = MergeTree() ORDER BY (chain, updated_at);

CREATE TABLE IF NOT EXISTS reputation_deltas (
    deployer_id   String,
    old_alpha     UInt32,
    old_beta      UInt32,
    new_alpha     UInt32,
    new_beta      UInt32,
    event_type    String,
    updated_at    DateTime
) ENGINE = MergeTree() ORDER BY (deployer_id, updated_at);

CREATE TABLE IF NOT EXISTS cost_attribution (
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
