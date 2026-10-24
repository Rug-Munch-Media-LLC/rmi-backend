# Orphan worktree archive — 2026-10-24

Disposition of artifacts recovered from the 2026-10-24 orphaned-metadata worktree
cleanup. Source manifest: `/tmp/wt-orphan/MANIFEST.txt` on Talos.

Two artifact classes were recovered: **unique files from deleted worktree metadata dirs**
(diffed against every ref — `dup=0`) and **stray blobs** not reachable in any repo history.

Scope note: only the `rmi-backend`-owned artifact is in this repo's lane. The two stray
scripts belong to **rugmunch-scanner** and are recorded and rejected here with a handoff,
not ported — see "Rejected" below.

## Verdicts

| artifact | owner repo | verdict | reason |
|---|---|---|---|
| `smart-money/app/api/v1/public/smart_money.py` | rmi-backend | **already recovered** | Contained in `main` via PR #36 (`fix/smart-money-recovered-20261024`). Only delta is an import style (`collections.abc.Callable` moved behind `TYPE_CHECKING`) — `main` is the better version. No unique behaviour, no route to merge. |
| `strays/intel-data/load_intel_data.py` | rugmunch-scanner | **rejected** | Hardcoded ClickHouse password fallback; duplicates canonical ingest already present in rugmunch-scanner; hardcoded absolute paths into throwaway worktrees. |
| `strays/scanner-pump2/pumpfun_bonding.py` | rugmunch-scanner | **rejected — handoff** | Not valid Python (severed module docstring, 122 ruff errors); truncated before any `BondState`-assembling entry point. Genuinely unique design content preserved below as a spec. |

## smart-money public API — no merge required

`app/api/v1/public/smart_money.py` in `main` is byte-identical to the orphan except for
the import block:

```diff
-from collections.abc import Callable
 from datetime import UTC, datetime
-from typing import Any
+from typing import TYPE_CHECKING, Any
+
+if TYPE_CHECKING:
+    from collections.abc import Callable
```

The route is registered once in `app/api/v1/__init__.py` (`smart_money_router`,
line 85). Adding the orphan would be a duplicate route. **Nothing merged.**

## Rejected — `strays/intel-data/load_intel_data.py`

Three independent blockers, any one sufficient:

1. **Credential in code.** `CLICKHOUSE_PASSWORD` defaults to a literal 32-char password
   rather than an empty string. This repo's canonical pattern is
   `os.getenv("CLICKHOUSE_PASSWORD", "") or ""` (see `app/routers/rugcharts/helpers/common.py`).
   The literal is absent from `app/`, `tests/` and `scripts/`, so it was never in this
   repo — correct. Do not import it. **Action: treat that password as exposed** (it sat
   in a scratch file) and rotate the `mcp_ro` ClickHouse user.
2. **Superseded.** rugmunch-scanner already owns every load path this script re-implements:
   `scripts/backfill_liquidations.py`, `rugmunch_scanner/hyperliquid/records.py`,
   `rugmunch_scanner/hyperliquid/fills.py`, `rugmunch_scanner/polymarket/ingest.py`.
3. **Not portable.** Hardcoded absolute paths into two throwaway worktrees (a capture
   JSONL and an output snapshot) — neither survives worktree cleanup. Plus `ruff` F841
   (assignment to unused `snapshot`) and five responsibilities in one module (DDL +
   ingest + backfill + snapshot export + `main`).

### Preserved insight — the liquidation dedupe fix

The one non-obvious contribution is the `hl_liquidations_v2` sort key. `ReplacingMergeTree`
collapses rows that share the `ORDER BY` tuple, so the original table dropped distinct
fills that landed in the same millisecond for the same account. Adding `tid` and `coin`
to the key keeps them:

```sql
CREATE TABLE IF NOT EXISTS wallet_memory.hl_liquidations_v2
(
    source LowCardinality(String), lid Int64 DEFAULT 0, ts_ms Int64,
    coin LowCardinality(String) DEFAULT '', px Float64 DEFAULT 0, sz Float64 DEFAULT 0,
    notional Float64, mark_px Float64 DEFAULT 0, method LowCardinality(String) DEFAULT '',
    dir String DEFAULT '', closed_pnl Float64 DEFAULT 0, liquidated_user String,
    liquidator String DEFAULT '', account_value Float64 DEFAULT 0, tid Int64 DEFAULT 0,
    hash String DEFAULT '', side LowCardinality(String) DEFAULT '',
    ingested_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMMDD(toDateTime(intDiv(ts_ms, 1000)))
ORDER BY (liquidated_user, ts_ms, tid, coin, source)
TTL toDateTime(intDiv(ts_ms, 1000)) + toIntervalDay(180)
SETTINGS index_granularity = 8192;
```

Verify uniqueness with `SELECT count() FROM wallet_memory.hl_liquidations_v2 FINAL`
against the source capture, not against `hl_liquidations`.

## Rejected with handoff — `strays/scanner-pump2/pumpfun_bonding.py`

The extracted file lost its opening `"""`, so line 1 is bare prose:

```
SENTINEL - Pump.fun Bond-State Detection
=========================================
```

`ast.parse` → `SyntaxError`; `ruff check --select F,E9` → **122 errors**. It also stops
mid-module: `BondState` is declared but never constructed, and there is no exposed
detector entry point or registry wiring. `resolve_data_access_mode`, `lookup_historical`,
`DataAccessUnavailable` and `time` are imported and unused. All four imports *do* resolve
against rugmunch-scanner, so the fragment would import once repaired — it is the missing
body, not the dependencies, that blocks the port.

### Preserved spec — bond-state contract

Worth rebuilding in `rugmunch_scanner/pumpfun/` (alongside `curve.py`, which already owns
curve maths). Values are `null` + an entry in `warnings` when unavailable; never `0`.

| field | source |
|---|---|
| `creator`, `bonded`, `bonded_at`, `bonding_progress_pct` | `wallet_memory.solana_launches` (`graduation_signal`, `confidence`) |
| `unique_buyers`, `volume_since_bond` | `wallet_memory.dex_events` where `type = "swap"` and `block_timestamp >= bonded_at` |
| `dev_hold_pct` | `wallet_memory.solana_spl_balance_changes` |
| all of the above | B2 parquet lake via DuckDB when ClickHouse is unavailable |

Thresholds: `ALMOST_BONDED_THRESHOLD_PCT = 80.0`,
`RECENTLY_BONDED_WINDOW_MIN = 60`, `PUMPFUN_GRADUATION_SOL = 85.0`.
`data_source` records which tier answered.

## Recovery

The rejected blobs remain under `/tmp/wt-orphan/` on Talos. `/tmp` is not durable —
if the originals are ever wanted verbatim, copy them out before the next reaper pass.
Nothing here was force-pushed; `main` was not rewritten.
