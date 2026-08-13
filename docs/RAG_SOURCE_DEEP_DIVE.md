# RAG Source Deep-Dive — Missing Crypto Datasets & Repos

> Research task output (2026-08-13). Prioritized list of public GitHub repos and
> datasets the RugMunch RAG does **not** yet ingest, with the top 3 wired into
> `app/rag/crawl_sources.json` (`incident_sources`). Every entry is free and
> unauthenticated; entries that need a parser or a new collection are marked
> clearly and left for lead approval. `[REVIEW]` = licensing/stability not fully
> verified from first-hand knowledge — confirm before production ingestion.

Priority legend:

- **P0** — wired now into `crawl_sources.json` (schema-valid, safe URL, no keys).
- **P1** — high value, build a small parser / new collection and enable.
- **P2** — useful, but lower signal or heavy parsing; schedule later.

Current coverage (already in `incident_sources` / DataBus — do **not** duplicate):
DefiLlama hacks, REKT leaderboard, SlowMist Hacked (disabled), CertiK alerts
(disabled), plus DataBus providers (GoPlus, Birdeye, DexScreener, CoinGecko,
Helius) and `sn3ll/KOL-Wallets-BULLX` (KOL wallet labels).

---

## P0 — Wired into crawl_sources.json (disabled until parser exists)

| # | Source | What | License | Target collection | Rationale |
|---|--------|------|---------|-------------------|-----------|
| 1 | **`metamask_eth_phishing_detect`** — [MetaMask/eth-phishing-detect](https://github.com/MetaMask/eth-phishing-detect), `raw.githubusercontent.com/MetaMask/eth-phishing-detect/main/src/config.json` | Canonical phishing-domain blacklist (thousands of domains, plus tolerance/fuzz/lists used by MetaMask wallet protection) | MIT | `known_scams`, `scam_patterns` | The de-facto phishing feed for EVM; stable raw URL, no auth. Normalizer currently expects dict records — feed is a domain-string list, so it is wired **disabled** until a domain-list parser is added. |
| 2 | **`defihacklabs_incidents`** — [DeFiHackLabs/DeFiHackLabs](https://github.com/DeFiHackLabs/DeFiHackLabs), `raw.githubusercontent.com/DeFiHackLabs/DeFiHackLabs/main/docs/Incidents_All.md` | 600+ exploit incidents (project, amount lost, date, chain, technique) in one markdown table; each incident also has a reproduction writeup | `[REVIEW]` — educational repo, LICENSE file not confirmed | `defi_exploits`, `threat_intel` | Highest-value single exploit DB besides DefiLlama hacks; overlaps REKT but adds technique/root-cause detail. Wired **disabled** until a markdown-table parser exists. |
| 3 | **`ethereum_labels_0x406`** — [0x406/ethereum-labels](https://github.com/0x406/ethereum-labels) | Labeled EVM addresses by category: mixers, exchanges, MEV bots, phishing, hackers (per-category CSV files) | MIT `[REVIEW]` | `known_scams`, `threat_intel` | Direct answer for "crypto-hackers" / entity-label DBs. Feeds wallet/entity attribution the RAG lacks today (wallet_labels collection is currently only TA concepts). Wired **disabled** until a CSV parser exists. |

## P1 — High value; approve parser/collection work

| # | Source | What | License | Target collection | Rationale |
|---|--------|------|---------|-------------------|-----------|
| 4 | [CryptoScamDB](https://cryptoscamdb.org/) | Full scam database: reported domains, addresses, entity names, categories (scam/phishing/fraud) | `[REVIEW]` — free tier; exact license/endpoint (API vs CSV dump) unverified | `known_scams`, `threat_intel` | Community-reported scam corpus broader than blacklists. Verify the unauthenticated endpoint before wiring. |
| 5 | [Chainabuse](https://chainabuse.com/) (TRM Labs) | Public scam/abuse reports: addresses, domains, report categories, severity | `[REVIEW]` — API keyless status unverified | `known_scams`, `scam_patterns` | Cross-chain abuse reports incl. Solana. Verify API auth before wiring. |
| 6 | [PhishFort](https://phishfort.com/) blacklist feed | Phishing-domain blacklist (exchange-targeted phishing) | `[REVIEW]` — endpoint/license unverified | `threat_intel`, `known_scams` | Complements eth-phishing-detect; good redundancy for phishing domains. |
| 7 | [OFAC SDN list](https://www.treasury.gov/ofac/downloads/sdn.csv) (public dataset, not GitHub) | Sanctioned entities and their crypto addresses/aliases | US government public domain | `threat_intel`, `known_scams` | Critical for wallet screening and sanctions compliance; CSV needs a parser. |
| 8 | [solana-labs/token-list](https://github.com/solana-labs/token-list) — `raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json` | Official Solana token registry (name/symbol/address/decimals/logo) | MIT | `token_metadata` (new collection) | Ground truth for Solana token identity — prevents fake-token false matches. Category is metadata, not scam/rug/exploit, so it does **not** fit `incident_sources`; needs a new source block + collection. |
| 9 | [MyEtherWallet/ethereum-lists](https://github.com/MyEtherWallet/ethereum-lists) | EVM token metadata + known contracts/networks per chain (JSON per chain) | MIT | `token_metadata` (new collection) | Same as #8 for EVM; also feeds contract-address checks. |
| 10 | [GoPlus Token Security API](https://gopluslabs.io/) (`api.gopluslabs.io/api/v1/token_security/{chain_id}`) | is_honeypot / is_blacklisted / owner permissions / tax per token | Free tier, no key; ToS `[REVIEW]` | `known_scams` (per-token enrichment) | **Already integrated in DataBus** (`_goplus_token_security`) — do **not** duplicate; note for RAG-vs-DataBus routing. |

## P2 — Later / optional

| # | Source | What | License | Target collection | Rationale |
|---|--------|------|---------|-------------------|-----------|
| 11 | [Uniswap/default-token-list](https://github.com/Uniswap/default-token-list) + [tokenlists.org](https://tokenlists.org/) | Aggregated ERC-20 token lists (name/symbol/decimals/logo) | `[REVIEW]` (GPL-3.0 / MIT variants) | `token_metadata` (new collection) | Cross-project token list coverage; duplicates #8/#9 for EVM — only if needed. |
| 12 | [flashbots/mev-inspect-js](https://github.com/flashbots/mev-inspect-js) + [libmev/mev-blocks](https://github.com/libmev/mev-blocks) | MEV bundle/block datasets (sandwich/arbitrage/liquidation patterns) | MIT / `[REVIEW]` | `threat_intel` | MEV-pattern grounding; data is BigQuery/parquet-heavy, not raw JSON — heavy parser. |
| 13 | [RugDoc](https://rugdoc.io/) honeypot/audit list | DeFi audit scores + honeypot warnings | `[REVIEW]` | `known_scams`, `scam_patterns` | Honeypot pattern corpus beyond static KB; site-scrape heavy. |
| 14 | [Honeypot.is](https://honeypot.is/) API | Per-address honeypot classification | `[REVIEW]` | `known_scams` (per-token) | Per-address only — use for token-specific enrichment, not bulk ingest. |
| 15 | Immunefi / Code4rena archives | Vulnerability reports & contest findings | Content/web `[REVIEW]` | `contract_audit`, `threat_intel` | Real vulnerability patterns for audit methodology; webpage scraping needed. |

---

## Wiring notes

- The `incident_sources` schema (`crawl_sources.json`) only accepts
  `category ∈ {rug, exploit, scam}` and collections in
  `{known_scams, scam_patterns, defi_exploits, threat_intel}` — token-metadata
  sources (#8–#11) intentionally **not** wired there; they need a new
  `token_metadata` source block + collection (lead approval).
- New names were added to `INCIDENT_CATEGORY_BY_SOURCE` in
  `app/rag/_crawler_constants.py` so the existing registry↔category invariant
  test stays green (supporting edit; flagged in the task return).
- Parser backlog (small pure helpers, no network): domain-list parser for #1,
  markdown-table parser for #2, CSV parser for #3/#7.
