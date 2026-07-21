-- 002_add_ttls.sql
-- Add 6-month expiry TTLs to all wallet_memory tables.
-- MODIFY TTL is metadata-only — does not rewrite data immediately.
-- Old data expires on the next merge cycle.

-- token_transfers: use block_timestamp (the canonical event time)
ALTER TABLE wallet_memory.token_transfers
MODIFY TTL block_timestamp + INTERVAL 6 MONTH DELETE;

-- wallet_entities: use last_updated_at (entity relations change over time)
ALTER TABLE wallet_memory.wallet_entities
MODIFY TTL last_updated_at + INTERVAL 6 MONTH DELETE;

-- scam_addresses: use first_seen_at (scam intel freshness)
ALTER TABLE wallet_memory.scam_addresses
MODIFY TTL first_seen_at + INTERVAL 6 MONTH DELETE;

-- wallet_profiles: use last_seen_at (profile staleness)
ALTER TABLE wallet_memory.wallet_profiles
MODIFY TTL last_seen_at + INTERVAL 6 MONTH DELETE;

-- deployer_history: use deployed_at (deploy records)
ALTER TABLE wallet_memory.deployer_history
MODIFY TTL deployed_at + INTERVAL 6 MONTH DELETE;

-- bridge_mappings: use bridged_at (bridge activity timestamp)
ALTER TABLE wallet_memory.bridge_mappings
MODIFY TTL bridged_at + INTERVAL 6 MONTH DELETE;

-- wallet_labels: use loaded_at (label ingestion time)
ALTER TABLE wallet_memory.wallet_labels
MODIFY TTL loaded_at + INTERVAL 6 MONTH DELETE;
