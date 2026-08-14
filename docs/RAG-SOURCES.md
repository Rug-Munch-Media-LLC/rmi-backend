# RAG Sources — Crypto-Intelligence Knowledge Curation Catalog

> Research task output (2026-08-14). Curated catalog of authoritative,
> license-aware crypto-intelligence sources for the RugMunch RAG knowledge
> base. Feeds the hand-written seed corpus (`app/rag/seed_corpus/*.jsonl`)
> and the crawler (`app/rag/crawl_sources.json`). Every URL was verified
> reachable on 2026-08-14 unless marked `[REVIEW]`.
>
> License policy (conservative):
> - **Ingest verbatim OK** — MIT / Apache-2.0 / CC BY / public domain.
> - **Summarize-only, do not ingest verbatim** — CC BY-SA / CC BY-NC-SA /
>   AGPL / proprietary / no-license-visible. Facts are not copyrightable, so
>   entries distilled *in our own words* are always fine; only copying text
>   is restricted.
> - `[REVIEW]` — license or reachability not 100% verified first-hand;
>   confirm before bulk ingestion.

Seed-corpus categories map to `SEED_CATEGORY_COLLECTIONS` in
`app/rag/ingest_crypto_knowledge.py`: `definition` → phishing_db, `defi` →
phishing_db, `market` → phishing_db, `tokenomics` → phishing_db, `onchain` →
phishing_db, `scam` → scam_intel, `security` → contract_audit, `ta` →
wallet_labels.

Companion docs (do not duplicate): `RAG_SOURCE_DEEP_DIVE.md` and
`RAG_CRYPTO_DOCS_DEEP_DIVE.md` catalog GitHub repos wired into
`crawl_sources.json`. This doc covers the *knowledge references* used to
author seed-corpus entries and future crawler targets.

---

## Quick catalog

| # | Source | URL | License / Ingest policy | RAG category | Approx. content volume |
|---|--------|-----|--------------------------|--------------|------------------------|
| 1 | ethereum.org Developers Docs | https://ethereum.org/en/developers/docs/ | CC BY 4.0 (site content) — **ingest OK with attribution** | definition, onchain, defi, security | Large — ~50+ doc pages (accounts, EVM, gas, MEV, oracles, tokens, bridges, smart-contract security) |
| 2 | Ethereum EIPs / ERCs | https://eips.ethereum.org/ | CC0 1.0 (public domain) — **ingest OK** | definition, tokenomics, onchain | Large — 800+ EIPs; token standards (ERC-20/721/1155/4626/4337) are the key subset |
| 3 | OWASP Smart Contract Top 10 (2023/2025/2026) + OWASP SCS | https://owasp.org/www-project-smart-contract-top-10/ , https://scs.owasp.org/ | CC BY-NC-SA 4.0 — **summarize-only** (non-commercial) | security, defi | Medium — 3 editions × 10 ranked vuln classes + SCWE enumeration + checklists |
| 4 | OpenZeppelin Contracts Docs | https://docs.openzeppelin.com/contracts/ | MIT (library code); docs `[REVIEW]` — **ingest OK for patterns, distilled** | security, definition, tokenomics | Medium-large — ERC impls, access control, governance, upgradeable/proxy patterns, ERC-4626 |
| 5 | SWC Registry (Smart Contract Weakness Classification) | https://swcregistry.io/ | MIT (repo LICENSE per prior internal audit) — **ingest OK, distilled** | security | Medium — 37 weakness entries (SWC-100..136) with CWE parents; **unmaintained since 2020** |
| 6 | Solidity Docs — Security Considerations | https://docs.soliditylang.org/en/latest/security-considerations.html | GPL-3.0 (repo) — **summarize-only** | security, definition | Medium — pitfalls (reentrancy, tx.origin, gas loops, randomness) + recommendations (CEI, fail-safe, peer review) |
| 7 | rekt.news | https://rekt.news/ | "All rights reserved" — **summarize-only** | scam, security, onchain | Large — 440+ incident writeups (page 0 of 44), leaderboard, tags = incident taxonomy |
| 8 | Mastering Bitcoin (bitcoinbook) | https://github.com/bitcoinbook/bitcoinbook | CC BY-SA 4.0 — **summarize-only (SA)** | onchain, market, definition, security | Very large — full book; keys, transactions, blockchain, mining, security, applications |
| 9 | Flashbots MEV Research | https://github.com/flashbots/mev-research | `[REVIEW]` (public research repo) — **distill; no verbatim** | defi, market, onchain | Medium — FRPs, workshops, resources on MEV auction, order flow, PBS, privacy |
| 10 | Bitcoin Wiki | https://en.bitcoin.it/wiki/Main_Page | CC BY 3.0 — **ingest OK with attribution** | onchain, scam, definition | Large — 1,386 pages (privacy, address reuse, dusting, blockchain analysis, wallet security) |
| 11 | Consensys Diligence — Smart Contract Best Practices | https://consensysdiligence.github.io/smart-contract-best-practices/ | `[REVIEW]` — no LICENSE file in repo (historically CC BY-SA) — **summarize-only** | security, defi | Medium-large — canonical attacks + development recommendations; repo moved from Consensys/ (old path redirects) |
| 12 | Trail of Bits Blog | https://blog.trailofbits.com/ | Blog copyright; tools MIT/Apache — **summarize-only for prose** | security, onchain, defi | Large — 100+ blockchain posts (Uniswap v4 hooks, ERC-4337 mistakes, DeFi dimensional analysis) + open-source tools |
| 13 | Immunefi — Web3 Security Playbook / reports | https://immunefi.com/ , https://immunefi.com/whitepapers/web3-security-playbook.pdf | Proprietary — **summarize-only** | security, scam, defi | Medium-large — bug-bounty platform, playbook (custody, governance, infra, monitoring), Crypto Losses reports, Top 10 bugs |
| 14 | Chainalysis Crypto Crime Reports + "Defining the Cluster" | https://www.chainalysis.com/reports/ | Proprietary — **summarize-only** | onchain, scam, market | Large — annual crime reports (2024/2025/2026), cluster methodology, geography reports |
| 15 | DefiLlama | https://defillama.com/ | Data open/free; code `[REVIEW]` — **ingest data facts** | defi, market, tokenomics, onchain | Very large — TVL, yields, hacks, fees, volumes, unlocks; API docs at api-docs.defillama.com |
| 16 | Uniswap Developer Docs | https://developers.uniswap.org/ | `[REVIEW]` — **summarize-only until verified** | defi, definition | Medium-large — v2/v3/v4: concentrated liquidity, pools, fees, hooks |
| 17 | Secureum Bootcamp | https://secureum.substack.com/ | `[REVIEW]` — no LICENSE — **summarize-only** | security, defi | Medium — structured EVM-security curriculum (attack classes, audit checklists) |
| 18 | Damn Vulnerable DeFi / Ethernaut | https://www.damnvulnerabledefi.xyz/ , https://ethernaut.openzeppelin.com/ | `[REVIEW]` (MIT/AGPL) — **distill mechanics only** | security, defi | Medium — CTF levels each illustrating a real attack class |
| 19 | Scam Sniffer Blog | https://blog.scamsniffer.io/ | Proprietary `[REVIEW]` (reachability unverified from this host) — **summarize-only** | scam | Medium — wallet-drainer kits, approval phishing, permit phishing taxonomy |
| 20 | SolidityScan Web3HackHub | https://solidityscan.com/web3hackhub | `[REVIEW]` — **summarize-only** | scam, security | Large — incident DB from 2011 onward; feeds OWASP SC Top 10 methodology |

---

## Detailed notes

### 1. ethereum.org Developers Docs — `definition`, `onchain`, `defi`, `security`
The de-facto protocol-level reference. Key pages for the seed corpus:
- Accounts, transactions, blocks, EVM, gas — foundational `definition`/`onchain`.
- Smart-contract security, formal verification, upgradeable contracts — `security`.
- MEV page (`/developers/docs/mev/`) — the canonical taxonomy of MEV types.
- Oracles, bridges, token standards (ERC-20/721/1155) — `defi`/`tokenomics`.
Site content is CC BY 4.0 (site-wide, per ethereum.org terms) — attribution
required, verbatim ingestion acceptable. HIGH confidence on license.

### 2. Ethereum EIPs / ERCs — `definition`, `tokenomics`
The Ethereum Improvement Proposals repo (ethereum/EIPs) releases EIPs under
CC0 1.0 (public domain). Highest signal: ERC-20, ERC-721, ERC-1155,
ERC-4626 (tokenized vaults), ERC-4337 (account abstraction), plus tokenomics
EIPs (e.g. burn/emission discussions). Permissive — safe to ingest.

### 3. OWASP Smart Contract Top 10 — `security`, `defi`
Three editions (2023, 2025, 2026) of the top-10 smart-contract weaknesses:
access control, business logic, price oracle manipulation, flash-loan
attacks, input validation, unchecked external calls, arithmetic errors,
reentrancy, integer overflow/underflow, proxy & upgradeability. Also:
OWASP SCS SCWE (weakness enumeration), SCS checklist, and the Web3 Attack
Vectors Top 15. **CC BY-NC-SA 4.0 — non-commercial + share-alike: do NOT
ingest verbatim; distill in own words.** Verified live.

### 4. OpenZeppelin Contracts Docs — `security`, `definition`, `tokenomics`
Library docs for the most-used Solidity contracts: ERC-20/721/1155/4626
implementations, access control (Ownable, AccessControl), governance
(Governor, timelock), upgradeable contracts (Transparent/Universal
proxies), utilities (SafeERC20, ReentrancyGuard, Pausable). Library code is
MIT; docs repo `[REVIEW]` — recommended policy: distill patterns, no
verbatim doc text.

### 5. SWC Registry — `security`
37 entries (SWC-100 through SWC-136) covering reentrancy (107), integer
overflow (101), tx.origin (115), delegatecall (112), uninitialized storage
pointer (109), signature malleability (117), DoS with block gas limit (128),
etc. Each maps to a CWE parent. **Caveat: site banner states unmaintained
since 2020** — use for vocabulary/history, cross-check against OWASP
2025/2026 for current ranking. MIT per repo LICENSE (verified in prior
internal audit; inline site does not state it).

### 6. Solidity Docs — Security Considerations — `security`, `definition`
Official language-level guidance. Pitfalls: private information &
randomness, reentrancy, gas limit & loops, sending/receiving Ether,
call-stack depth, authorized proxies, tx.origin, overflow/underflow,
clearing mappings. Recommendations: take warnings seriously, restrict
Ether, keep small & modular, Checks-Effects-Interactions, fail-safe mode,
peer review. Docs live in the GPL-3.0 solc repo — **summarize-only**.

### 7. rekt.news — `scam`, `security`, `onchain`
The best-known incident outlet. 44+ pages of writeups, each tagged with an
incident class: governance attack, signature-verification bypass, validator
compromise, price-feed compromise, NAV manipulation, admin privileges,
bridge hack, firmware/opsec failures. Its leaderboard ranks losses.
**Explicit "all rights reserved" — summarize-only.** Excellent raw material
for rug-pull / exploit taxonomies. Verified live.

### 8. Mastering Bitcoin — `onchain`, `market`, `definition`, `security`
Antonopoulos/Harding, 3rd ed. (2023). Chapters on keys & wallets,
transactions, signatures, fees, the network, blockchain structure, mining,
and security — the clearest prose treatment of UTXO mechanics, address
types, and transaction graph concepts that underpin Bitcoin forensics.
CC BY-SA 4.0 — **summarize-only** (share-alike copyleft). Verified live.

### 9. Flashbots MEV Research — `defi`, `market`, `onchain`
Research hub for Maximal Extractable Value: FRPs (research proposals),
workshops, resources on MEV auctions, builder/relay order flow,
proposer-builder separation (PBS), MEV on L2s, privacy (e.g. encrypted
mempools). Covers sandwich, arbitrage, liquidation MEV. Public repo, no
explicit LICENSE visible — `[REVIEW]`, distill only. Verified live.

### 10. Bitcoin Wiki — `onchain`, `scam`, `definition`
1,386 pages, community-maintained. High-signal pages: Address reuse, Dust
transactions, Mixing, Blockchain analysis, Privacy, Wallet security,
Multisignature, Brain wallet. CC BY 3.0 — safe to ingest with attribution.
Verified live (footer license confirmed).

### 11. Consensys Diligence — Smart Contract Best Practices — `security`, `defi`
The canonical "known attacks + development recommendations" guide:
reentrancy, front-running, integer bugs, DoS, malicious admin, insecure
randomness, etc. Repo **moved** to `ConsenSysDiligence/smart-contract-best-practices`
(old `Consensys/` path is a redirect stub). No LICENSE file — `[REVIEW]`,
**summarize-only** to be safe. Verified live at new URL.

### 12. Trail of Bits Blog — `security`, `onchain`, `defi`
100+ blockchain-category posts. 2026 highlights relevant to RugMunch:
"Building secure Uniswap v4 hooks" (7 recurring failure patterns),
"Six mistakes in ERC-4337 smart accounts", "Spotting issues in DeFi with
dimensional analysis", mutation-testing posts. Their open-source tools
(Slither, Echidna, Manticore) are MIT/Apache but the blog prose is
copyrighted — **summarize-only**. Verified live.

### 13. Immunefi — `security`, `scam`, `defi`
Largest Web3 bug-bounty platform ("92% of Web3 critical vulns reported
here"; $25B hacks prevented claim). Freely downloadable Web3 Security
Playbook (custody, governance, infrastructure, monitoring) and Crypto
Losses reports; Top 10 Bugs page. All proprietary — **summarize-only**.
Verified live.

### 14. Chainalysis — `onchain`, `scam`, `market`
Annual Crypto Crime Report (2024/2025/2026 editions live), Geography of
Cryptocurrency report, and "Defining the Cluster" (cluster methodology —
foundational for wallet-clustering entries). Proprietary — **summarize-only**.
Verified live at /reports/.

### 15. DefiLlama — `defi`, `market`, `tokenomics`, `onchain`
Open, free, keyless data: TVL per chain/protocol, yields, hacks
(dashboard), fees/revenue, DEX volumes, stablecoin mcap, unlocks calendar.
The hacks page is already wired into `crawl_sources.json` (incident
sources). Use the data facts freely; code license `[REVIEW]`. Verified
live.

### 16. Uniswap Developer Docs — `defi`, `definition`
Protocol docs for v2/v3/v4: concentrated liquidity, pool math, fee tiers,
hooks (v4), flash swaps. Redirects from docs.uniswap.org →
developers.uniswap.org. License not stated on landing — `[REVIEW]`,
**summarize-only** until verified.

### 17. Secureum Bootcamp — `security`, `defi`
Structured EVM/audit curriculum (Secureum Book + Bootcamp quizzes): EVM
internals, vulnerability classes, audit checklists, "Secureum A-Z" security
knowledge. Public Substack, no explicit license — `[REVIEW]`,
**summarize-only**.

### 18. Damn Vulnerable DeFi / Ethernaut — `security`, `defi`
CTF platforms where each level is a deliberately vulnerable contract:
reentrancy, flash loans, oracle manipulation, proxy puzzles, etc.
Damn Vulnerable DeFi `[REVIEW]` MIT intent; Ethernaut AGPL-3.0 per prior
audit. Distill attack mechanics only.

### 19. Scam Sniffer Blog — `scam`
Wallet-drainer ecosystem research: drainer kits ("Inferno Drainer" etc.),
approval phishing, permit-signature phishing, address poisoning. Site was
unreachable from this host on 2026-08-14 (`[REVIEW]`); include only
distilled, clearly-attributed facts if reachable later.

### 20. SolidityScan Web3HackHub — `scam`, `security`
Incident database from 2011 onward, used as the empirical basis for the
OWASP SC Top 10 (e.g. 2024: $1.42B across 149 incidents; access control
$953.2M, logic errors $63.8M, reentrancy $35.7M). `[REVIEW]` — summarize
aggregate figures only.

---

## License / ingest decision summary

| Policy | Sources | Action |
|--------|---------|--------|
| **Ingest verbatim OK** | ethereum.org (CC BY 4.0), EIPs (CC0), Bitcoin Wiki (CC BY 3.0), SWC Registry (MIT), OpenZeppelin code/docs (MIT, `[REVIEW]` docs) | Crawl/chunk verbatim with attribution in metadata |
| **Summarize-only** | rekt.news, Chainalysis, Immunefi, Trail of Bits prose, OWASP SC Top 10, Solidity docs, Consensys Diligence, Uniswap docs, Secureum, Scam Sniffer, Web3HackHub, Flashbots, Mastering Bitcoin, Damn Vulnerable DeFi | Distill into seed-corpus entries in own words; never bulk-copy text |
| **[REVIEW] — confirm before use** | Flashbots license, OpenZeppelin docs license, Uniswap docs license, Consensys LICENSE file, Scam Sniffer reachability, Web3HackHub, Secureum, Damn Vulnerable DeFi license | Re-check LICENSE/README before enabling a crawler entry |

Seed-corpus entries derived from these sources are authored in our own
words (facts only), so all 20 sources are usable today for the hand-written
corpus; the license policy above governs *verbatim ingestion* via the
crawler.

## How this was produced
- Research date: 2026-08-14. URLs verified via direct fetch; license
  statements read from the pages themselves where available.
- `websearch` tooling was unavailable in this environment (provider
  unsupported), so verification was performed by direct page fetches and
  prior internal research (`RAG_SOURCE_DEEP_DIVE.md`,
  `RAG_CRYPTO_DOCS_DEEP_DIVE.md`).
- Confidence per source: see `[REVIEW]` flags; everything else HIGH.
