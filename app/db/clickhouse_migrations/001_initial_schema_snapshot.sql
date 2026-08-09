-- 001_initial_schema_snapshot.sql
-- Capture of the wallet_memory schema as defined in app/wallet_memory/storage.py
-- Applied 2026-07-21. This is a schema snapshot only — no tables existed before this migration.

CREATE DATABASE IF NOT EXISTS wallet_memory;

CREATE TABLE IF NOT EXISTS wallet_memory.token_transfers (
    chain_id      String,
    block_number   UInt64,
    block_timestamp DateTime,
    transaction_hash String,
    log_index      UInt32,
    from_address   String,
    to_address     String,
    token_address  String,
    value_raw      String,
    value_decimal  Float64,
    transfer_type  LowCardinality(String),
    is_scam_related UInt8 DEFAULT 0
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(block_timestamp)
ORDER BY (chain_id, block_number, transaction_hash, log_index);

CREATE TABLE IF NOT EXISTS wallet_memory.wallet_entities (
    entity_id       String,
    wallet_address  String,
    chain_id        String,
    heuristic_type  LowCardinality(String),
    confidence_score Float32,
    entity_label    String DEFAULT '',
    entity_category LowCardinality(String) DEFAULT 'unknown',
    first_linked_at DateTime DEFAULT now(),
    last_updated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(last_updated_at)
ORDER BY (wallet_address, chain_id);

CREATE TABLE IF NOT EXISTS wallet_memory.scam_addresses (
    address     String,
    chain_id    String,
    source      LowCardinality(String),
    threat_type LowCardinality(String),
    confidence  Float32,
    first_seen_at DateTime,
    evidence    String DEFAULT ''
) ENGINE = MergeTree()
ORDER BY (address, chain_id);

CREATE TABLE IF NOT EXISTS wallet_memory.wallet_profiles (
    wallet_address     String,
    chain_id           String,
    entity_id          String DEFAULT '',
    entity_label       String DEFAULT '',
    entity_category    LowCardinality(String) DEFAULT 'unknown',
    total_transactions UInt64 DEFAULT 0,
    total_volume       Float64 DEFAULT 0,
    unique_counterparties UInt32 DEFAULT 0,
    first_seen_at      DateTime,
    last_seen_at       DateTime,
    risk_score         Float32 DEFAULT 0,
    risk_level         LowCardinality(String) DEFAULT 'unknown',
    updated_at         DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (wallet_address, chain_id);

CREATE TABLE IF NOT EXISTS wallet_memory.deployer_history (
    deployer_address String,
    chain_id         String,
    token_address    String,
    token_name       String DEFAULT '',
    token_symbol     String DEFAULT '',
    deployed_at      DateTime,
    outcome          LowCardinality(String) DEFAULT 'unknown',
    lifespan_days    Float32 DEFAULT 0,
    max_market_cap   Float64 DEFAULT 0,
    is_scam_related  UInt8 DEFAULT 0,
    scan_risk_score  Float32 DEFAULT 0
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(deployed_at)
ORDER BY (deployer_address, chain_id, deployed_at);

CREATE TABLE IF NOT EXISTS wallet_memory.bridge_mappings (
    source_address String,
    source_chain   String,
    dest_address   String,
    dest_chain     String,
    bridge_name    LowCardinality(String),
    tx_hash        String,
    bridged_at     DateTime,
    confidence     Float32 DEFAULT 1.0
) ENGINE = MergeTree()
ORDER BY (source_address, source_chain);

CREATE TABLE IF NOT EXISTS wallet_memory.wallet_labels (
    address        String,
    chain_id       String,
    label_name     String,
    label_category String,
    source         String,
    is_sanctioned  UInt8 DEFAULT 0,
    loaded_at      DateTime DEFAULT now(),
    label_subtype  String DEFAULT ''
) ENGINE = MergeTree()
ORDER BY (address, label_name, source);
