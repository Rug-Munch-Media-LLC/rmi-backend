-- 004_entity_label_hygiene_down.sql — DOWN
-- Reverses everything 004 created. Run with mutations_sync=1 (the two DELETEs
-- are ClickHouse mutations).
--
-- Deliberately NOT reversed:
--   * is_sanctioned quarantine — restoring 3.7M false flags would restore the defect.
--   * chain-id canonicalisation — the pre-image (eth/sol vs ethereum/solana) is not
--     recoverable row-by-row; wallet_memory.chain_alias keeps the mapping for audits.

-- 1. remove the promoted Solana CEX entity rows
ALTER TABLE wallet_memory.entity_labels DELETE WHERE source = 'solana_cex';

-- 2. remove the promoted OFAC rows that 004 inserted
ALTER TABLE wallet_memory.wallet_labels DELETE WHERE source = 'ofac_sdn';

-- 3. drop the guard
ALTER TABLE wallet_memory.wallet_labels DROP CONSTRAINT IF EXISTS chk_sanctioned_source;

-- 4. drop the canonical read surface, then the mapping
DROP VIEW IF EXISTS wallet_memory.v_wallet_labels;
DROP VIEW IF EXISTS wallet_memory.v_entity_labels;
DROP DICTIONARY IF EXISTS wallet_memory.chain_alias_dict;
DROP TABLE IF EXISTS wallet_memory.chain_alias;
