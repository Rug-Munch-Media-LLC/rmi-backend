# RAG Crypto Docs Deep-Dive — Documentation & Knowledge Sources

> Research task output (2026-08-14). Prioritized list of public, free, stable
> documentation / knowledge sources that give the RugMunch RAG a deep
> understanding of crypto coding, Web3 security protocols, and scam patterns.
> The top 5 are wired into `app/rag/crawl_sources.json` (`incident_sources`,
> category `education` → `coding_knowledge` + `threat_intel`). Every entry is
> unauthenticated; entries that need a parser are wired **disabled** until a
> markdown/RST/AsciiDoc ingest path exists. `[REVIEW]` = licensing/stability
> not fully verified from first-hand knowledge — confirm before production
> ingestion.

Priority legend:

- **P0** — wired now into `crawl_sources.json` (schema-valid, verified raw URL).
- **P1** — high value, build a small ingest path and enable.
- **P2** — useful, but lower signal or heavier parsing; schedule later.

Current coverage (do **not** duplicate): DefiLlama hacks, REKT leaderboard,
SlowMist Hacked, CertiK alerts, MetaMask eth-phishing-detect, DeFiHackLabs
incidents, ethereum-labels (all in `incident_sources`), plus DataBus providers
(GoPlus, Birdeye, DexScreener, CoinGecko, Helius) and the deprecated
`github_rag_feeder` (DeFiHackLabs clone, awesome-audits-checklists, etc.).

---

## P0 — Wired into crawl_sources.json (disabled until ingest path exists)

All five are raw-markdown / RST / AsciiDoc files fetched from
`raw.githubusercontent.com` (unauthenticated, no rate limit beyond GitHub
standard). Category `education` maps to `coding_knowledge` (+ `threat_intel`),
a live RAG collection already searched by the chat router
(`app/routers/chat.py` → `RAG_COLLECTIONS`).

| # | Source | What | License | Target collection | Rationale |
|---|--------|------|---------|-------------------|-----------|
| 1 | **`consensys_best_practices`** — [ConsenSysDiligence/smart-contract-best-practices](https://github.com/ConsenSysDiligence/smart-contract-best-practices), `raw.githubusercontent.com/ConsenSysDiligence/smart-contract-best-practices/master/README.md` | Canonical Solidity security best practices: known attacks, development recommendations, secure design. Repo **moved** from `Consensys/` (the old path is a 25-star redirect stub — do not use). Docs tree: `docs/attacks/*.md`, `docs/development-recommendations/*.md`. | `[REVIEW]` — no LICENSE file in repo; historically CC BY-SA 4.0 | `coding_knowledge`, `threat_intel` | The de-facto Solidity security guide. Wired **disabled** until a markdown-doc ingest path exists (README is the index; per-topic pages are P1 tree-crawl). |
| 2 | **`not_so_smart_contracts`** — [crytic/not-so-smart-contracts](https://github.com/crytic/not-so-smart-contracts), `raw.githubusercontent.com/crytic/not-so-smart-contracts/master/README.md` | Trail of Bits real vulnerable contract examples with explanations: reentrancy, honeypots, integer overflow, race conditions, unchecked calls. | Apache-2.0 (repo **archived** → read-only, URL stable) | `coding_knowledge`, `threat_intel` | Real code-level attack patterns the KB lacks today. Wired **disabled**; per-vuln `honeypots/`, `reentrancy/` READMEs are P1. |
| 3 | **`smart_contract_vulnerabilities`** — [kadenzipfel/smart-contract-vulnerabilities](https://github.com/kadenzipfel/smart-contract-vulnerabilities), `raw.githubusercontent.com/kadenzipfel/smart-contract-vulnerabilities/master/README.md` | ~30 attack vectors with PoC code + references: reentrancy, tx.origin auth, signature malleability, flash-loan oracle manipulation, DoS. **Renamed** from `smart-contract-attack-vectors`. | MIT | `coding_knowledge`, `threat_intel` | Clean single-vector writeups; the README indexes `references/*.md`. Wired **disabled**. |
| 4 | **`solidity_security_considerations`** — [ethereum/solidity](https://github.com/ethereum/solidity) docs, `raw.githubusercontent.com/ethereum/solidity/develop/docs/security-considerations.rst` | Official Solidity docs "Security Considerations" chapter: pitfalls, recommendations, smart-contract security guidance (source of docs.soliditylang.org). | GPL-3.0 (repo license) `[REVIEW]` — GitHub redirect to `argotorg/solidity` observed, but raw URL serves canonical content and git refs resolve | `coding_knowledge` | Authoritative language-level security guidance. Wired **disabled** (RST ingest). |
| 5 | **`openzeppelin_upgradeable_docs`** — [OpenZeppelin/openzeppelin-contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) docs, `raw.githubusercontent.com/OpenZeppelin/openzeppelin-contracts/master/docs/modules/ROOT/pages/upgradeable.adoc` | "Upgradeable" chapter: proxy patterns, upgrade risks, storage collisions, unsafe upgrade flows — feeds proxy-risk rug detection. | MIT | `coding_knowledge`, `threat_intel` | Direct grounding for the proxy/upgrade red-flag logic in `SCAM_INTEL`/`CONTRACT_AUDIT`. Wired **disabled** (AsciiDoc ingest). |

## P1 — High value; approve an ingest path and enable

| # | Source | What | License | Target collection | Rationale |
|---|--------|------|---------|-------------------|-----------|
| 6 | [OpenZeppelin/ethernaut](https://github.com/OpenZeppelin/ethernaut) — `raw.githubusercontent.com/OpenZeppelin/ethernaut/master/README.md` + `src/contracts/levels/*.sol` | The Ethernaut CTF levels: each level is a deliberately vulnerable contract (reentrancy, delegation, gatekeepers, etc.). | AGPL-3.0 | `coding_knowledge` | Interactive learning ground; the README alone is thin, levels need `.sol` extraction (tree-crawl). |
| 7 | [Secureum Book](https://github.com/secureum/Secureum-Book) — `raw.githubusercontent.com/secureum/Secureum-Book/main/README.md` + gitbook content | The Secureum security curriculum (EVM internals, attack classes, audit approach). | `[REVIEW]` — no LICENSE detected on README check | `coding_knowledge`, `threat_intel` | Best structured audit-curriculum corpus after ConsenSys; content is gitbook/tex-heavy → heavier parser. |
| 8 | [SmartContractSecurity/SWC-registry](https://github.com/SmartContractSecurity/SWC-registry) — `raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/README.md` + `entries/*.md` | SWC (Smart Contract Weakness Classification) registry — the standard weakness taxonomy (reentrancy SWC-107, tx.origin SWC-115, etc.). | MIT (LICENSE present) | `coding_knowledge`, `scam_patterns` | Canonical weakness IDs map 1:1 to audit findings; **note**: registry unmaintained since 2020, still the standard. |
| 9 | [vyperlang/vyper](https://github.com/vyperlang/vyper) docs — e.g. `raw.githubusercontent.com/vyperlang/vyper/master/docs/solidity-differences.rst` | Vyper docs: differences from Solidity, security-relevant language guarantees (no reentrancy by default via checks-effects-interactions, integer overflow reverts). | NOASSERTION `[REVIEW]` (Apache-2.0/MIT intent) | `coding_knowledge` | Covers the Solana/EVM alternative-language gap; RST ingest needed. |
| 10 | [CryptoScamDB](https://github.com/CryptoScamDB/api.cryptoscamdb.org) | Community-reported scam database: domains, addresses, entity names, categories. | `[REVIEW]` — exact dump endpoint/format unverified | `known_scams`, `threat_intel` | Broader community scam corpus; verify the unauthenticated dump before enabling. |

## P2 — Later / optional

| # | Source | What | License | Target collection | Rationale |
|---|--------|------|---------|-------------------|-----------|
| 11 | [fravoll/solidity-patterns](https://github.com/fravoll/solidity-patterns) | Solidity design/security patterns (0.4.x-era, still relevant for pattern vocabulary). | MIT | `coding_knowledge` | Pattern vocabulary; partially superseded by #1/#8. |
| 12 | [ConsenSysDiligence docs tree](https://github.com/ConsenSysDiligence/smart-contract-best-practices/tree/master/docs) | Per-topic best-practice pages (`docs/attacks/*.md`, `docs/development-recommendations/*.md`). | Same as #1 `[REVIEW]` | `coding_knowledge` | Expand #1 from index → full tree once the markdown-doc ingest path exists. |
| 13 | [samczsun / Paradigm research](https://samczsun.com/) + [Immunefi bug-bounty writeups](https://immunefi.com/) | Researcher writeups of novel attack classes. | Content/web `[REVIEW]` | `threat_intel`, `contract_audits` | Highest-signal but scrape-heavy; not raw-markdown friendly. |

---

## Wiring notes

- New sources use the existing `incident_sources` schema (name/description/type/
  url/category/collections/enabled) with **category `education`** added to
  `INCIDENT_CATEGORIES` and mapped to `coding_knowledge` + `threat_intel` in
  `INCIDENT_COLLECTION_BY_CATEGORY` (`app/rag/_crawler_constants.py`).
- `coding_knowledge` is an existing live RAG collection (chat router searches it);
  no new vector collection was created.
- All P0 entries are `type: html_page`, `enabled: false` — the incident loader
  (`ingest_incidents_from_sources`) only ingests `json_api`; raw-doc ingest will
  go through a markdown-doc path (crawler `crawl_and_ingest` or a tree-crawl
  like the old `github_rag_feeder`). Enabling requires that path, not a parser
  per se.
- `INCIDENT_CATEGORY_BY_SOURCE` in `_crawler_constants.py` gained the five new
  source names so the registry↔category invariant test stays green.
- `docs/RAG_SOURCE_DEEP_DIVE.md` remains the incident-data deep-dive; this doc
  covers the documentation/knowledge layer.
