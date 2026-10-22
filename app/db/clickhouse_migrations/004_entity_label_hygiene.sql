-- 004_entity_label_hygiene.sql — UP
-- Entity-label hygiene for wallet_memory.{wallet_labels,entity_labels}.
--   1. one chain-id mapping (table + dictionary + canonical views)
--   2. quarantine non-authoritative sanctions (is_sanctioned=1 only for official lists)
--   3. promote the authoritative OFAC SDN set that lived only in entity_labels
--   4. DB-level guard: no writer can flag a row sanctioned outside the allowlist
--   5. physical chain-id canonicalisation of the alias forms that are actually split
--   6. promote solana_cex exchange labels into entity_labels
-- Idempotent: every statement is safe to re-run (verified: re-run appends 0).
-- DOWN: 004_entity_label_hygiene_down.sql

-- ── 1. Single chain-id mapping ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wallet_memory.chain_alias
(
    alias     String,
    canonical String,
    note      String DEFAULT '',
    added_at  DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree
ORDER BY alias;

INSERT INTO wallet_memory.chain_alias (alias, canonical, note) VALUES
    ('btc',   'bitcoin',     'legacy short form (wilder/mbal/ofac)'),
    ('bitcoin','bitcoin',    'canonical'),
    ('eth',   'ethereum',    'legacy short form (etherscan imports)'),
    ('ethereum','ethereum',  'canonical'),
    ('sol',   'solana',      'legacy short form (solana_cex/solana_dapp)'),
    ('solana','solana',      'canonical'),
    ('bnb',   'bsc',         'legacy short form'),
    ('bsc',   'bsc',         'canonical'),
    ('hype',  'hyperliquid', 'legacy short form'),
    ('hyperliquid','hyperliquid','canonical');

-- collapse the 10-row re-insert above into one row per alias
OPTIMIZE TABLE wallet_memory.chain_alias FINAL;

CREATE DICTIONARY IF NOT EXISTS wallet_memory.chain_alias_dict
(
    alias     String,
    canonical String
)
PRIMARY KEY alias
SOURCE(CLICKHOUSE(DB 'wallet_memory' TABLE 'chain_alias'))
LIFETIME(MIN 0 MAX 300)
LAYOUT(HASHED());

-- canonical read surface: any consumer asking for bitcoin/ethereum/solana/bsc/
-- hyperliquid gets the right rows regardless of the stored short form.
CREATE OR REPLACE VIEW wallet_memory.v_wallet_labels AS
SELECT
    address,
    dictGetOrDefault('wallet_memory.chain_alias_dict', 'canonical', chain_id, chain_id) AS chain_id,
    label_name, label_category, source, is_sanctioned, loaded_at,
    label_subtype, tier, tier_reason, content_hash
FROM wallet_memory.wallet_labels;

CREATE OR REPLACE VIEW wallet_memory.v_entity_labels AS
SELECT
    address,
    dictGetOrDefault('wallet_memory.chain_alias_dict', 'canonical', chain_id, chain_id) AS chain_id,
    entity_type, label_type, name, source, provenance, first_seen, confidence
FROM wallet_memory.entity_labels;

-- ── 2. Quarantine false sanctions ─────────────────────────────────────────────
-- 3,743,671 rows carried is_sanctioned=1 inherited from Wilder tier A / MBAL
-- tiers. Only official sanctions lists may set the flag.
ALTER TABLE wallet_memory.wallet_labels
    UPDATE is_sanctioned = 0
WHERE is_sanctioned = 1
  AND source NOT IN ('safe_sanctions_us_ofac','safe_sanctions_uk','safe_sanctions_eu','ofac_sdn');

-- ── 3. Promote the authoritative OFAC SDN set ─────────────────────────────────
-- 499 OFAC addresses lived only in entity_labels(source='ofac_sdn'); 408 of them
-- were invisible to any wallet_labels.is_sanctioned check.
INSERT INTO wallet_memory.wallet_labels
    (address, chain_id, label_name, label_category, source, is_sanctioned,
     label_subtype, tier, tier_reason)
SELECT
    e.address,
    e.chain_id,
    'ofac_sdn',
    'sanctioned',
    'ofac_sdn',
    toUInt8(1),
    'ofac_sdn',
    'A',
    concat('authoritative OFAC SDN; entity name: ', e.name)
FROM wallet_memory.entity_labels AS e
WHERE e.source = 'ofac_sdn'
  AND e.address NOT IN (SELECT address FROM wallet_memory.wallet_labels WHERE is_sanctioned = 1)
ORDER BY e.address
LIMIT 1 BY e.address;

-- ── 4. Writer-agnostic guard ──────────────────────────────────────────────────
-- Applies to INSERTs only; blocks every loader (incl. a future Wilder reload)
-- from setting the flag outside the authoritative allowlist.
ALTER TABLE wallet_memory.wallet_labels
    ADD CONSTRAINT IF NOT EXISTS chk_sanctioned_source
    CHECK is_sanctioned = 0
       OR source IN ('safe_sanctions_us_ofac','safe_sanctions_uk','safe_sanctions_eu','ofac_sdn');

-- ── 5. Physical chain-id canonicalisation ─────────────────────────────────────
-- Only the alias forms that are actually split in the data are rewritten;
-- 'btc' stays stored (see note in the report) and is canonicalised at read time.
ALTER TABLE wallet_memory.wallet_labels
    UPDATE chain_id = multiIf(
        chain_id = 'eth', 'ethereum',
        chain_id = 'sol', 'solana',
        chain_id = 'bnb', 'bsc',
        chain_id = 'hype','hyperliquid',
        chain_id)
WHERE chain_id IN ('eth','sol','bnb','hype');

-- entity_labels has chain_id inside its ORDER BY key, so ClickHouse rejects
-- ALTER ... UPDATE on it (CANNOT_UPDATE_COLUMN). It also has no alias rows to
-- fix — verified: 0 rows with chain_id in ('eth','sol','bnb','hype'). Its
-- canonical surface is the v_entity_labels view, and write-side canonicalisation
-- is enforced by the loader seam (documents/LABEL-WRITERS.md).

-- ── 6. Promote solana_cex → entity_labels ─────────────────────────────────────
-- 100,000 exchange-labelled Solana rows never promoted. Exchange-branded
-- (18 CEX brands); tier B → confidence 0.6. Re-run appends 0.
INSERT INTO wallet_memory.entity_labels
    (address, chain_id, entity_type, label_type, name, source, provenance, confidence)
SELECT
    w.address,
    'solana',
    'exchange',
    if(w.label_name LIKE '%deposit_wallet', 'deposit_wallet', 'exchange_wallet'),
    replaceOne(w.label_name, ' deposit_wallet', ''),
    'solana_cex',
    'wallet_labels:solana_cex',
    toFloat32(if(w.label_name LIKE '%deposit_wallet', 0.6, 0.95))
FROM wallet_memory.wallet_labels AS w
WHERE w.source = 'solana_cex'
  AND w.address NOT IN (SELECT address FROM wallet_memory.entity_labels WHERE source = 'solana_cex');
