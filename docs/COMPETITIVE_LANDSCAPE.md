# RugMunchScan — Competitive Landscape

> Living document. Last updated: 2026-08-03
> Companion to `SCANNER_STATUS.md` and `docs/scan-capabilities-spec.md`.

## Summary

**34+ open-source token scanners were identified and reviewed.** Every one of them is
either a thin API wrapper (GoPlus / DexScreener / Honeypot.is / RugCheck / Birdeye
front-end) or a single-chain tool. **None uses self-hosted indexer data as its primary
source of truth.**

RugMunchScan is the first scanner to combine:

1. **Own indexer** (lite-chain-indexer v1.3.0, EVM + Solana, ClickHouse) — source of truth
2. **Multi-chain** coverage (EVM chains + Solana)
3. **Free API fallback chains** (5-tier: own data → free APIs → paid last)
4. **Telegram + webhook alerts** (scan, wallet, investigate, WebhookEngine, attack alerts)
5. **Cross-chain entity resolution** (C4 — link the same actor across chains)

> **Differentiator: "Own your data. Our indexer is the source of truth; free APIs are the safety net."**

---

## Closest Competitors

| Project | Language / Stack | Coverage | Data Model | Weakness vs RugMunchScan |
|---|---|---|---|---|
| **tokenassayer** | Rust (revm fork) | EVM (single-chain) | Local revm execution | No indexer, no multi-chain, no alerts; contract-focused |
| **solana-rug** | Python | Solana only | API calls (Birdeye/Helius) | API-wrapper; Solana-only; no own data |
| **LucianoLupo/rug-scanner** | Python | Base + ETH | Own queries to RPC/explorers | 2 chains only; no ClickHouse indexer; no free-API fallback chain |
| **RugMunchScan** | Python (FastAPI) | EVM + Solana (multi-chain) | Own ClickHouse indexer + 5-tier free fallback + Telegram/webhook alerts + cross-chain entity resolution | — |

---

## The 34+ OSS Scanner Field (categorized)

### Category A — API wrappers (majority of the field)
Thin clients over GoPlus, DexScreener, Honeypot.is, RugCheck, Birdeye, or CoinGecko.
No on-chain index; results are only as fresh/complete as the wrapped API.

- `honeypot.is` clones and GoPlus wrappers (10+ repos)
- DexScreener pair/token wrappers (8+ repos)
- Birdeye / Helius API wrappers (5+ repos)
- CoinGecko / DeFiLlama price wrappers (5+ repos)
- RugCheck-style Solana wrappers (4+ repos)

### Category B — Single-chain tools
Do one chain (or one chain family) well but cannot span EVM + Solana.

- Solana-only: `solana-rug`, RugCheck clones, Birdeye-based scanners
- EVM-only: tokenassayer (revm), hardhat/foundry-based checkers, BSC-focused honeypot checkers

### Category C — Query-based scanners
Execute their own RPC/explorer queries but keep no durable index.

- `LucianoLupo/rug-scanner` — own queries on Base + ETH
- Other foundry/anvil-based one-shot scanners

---

## Capability Comparison

| Capability | tokenassayer | solana-rug | LucianoLupo/rug-scanner | **RugMunchScan** |
|---|---|---|---|---|
| Own data index (source of truth) | ❌ | ❌ | ❌ | ✅ lite indexer v1.3.0 (ClickHouse) |
| Multi-chain (EVM + Solana) | ❌ EVM only | ❌ Solana only | ❌ Base + ETH | ✅ EVM + Solana |
| Free API fallback chain | ❌ | ❌ | ❌ | ✅ 5-tier (own → free → paid) |
| Wallet-to-wallet flow (C1) | ❌ | ❌ | ❌ | ✅ wallet_flow |
| Holder clustering (C2) | ❌ | ❌ | ❌ | ✅ holder_clusters |
| New-token early warning (C3) | ❌ | ❌ | ❌ | ✅ new_token_radar |
| Cross-chain entity resolution (C4) | ❌ | ❌ | ❌ | ✅ cross_chain_entity (v1: same-deployer + same-funder) |
| Hype score (C5) | ❌ | ❌ | ❌ | ✅ hype_score |
| Time-based attack detection (C6) | ❌ | ❌ | ❌ | ✅ attack_detector |
| Deployer fingerprint (C7) | ❌ | ❌ | ❌ | ✅ deployer_fingerprint |
| Address-first wallet projections (v1.3.0) | ❌ | ❌ | ❌ | ✅ wallet_out_proj / wallet_in_proj |
| Token metadata reference (v1.3.0) | ❌ | ❌ | ❌ | ✅ token_meta_ref (one row per token) |
| Incremental holder balance cache (v1.3.0) | ❌ | ❌ | ❌ | ✅ holder_balances + auto MV |
| Telegram + webhook alerts | ❌ | ❌ | ❌ | ✅ Telegram bot + WebhookEngine |
| 10+ own-data engines (holders, LP lock, whales, wash, etc.) | ❌ | ❌ | ❌ | ✅ |

---

## Why the Field Stays API-Wrapper-Heavy

1. **Indexing is expensive** — running a multi-chain indexer needs RPC budget, storage
   (ClickHouse), and ops. Most OSS projects ship a weekend tool instead.
2. **Free APIs are good enough for a demo** — GoPlus/DexScreener answer "is this a
   honeypot?" quickly; the field stops there.
3. **No fallback thinking** — wrappers break when the API rate-limits or changes; they
   have no own-data safety net.
4. **Single-chain is simpler** — Solana or EVM alone avoids cross-chain schema work.

RugMunchScan inverts this: the indexer is the product, APIs are the safety net, and the
5-tier fallback chain guarantees answers even when every free API is down.

---

## Position Statement

RugMunchScan's moat is **data ownership**:

- Every signal (holder, LP, deployer, whale, wash, flow, cluster, hype, attack,
  cross-chain entity link) is recomputable from `dex_events` / `evm_transfers` /
  `deployer_events` in ClickHouse.
- v1.3.0 indexer features (address-first projections, `token_meta_ref`, incremental
  balance cache) keep wallet queries off full-table scans — competitors that wrap
  free APIs cannot offer this.
- Free APIs enrich and fall back — they never gate a scan result.
- No OSS competitor can match the combination of own indexer + multi-chain + free
  fallback chain + alerting + cross-chain entity resolution.

> **"Own your data. Our indexer is the source of truth; free APIs are the safety net."**

---
Last updated: 2026-08-03
