# DataBus Architecture — Where Data Comes From, and When

> Audience: agents + engineers touching scanner, indexer, or data plumbing.
> Goal: zero confusion about which layer to use for what.

## The Three Data Layers (and the rule for choosing)

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — DIRECT PROVIDER CALLS (in-process, per-feature)      │
│  app/domains/databus/providers/chains/*.py                      │
│  Free public APIs + keyed freemium. Used when ONE feature needs │
│  ONE specific source with custom logic (parsing, fallbacks).    │
│  Examples: dexscreener.py, free_security.py (GoPlus), vybe.     │
└──────────────────────────┬──────────────────────────────────────┘
                           │ shared via
┌──────────────────────────▼──────────────────────────────────────┐
│  LAYER 2 — DATABUS CORE (app/databus/, 89 modules)              │
│  Aggregation, caching, key vault, rate-limit pools, rotation,   │
│  access control, quality scoring. The "smart middle".           │
│  Used when: multiple callers need the same data, or when key    │
│  management/rotation matters (vault-backed ManagedKeys).        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ exposed via
┌──────────────────────────▼──────────────────────────────────────┐
│  LAYER 3 — GATEWAY HTTP API (/api/v1/databus/...)               │
│  Cross-service access: Telegram bot, external tools, x402 paid  │
│  tier. The ONLY path for processes outside this backend.        │
└─────────────────────────────────────────────────────────────────┘
```

**The rule**: in-process code uses Layer 1 or 2 directly; anything
crossing a process boundary uses Layer 3. Never call Layer-1 provider
modules from the bot or indexer.

## Who Uses What (authoritative map)

| Consumer | Data path | Notes |
|----------|-----------|-------|
| **Scanner engines** (`app/domains/scanner/engines/`) | Layer 1 direct (GoPlus, honeypot.is, DexScreener) + `_indexer_port` → ClickHouse | Indexer access ONLY via `_indexer_port` — never import the provider module |
| **Bot scanner** (vendored copies) | Same Layer-1 logic vendored + DataBus gateway as final rung (`tools/databus.py`) | Vendored files frozen; refresh via `scripts/refresh-vendors.sh` |
| **lite-chain-indexer** (standalone daemon) | Own RPC pools only — NO DataBus dependency by design | Writes ClickHouse; backend reads it back. One-way data flow |
| **RAG / RugBrain** | Layer 2 (embedders, Qdrant) | Separate concern from market data |
| **External/x402 clients** | Layer 3 gateway with payment gating | `/api/v1/databus/{chain}/{endpoint}` |

## Invariants (enforced by tests where possible)

1. **Free-first always**: registry ordering = free publics before keyed
   templates (tested: `test_registry_integrity`, wave35).
2. **Indexer one-way flow**: indexer writes CH ← backend reads CH.
   The backend NEVER writes indexer tables; the indexer NEVER calls
   backend APIs.
3. **Scanner↔indexer via port only**: `engines/_indexer_port.py` is the
   single seam (post repo-split it becomes an HTTP adapter).
4. **Keys live in gopass** → vault.py runtime acquisition → ManagedKey
   pools. No plaintext env vars for provider keys.
5. **Fail-open everywhere**: any provider dying degrades that field,
   never the scan.

## Adding a New Data Source — decision tree

```
Is it RPC endpoint data?            → lite-chain-indexer registries
Is it used by ONE feature only?     → Layer 1 provider module
Do multiple features need it?       → Layer 2 databus module + vault entry
Does an EXTERNAL process need it?   → Layer 3 gateway route
Is it Solana holder/wallet intel?   → check Vybe/GMGN providers first
```

## Vault Provider Map (gopass paths)

See `app/databus/vault.py` `VAULT_PROVIDERS` — currently 20 keys across
16 providers (etherscan, solscan, alchemy, helius×3, ankr, drpc,
chainstack, getblock, nownodes, vybe, birdeye, moralis×3, quicknode,
coingecko_pro, coinmarketcap, arkham). Adding a provider = add gopass
entry + VAULT_PROVIDERS row; rotation is automatic.
