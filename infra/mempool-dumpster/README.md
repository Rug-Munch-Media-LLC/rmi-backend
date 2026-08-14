# Mempool Dumpster — real-time mempool ingestion into ClickHouse

Deploys [flashbots/mempool-dumpster](https://github.com/flashbots/mempool-dumpster)
(Go mempool collector) to stream **Ethereum pending transactions** into our
platform ClickHouse (`rmi-clickhouse`) for real-time sandwich / snipe /
honeypot-buy detection.

**Scope:** infra config + runbook only. No application code.

---

## 1. What it does

`mempool-dumpster` subscribes to pending transactions at one or more EL/mempool
sources, then writes every seen transaction to:

1. **ClickHouse** (primary sink, since upstream v1.1.0) — batches of rows into
   the `mempool.transactions` and `mempool.sourcelogs` tables.
2. **Local CSV/Parquet archive** (`OUT=/mnt/data`, mounted volume) — durable
   fallback and historical archive, matching Flashbots' public archive schema
   at https://mempool-dumpster.flashbots.net.

Supported sources (WebSocket and/or gRPC):

| Source | Transport | Notes |
|--------|-----------|-------|
| Generic EL node (geth, Erigon, Infura) | WS (`newPendingTransactions`) | Free, best default |
| Alchemy | WS (`alchemy_pendingTransactions`) | Burns a lot of credits — use sparingly |
| bloXroute | WS + gRPC | Requires `BLX_AUTH` |
| Chainbound Fiber | gRPC | Requires `CHAINBOUND_AUTH`, lowest latency |
| Eden | WS + gRPC | Requires `EDEN_AUTH` |

Multiple collector instances can run concurrently; the ClickHouse
`transactions` table is a `ReplacingMergeTree` keyed on `hash`, so duplicates
across instances collapse automatically.

---

## 2. How the ClickHouse sink works

Since upstream **v1.1.0** the collector writes batches directly to ClickHouse
(the older flow was CSV files → offline merger → Parquet). Config is one env var:

```
CLICKHOUSE_DSN=clickhouse://<user>:<password>@<host>:9000/<database>
CLICKHOUSE_BATCH_SIZE=300   # rows per insert batch (default 300)
```

Behavior:

- The collector buffers transactions and flushes **batches** to ClickHouse
  (native TCP port 9000) — it does not insert row-by-row.
- It writes to **two tables**:
  - `transactions` — one row per unique transaction, deduplicated by `hash`
    (`ReplacingMergeTree(ver)` keeps the row with the **earliest**
    `received_at`). Safe even with multiple collector instances.
  - `sourcelogs` — one row per *observation* (tx seen by a source). Duplicates
    expected; filter with `min(receivedAt)` per `hash`.
- If ClickHouse is unreachable the collector keeps archiving CSV locally and
  retries on the next flush — the sink fails soft, not hard.
- Chain/network is taken from the source node; `chainId` is recorded per row.

Verify the sink is live:

```bash
docker compose logs -f mempool-dumpster        # look for ClickHouse batch writes
curl -s http://127.0.0.1:9090/metrics | grep -i clickhouse   # sink metrics
docker exec rmi-clickhouse clickhouse-client --query "SELECT count() FROM mempool.transactions"
```

---

## 3. Prerequisites

- The platform stack must be running (`docker compose up -d` from the repo
  root), which provides:
  - `rmi-clickhouse` container on the `rmi_network` bridge network
    (172.28.0.0/16), ClickHouse native port 9000 + HTTP 8123.
- A mempool-capable Ethereum node reachable over WebSocket
  (`ws://host:8546`, Alchemy, etc.). The platform geth/archive node is the
  usual source — confirm `newPendingTransactions` is enabled.
- On a fresh host (no platform stack): `docker network create rmi_network`.

---

## 4. Setup & run

### 4.1 Create the ClickHouse database + tables (one-time)

```bash
# apply the schema below (Section 6) inside the platform ClickHouse:
docker exec -i rmi-clickhouse clickhouse-client < infra/mempool-dumpster/clickhouse_schema.sql
```

The runbook keeps the DDL inline in Section 6 so you can apply it via
`clickhouse-client` heredoc; if you prefer, save it as
`infra/mempool-dumpster/clickhouse_schema.sql` and pipe it in.

### 4.2 Configure

```bash
cp infra/mempool-dumpster/.env.example infra/mempool-dumpster/.env
# edit .env — at minimum set NODES and CLICKHOUSE_DSN
```

Production DSN (attach to the existing platform ClickHouse):

```
CLICKHOUSE_DSN=clickhouse://rmi:<CLICKHOUSE_PASSWORD>@rmi-clickhouse:9000/rmi
```

> Recommend a dedicated `mempool` database + restricted user instead of the
> app `rmi` user:
> `CREATE DATABASE IF NOT EXISTS mempool;` then DSN
> `clickhouse://mempool:<pass>@rmi-clickhouse:9000/mempool`.

### 4.3 Start

```bash
docker compose -f infra/mempool-dumpster/docker-compose.yml up -d
docker compose -f infra/mempool-dumpster/docker-compose.yml ps
curl -s http://127.0.0.1:9092/txs | head                      # transaction API
curl -s http://127.0.0.1:9090/metrics | head                  # prometheus metrics
```

**Standalone (isolated test ClickHouse, no platform stack):**

```bash
docker network create rmi_network           # if missing
docker compose -f infra/mempool-dumpster/docker-compose.yml --profile standalone up -d
# ClickHouse available at 127.0.0.1:8124 (HTTP) / 127.0.0.1:9001 (native)
# DSN for this mode: clickhouse://mempool:<pass>@mempool-dumpster-clickhouse:9000/mempool
```

### 4.4 Stop / rotate

```bash
docker compose -f infra/mempool-dumpster/docker-compose.yml down
# keep data: docker compose ... down   (volumes persist)
# wipe local archive: docker compose ... down -v
```

---

## 5. Config reference (env vars)

| Var | Default | Description |
|-----|---------|-------------|
| `NODES` | *(required)* | Comma-separated ws/wss mempool source endpoints |
| `CHECK_NODE` | empty | Node URI used to check inclusion status |
| `SRC_ALIASES` | empty | Comma-separated `alias=url` source labels |
| `BLX_AUTH` / `CHAINBOUND_AUTH` / `EDEN_AUTH` | empty | Premium feed auths (`token` or `token@url`) |
| `OUT` | `/mnt/data` | Local CSV archive dir (mounted volume) |
| `LOCATION` | `talos-eu` | Free-form location tag in sourcelogs |
| `CLICKHOUSE_DSN` | empty | ClickHouse DSN; empty disables the sink |
| `CLICKHOUSE_BATCH_SIZE` | `300` | Rows per ClickHouse batch insert |
| `API_ADDR` | `0.0.0.0:9092` | Transaction API listen addr (loopback port) |
| `METRICS_ADDR` | `0.0.0.0:9090` | Prometheus metrics listen addr (loopback port) |
| `ENABLE_PPROF` | `0` | Enable pprof on metrics server (debug only) |
| `HEALTHCHECKS_IO_URI` | empty | healthchecks.io ping URL for uptime alerting |
| `MEMPOOL_DUMPSTER_IMAGE` | `flashbots/mempool-dumpster:1.3.1` | Image pin override |

Secrets (`BLX_AUTH`, `CHAINBOUND_AUTH`, `EDEN_AUTH`, `CLICKHOUSE_DSN` password,
Alchemy keys) come from gopass / env — never commit a real `.env`.

---

## 6. ClickHouse table schema

Apply inside the platform ClickHouse (upstream schema, v1.1.0+ compatible):

```sql
CREATE DATABASE IF NOT EXISTS mempool;

-- Transaction details, deduplicated by hash (keeps earliest received_at).
CREATE TABLE IF NOT EXISTS mempool.transactions (
    received_at DateTime64(3, 'UTC'),
    hash String,
    chain_id String,
    tx_type Int64,
    from String,
    to String,
    value String,
    nonce String,
    gas String,
    gas_price String,
    gas_tip_cap String,
    gas_fee_cap String,
    data_size Int64,
    data_4bytes String,
    raw_tx String,

    ver Int64 MATERIALIZED -toUnixTimestamp(received_at)
)
ENGINE = ReplacingMergeTree(ver)
PRIMARY KEY (hash)
ORDER BY (hash)
PARTITION BY toDate(received_at)
COMMENT 'Mempool transactions, deduplicated by hash, keeps earliest received_at.';

-- Receipt log for every observation of a tx by any source (duplicates possible).
CREATE TABLE IF NOT EXISTS mempool.sourcelogs (
    received_at DateTime64(3),
    hash String,
    source String,
    location String,
)
ENGINE = MergeTree
PRIMARY KEY (received_at, hash)
ORDER BY (received_at, hash)
PARTITION BY toDate(received_at)
COMMENT 'Mempool observation log (one row per source that saw the tx).';

-- Suggested TTLs (retention, apply if desired):
-- ALTER TABLE mempool.transactions MODIFY TTL toDate(received_at) + INTERVAL 90 DAY;
-- ALTER TABLE mempool.sourcelogs    MODIFY TTL toDate(received_at) + INTERVAL 30 DAY;
```

Notes:

- `value`, `gas`, `gas_price`, `gas_tip_cap`, `gas_fee_cap`, `nonce` are stored
  as **decimal strings** (uint256 too large for Int64/UInt256 native types in
  this schema) — cast with `toUInt256(...)` in queries.
- `raw_tx` is the RLP-encoded typed transaction (EIP-2718 envelope); decode
  with `decode` in analysis code or keep raw for replay/honeypot simulation.
- Because `transactions` is `ReplacingMergeTree`, read with
  `SELECT ... FINAL` or aggregate `argMin` to see current state.

---

## 7. Alerting ideas — real-time MEV / snipe / honeypot detection

The whole point of the sink: cheap, fast queries over the mempool window.

### 7.1 Sandwiches (candidate attacker txs)

Classic sandwich: same `from` sends a buy **just before** (front-run) and a
sell **just after** (back-run) a target's swap, targeting the same pool with
high gas. Look for repeated high-gas txs to known DEX routers:

```sql
SELECT
  from, to,
  count() AS txs,
  min(received_at) AS first_seen,
  max(received_at) AS last_seen,
  uniq(data_4bytes) AS methods
FROM mempool.transactions
WHERE to IN (
  '0x7a250d5630b4cf539739df2c5dacb4c659f2488d', -- Uniswap V2 Router
  '0xdef171fe48cf0115b1d80b88dc8eab59176fee57', -- Uniswap V3 SwapRouter
  '0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad'  -- 0x V3 ExchangeProxy
)
  AND gas_price > toUInt256('100000000000')   -- > 100 gwei
  AND received_at > now() - INTERVAL 60 SECOND
GROUP BY from, to
HAVING txs >= 2 AND methods >= 2
ORDER BY txs DESC;
```

Follow up with per-hash inspection to confirm front-run/back-run ordering:

```sql
SELECT hash, from, to, gas_price, data_4bytes, received_at
FROM mempool.transactions
WHERE from = '<suspect>'
  AND received_at > now() - INTERVAL 5 MINUTE
ORDER BY received_at;
```

### 7.2 Snipe / token-launch buys

New-pair snipers hit the pool router immediately at launch with tiny slippage
and max gas. Flag bursty buys to the same `to` (pool/router) from new addresses:

```sql
SELECT
  to AS pool,
  uniqExact(from) AS unique_buyers,
  count() AS buys,
  quantile(0.9)(toUInt256(gas_price)) AS p90_gas
FROM mempool.transactions
WHERE data_4bytes = '0x38ed1739'           -- swapExactTokensForTokens
  AND received_at > now() - INTERVAL 30 SECOND
GROUP BY pool
HAVING buys >= 5 AND unique_buyers >= 3
ORDER BY buys DESC;
```

New-pair sniping often shows `from` addresses that are **fresh** (low nonce /
no prior history in `mempool.transactions`):

```sql
SELECT t.from, count() AS txs
FROM mempool.transactions t
LEFT JOIN (
  SELECT from, min(received_at) AS first_seen
  FROM mempool.transactions GROUP BY from
) f ON t.from = f.from
WHERE t.received_at > now() - INTERVAL 60 SECOND
GROUP BY t.from
HAVING count() >= 3 AND min(t.received_at) - max(f.first_seen) < INTERVAL 1 HOUR
ORDER BY txs DESC;
```

### 7.3 Honeypot-buy detection

Buy orders directed at freshly deployed (unverified / unrenounced) contracts.
Pair mempool activity with our existing deployer/contract intelligence:
transactions whose `to` matches a **recently deployed** contract with a buy
method (`0x38ed1739`, `0x095ea7b3` approve, etc.) and high gas — these are the
trades our scanner should re-check against honeypot heuristics before the
victim's tx lands:

```sql
SELECT
  hash, from, to AS contract, data_4bytes,
  gas_price, value, received_at
FROM mempool.transactions
WHERE data_4bytes IN ('0x38ed1739','0x095ea7b3','0x7ff36ab5')  -- buys / approves
  AND to IN (
    SELECT deployer_tracker.contract -- contracts deployed recently
    FROM mempool.transactions
    WHERE data_4bytes = '' AND to = '0x0000000000000000000000000000000000000000'
  )
  AND gas_price > toUInt256('50000000000')   -- > 50 gwei
  AND received_at > now() - INTERVAL 30 SECOND;
```

### 7.4 Integration hooks

- Feed flagged hashes into the scanner pipeline (`app/scanners/`) via the
  transaction API: `curl http://127.0.0.1:9092/txs?hash=0x...`.
- Add Prometheus rules in `infra/alert_rules.yml` on collector metrics
  (`mempool_dumpster_*`, sink failures, batch flush lag).
- Keep `HEALTHCHECKS_IO_URI` set so a dead collector pages before the data
  gap grows.

---

## 8. Operations

- **Logs:** `docker compose -f infra/mempool-dumpster/docker-compose.yml logs -f`
- **Metrics:** `curl -s http://127.0.0.1:9090/metrics` (VictoriaMetrics format,
  `/metrics` path confirmed in upstream `collector/collector.go`).
- **Transaction API:** `curl -s http://127.0.0.1:9092/txs`
- **Restart policy:** `restart: unless-stopped`; healthcheck hits the metrics
  endpoint every 30s.
- **Backup:** ClickHouse data is covered by existing backups; the local
  CSV/Parquet archive (`dumpster-data` volume) is a secondary archive — add it
  to backup policy if retention matters.

### Security notes

- API + metrics ports bind to `127.0.0.1` only — never expose publicly.
- Container drops all Linux capabilities, runs with `no-new-privileges`, no
  `host` networking.
- Secrets via gopass/env only; `.env` is gitignored, `.env.example` is the
  committed template.
- Pin image: `flashbots/mempool-dumpster:1.3.1` (override via
  `MEMPOOL_DUMPSTER_IMAGE`). Audit upstream tags before bumping.

---

## 9. References

- Upstream: https://github.com/flashbots/mempool-dumpster (MIT)
- ClickHouse sink notes: https://github.com/flashbots/mempool-dumpster/releases/tag/v1.1.0
- Public archive: https://mempool-dumpster.flashbots.net
- Upstream compose + schema: `docker-compose.yaml`, `schema/clickhouse/` in the repo
