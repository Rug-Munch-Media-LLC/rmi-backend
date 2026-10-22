# Label writers — ownership, overlap, and the canonical seam

`wallet_memory.wallet_labels` (40.0M rows) and `wallet_memory.entity_labels`
(421,770 rows after 004) have **four** write paths with no shared contract.
This note records the overlap and the seam 004 imposes.

## The four writers

| # | Writer | Target table | Rows owned (approx) | Chain ids emitted | Sets `is_sanctioned` |
|---|--------|--------------|---------------------|-------------------|----------------------|
| 1 | `app/wallet_memory/label_importer/` (`download.py` + `__init__.py`) — rmi-backend | `wallet_labels` | etherscan/malicious/scam feeds; `scanner:wallet_labels:*` prefixes | `ethereum`, `eth` (pre-004) | yes (`_load_ofac_sanctions`) |
| 2 | `scripts/ingest_wallet_labels.py` — rugbrain | `wallet_labels` | bulk reference loads (wilder/mbal-class dumps) | `btc`, `sol`, `eth` | historically yes (root cause of the 3.7M false flags) |
| 3 | `scripts/data/ingest_scam_addresses.py` — fleet-infra | `wallet_memory.scam_addresses` + re-emits `entity_labels` as `scam_addresses:*` (66,789 rows) | derived from (1) | copies whatever (1) wrote | no |
| 4 | `rugmunch_scanner/wallet_memory/label_importer/` — rugmunch-scanner | `wallet_labels` | a **fork** of (1); shares module names but not code | `eth`/`ethereum` | no |

Overlap: (1) and (4) are near-duplicate importers over the same sources; (3) is a
derived read of (1) that writes back a second copy into `entity_labels`, so the same
scam address exists twice under two source names. (2) is the only writer that ever
flagged sanctions non-authoritatively.

## The seam (single canonical loader)

`app/wallet_memory/storage.py` + `app/wallet_memory/label_importer/` is the only
module that speaks ClickHouse for labels from rmi-backend, and `rugmunch-scanner`
already imports the same module names. Recommended target:

1. (4) imports (1) instead of forking it — delete the scanner copy.
2. (3) stops re-emitting `entity_labels` and reads `entity_type='malicious'` from
   the canonical table instead of duplicating rows.
3. (2) calls the same writer and passes `is_sanctioned` only for the four
   allowlisted sources.

**Not done in 004** — changing four live production writers in one commit is the
higher-risk path, and the DB-level guard below already removes the worst failure
mode regardless of which writer runs.

## What 004 enforces (writer-agnostic)

- `CONSTRAINT chk_sanctioned_source` on `wallet_labels`: any INSERT with
  `is_sanctioned=1` and a source outside
  `safe_sanctions_us_ofac | safe_sanctions_uk | safe_sanctions_eu | ofac_sdn`
  is **rejected**. A future Wilder reload cannot restore the 3.7M false flags.
- `wallet_memory.chain_alias` + `chain_alias_dict` + `v_wallet_labels` /
  `v_entity_labels`: one mapping (`btc→bitcoin`, `eth→ethereum`, `sol→solana`,
  `bnb→bsc`, `hype→hyperliquid`). Consumers read the views to get canonical ids;
  loaders should normalise through the dictionary before INSERT.
- Physical normalisation applied to `wallet_labels` for `eth→ethereum` and
  `sol→solana` (161k rows). `btc` intentionally stays stored — see [REVIEW] in 004.
