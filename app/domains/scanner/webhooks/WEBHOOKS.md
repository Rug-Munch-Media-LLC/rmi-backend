# Webhooks — RugMunch Scanner Event System

This document describes the scanner webhook system (`app/domains/scanner/webhooks/`),
its event types, payload schemas, delivery semantics, and how to subscribe.

> Modeled after Helius and Alchemy webhook concepts. Our delivery model is
> **poll-based** (we detect events from our own data + free/self-hosted RPCs
> and POST them to you) rather than push-based (Helius subscribes to the chain
> for you). The payload shapes, however, follow the same conventions — in
> particular `transaction.enhanced` mirrors Helius "enhanced transactions".

---

## 1. Event Types

| Event type | Meaning | Data source |
|------------|---------|-------------|
| `token.deployed` | New token contract detected by our deployer tracker | ClickHouse `deployer_history` |
| `token.first_swap` | First DEX swap detected for a token | ClickHouse `token_transfers` × DEX router list |
| `token.price_change` | Token price moves beyond configured % threshold | Internal price oracle |
| `token.whale_move` | Large transfer (>$10K) from/to a known address | ClickHouse `token_transfers` |
| `token.liquidity_change` | Significant LP add/remove (>$5K) | ClickHouse + LiquidityWatchdog |
| `wallet.activity` | Any transfer involving a watched wallet address | ClickHouse `token_transfers` |
| `chain.block` | New block detected on monitored chains | Free public RPCs |
| `transaction.enhanced` | **Full decoded transaction** on watched wallets/tokens — token transfers, native SOL/ETH transfers, swap classification, account balance deltas (Helius-style enhanced transaction) | Helius RPC / free Solana RPC + Alchemy / EVM receipts |

`GET /api/v1/scanner/webhooks/events` returns this catalog at runtime.

---

## 2. Delivery Envelope

Every delivery is an HTTP `POST` with the following JSON body:

```json
{
  "event_id": "evt:1712000001",
  "event_type": "token.whale_move",
  "chain": "ethereum",
  "triggered_at": "2026-08-01T12:00:00.000000+00:00",
  "data": { "...": "provider-specific payload, see below" }
}
```

Request headers:

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `User-Agent` | `RMI-Webhook/1.0` |
| `X-Webhook-Event` | the event type (e.g. `transaction.enhanced`) |
| `X-Webhook-ID` | unique event id (dedupe key) |
| `X-Delivery-Attempt` | 1..3 |

### Delivery semantics

- Timeout: 10s per attempt.
- Retries: 3 total attempts with exponential backoff — `10s`, `30s`, `60s`.
- 2xx → delivered. 4xx → failed immediately (client error). 5xx / timeout /
  network error → retried.
- Delivery records are persisted to Redis (`rmi:webhook_deliveries:{event_id}`,
  7-day TTL) for observability.
- There is **no automatic endpoint disabling** (unlike Helius). To pause a
  failing endpoint, set `active: false` via `PATCH` (see §5).

### Telegram (internal) delivery

Subscriptions whose URL is `internal://telegram/{user_id}` (created by the
RugMunchBot `/alert` / `/watch` commands) are **not** POSTed over HTTP.
Instead the event is enqueued to the bot's Redis alert queue
(`bot:alerts:queue`), and the bot's `deliver_pending_alerts` job formats and
pushes it as a Telegram alert.

---

## 3. Payload Schemas

### 3.1 `token.deployed`

```json
{
  "chain": "ethereum",
  "token_address": "0x…",
  "token_name": "MyToken",
  "token_symbol": "MTK",
  "deployer_address": "0x…",
  "deployed_at": "2026-08-01 12:00:00"
}
```

### 3.2 `token.first_swap`

```json
{
  "chain": "ethereum",
  "block_timestamp": "2026-08-01 12:00:01",
  "transaction_hash": "0x…",
  "token_address": "0x…",
  "from_address": "0x…",
  "to_address": "0x…",
  "value_decimal": 1234.5,
  "detection_method": "dex_router_transfer"
}
```

### 3.3 `token.price_change`

```json
{
  "chain": "ethereum",
  "token_address": "0x…",
  "price": 0.000123,
  "price_before": 0.000115,
  "change_pct": 6.96,
  "direction": "up",
  "timestamp": 1780000000
}
```

### 3.4 `token.whale_move`

```json
{
  "chain": "ethereum",
  "block_timestamp": "2026-08-01 12:00:02",
  "transaction_hash": "0x…",
  "from_address": "0x…",
  "to_address": "0x…",
  "token_address": "0x…",
  "value_decimal": 50000.0,
  "transfer_type": "erc20"
}
```

### 3.5 `token.liquidity_change`

Same shape as `token.whale_move` plus `"detection_method": "large_transfer_fallback"`
(or watchdog-sourced events when LiquidityWatchdog is available).

### 3.6 `wallet.activity`

Same transfer shape as `token.whale_move`, emitted for any transfer touching a
watched wallet address (`filters.wallet_addresses`).

### 3.7 `chain.block`

```json
{
  "chain": "ethereum",
  "block_number": 20543210,
  "previous_block_number": 20543209
}
```

### 3.8 `transaction.enhanced` (NEW — Helius-style)

Full decoded transaction with instruction-level detail. Payload:

```json
{
  "chain": "solana",
  "signature": "5nNt…",
  "slot": 171942732,
  "timestamp": 1673445241,
  "status": "success",
  "type": "SWAP",
  "fee": 10000,
  "fee_payer": "…",
  "description": "Swap transaction 5nNt… involving 2 token transfer(s)",
  "tokenTransfers": [
    {
      "mint": "FdsN…",
      "source": "token account…",
      "destination": "token account…",
      "authority": "…",
      "amount": 1000000,
      "decimals": 6,
      "uiAmount": 1.0
    }
  ],
  "nativeTransfers": [
    { "fromUserAccount": "…", "toUserAccount": "…", "amount": 72936000000, "uiAmount": 72.936 }
  ],
  "accountData": [
    {
      "account": "…",
      "nativeBalanceChange": -72938049280,
      "tokenBalanceChanges": [
        { "mint": "…", "owner": "…", "rawTokenAmount": { "tokenAmount": "-1" }, "decimals": 0 }
      ]
    }
  ],
  "instructions": [
    { "programId": "JUP6Lkb…", "program": "JUPITER", "instructionType": "route" }
  ]
}
```

Supported `type` values (Helius-aligned subset): `SWAP`, `TRANSFER`,
`NFT_SALE`, `NFT_MINT`, `BURN`, `LIQUIDITY`, `UNKNOWN`.

For EVM chains, `chain` is the concrete chain (`ethereum`, `polygon`,
`arbitrum`, `base`), `signature` is the tx hash, `block_number` replaces
`slot`, and `tokenTransfers` carry `fromUserAccount` / `toUserAccount` /
`tokenStandard`.

Filters available for this event type: `wallet_addresses`,
`token_addresses`, `chains`, `transaction_types` (e.g. `["SWAP"]`),
`program_ids` (Solana program IDs / EVM router addresses).

---

## 4. How to Subscribe

### 4.1 Create a subscription

```bash
curl -X POST http://localhost:8000/api/v1/scanner/webhooks \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://your-server.example.com/rmi-webhook",
    "event_types": ["transaction.enhanced", "token.whale_move"],
    "filters": {
      "chains": ["solana", "ethereum"],
      "wallet_addresses": ["0xABC…", "So11111111111111111111111111111111111111112"],
      "transaction_types": ["SWAP", "TRANSFER"]
    }
  }'
```

Response: `201` with the created subscription (id, url, event_types, filters, active).

### 4.2 List subscriptions

```bash
curl http://localhost:8000/api/v1/scanner/webhooks
```

### 4.3 Get a single subscription

```bash
curl http://localhost:8000/api/v1/scanner/webhooks/whk:1712000000
```

### 4.4 Update a subscription (pause / change filters)

```bash
curl -X PATCH http://localhost:8000/api/v1/scanner/webhooks/whk:1712000000 \
  -H 'Content-Type: application/json' \
  -d '{"active": false}'                     # pause delivery
curl -X PATCH http://localhost:8000/api/v1/scanner/webhooks/whk:1712000000 \
  -H 'Content-Type: application/json' \
  -d '{"filters": {"transaction_types": ["SWAP"]}}'   # narrow event filter
```

### 4.5 Test delivery

Send a synthetic event to the endpoint to verify payload acceptance:

```bash
curl -X POST http://localhost:8000/api/v1/scanner/webhooks/whk:1712000000/test
```

### 4.6 Delete (unsubscribe)

```bash
curl -X DELETE http://localhost:8000/api/v1/scanner/webhooks/whk:1712000000
```

### 4.7 Telegram bot alerts (internal)

The RugMunchBot subscribes chats via
`POST /api/v1/scanner/webhooks/telegram` (used by `/alert` and `/watch`).
These create `internal://telegram/{user_id}` subscriptions delivered through
the bot's alert queue — no external URL required.

---

## 5. Subscription Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | e.g. `whk:1712000000` |
| `url` | str | `http(s)://` callback, or `internal://telegram/{user_id}` |
| `event_types` | list[str] | one or more event types from §1 |
| `filters.chains` | list[str] | chain filter, empty = all |
| `filters.token_addresses` | list[str] | token filter, empty = all |
| `filters.wallet_addresses` | list[str] | wallet filter (wallet.activity, transaction.enhanced) |
| `filters.min_value_usd` | float? | min USD value (whale/liquidity) |
| `filters.price_change_pct` | float? | min % price change |
| `filters.transaction_types` | list[str] | decoded tx type filter (transaction.enhanced) |
| `filters.program_ids` | list[str] | program/router filter (transaction.enhanced) |
| `active` | bool | delivery on/off |
| `created_at` | datetime | creation time |
| `last_triggered_at` | datetime? | last dispatch time |

---

## 6. Comparison: Ours vs Helius vs Alchemy

| Capability | Ours | Helius | Alchemy |
|------------|------|--------|---------|
| Delivery model | Poll + POST (free) | Push (paid credits) | Push (paid credits) |
| Enhanced decoded tx | ✅ `transaction.enhanced` | ✅ enhanced webhooks | ✅ `ALCHEMY_*` activity |
| Raw transaction payload | ❌ (planned) | ✅ raw webhooks | ✅ mined/raw tx |
| NFT mint/sale/burn events | Partial (classified inside `transaction.enhanced` as `NFT_MINT` / `NFT_SALE` / `BURN`) | ✅ dedicated `NFT_*` types | ✅ NFT activity |
| Program-account-change events | ❌ (planned — poll `program_ids` filter exists) | ✅ program webhooks | ✅ address activity |
| Transaction-type filter | ✅ `transaction_types` | ✅ `transactionType` | Partial |
| Endpoint auto-disable | ❌ (manual `active: false`) | ✅ (≥95% failure) | ✅ |
| Signature verification | Delivery is outgoing only | Incoming (for our receivers) | Incoming |
| Free tier | ✅ fully free | ❌ | ❌ |

---

## 7. Configuration & Operations

- **Engine enable/disable:** `WEBHOOK_ENGINE_ENABLED` (default `true`). Set to
  `false` to stop the poll loops.
- **Helius RPC:** set `HELIUS_API_KEY` (or `_2` / `_3` for round-robin) to use
  Helius RPC for `transaction.enhanced` Solana decoding. Without a key, the
  free public `api.mainnet-beta.solana.com` RPC is used (rate-limited).
- **Alchemy:** set `ALCHEMY_API_KEY` to enable EVM `transaction.enhanced`
  via `alchemy_getAssetTransfers` + `eth_getTransactionReceipt`. Without it,
  EVM enhanced events are skipped (fail-open).
- **ClickHouse:** `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE` for the
  poll providers backed by `wallet_memory` tables.
- **Redis:** subscription + cursor + delivery-record storage.
- **Telegram bridge:** `bot:alerts:queue` Redis list consumed by
  `app/domains/telegram/rugmunchbot/alert_worker.py`.

Cursor state is stored in Redis (`webhook:cursor:*`) so the engine can be
restarted without losing position. `transaction.enhanced` also keeps a bounded
ring of seen signatures (`cursor["seen_sigs"]`, max 200) to dedupe deliveries.
