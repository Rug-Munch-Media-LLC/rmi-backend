# SCOUNDREL-ENUMERATION.md

> Canonical methodology for **exhaustive scoundrel enumeration**: systematically finding
> EVERY scoundrel KOL and EVERY wallet in their orbit (funding sources, deployers,
> co-shillers) — and keeping the set complete as new scoundrels appear.
> Status: design (greenfield) · Owner: RugMunch intelligence · Last updated: 2026-08-13
> **Grounding note:** The referenced artifacts `app/databus/known_scammers.json`,
> `kol_rug_history.json`, and `docs/KOL-CALL-TRACKING.md` do not exist. This doc is the
> canonical design and is written against modules that DO exist: `app/auto_labeler.py`
> (`repeat_deployer_*`, `scam_funding_source`, `rapid_deploy_drain`, `sybil_deployer`),
> `app/domains/labels/{models,federated}.py` + `sources/*` (chainabuse, cryptoscamdb,
> metasleuth, etherscan_malicious, solana_labels_csv …), `import_wallet_labels.py`,
> `app/webhooks/providers/{scam_interaction,creator_funded,liquidity_removed}.py`,
> and the tiers/ladder defined in `docs/SHILL-TRACKING-METHODOLOGY.md` (T0–T3, L1–L4,
> Bad-Boy List, syndicate detection). No code in this doc is committed yet.

---

## 0. Goal & Definition of "Exhaustive"

**Scoundrel** = T0 (Malicious) or T1 (Serial Shiller) KOL on the Bad-Boy List, plus any
T2 (Watch) entity with ≥1 corroborated wallet. **Wallet in orbit** = any address
attributable to a scoundrel (L1–L4 ladder) or to their **syndicate** (shared funding
source, shared deployer, repeated co-shill).

Enumeration is "exhaustive" when **closure** is reached: the expansion loop (§2) runs
until its BFS frontier is empty (no new unvisited nodes) AND every seed source in §1 has
been drained (no new seeds). Because scoundrels keep minting new wallets and accounts,
exhaustiveness is a per-cycle property, not a permanent one — hence the continuous update
cadence in §5.

State lives in two target artifacts (both to be created, neither exists):
- `app/databus/known_scammers.json` — the scoundrel registry (entity + wallet + evidence
  + tier + syndicate_id). Write-once with tombstone-style supersede, mirroring the
  Bad-Boy audit rules.
- `kol_rug_history.json` — per-KOL call/outcome/rug history used as both seed evidence
  and re-verification input.

## 1. Seed Sources (the discovery frontier)

Seeds are **entity → wallet** or **wallet → entity** pairs that bootstrap the graph. Ranked
by precision/recall trade-off; all tiers feed the same frontier queue.

### Tier A — Self-revealing leaderboards (high recall, low precision, cheap)
KOLs publicly bind handles to wallets and calls. Primary harvest targets:
- **kolscan.io** — KOL profiles with bound wallets, call history, win-rate stats.
- **GMGN KOL tags** — GMGN tags KOLs with wallet addresses + call records.
- **BullX NEO** KOL leaderboards — alpha-call rankings with wallet links.
- **Biteye / Ave.ai / DexScreener top-callers** — secondary KOL ranking surfaces.
- **X/Twitter alpha lists** (LunarCrush etc.) — handles only; wallets must come from the
  evidence ladder, never from a follower count.

### Tier B — Label / entity feeds (medium precision)
Import via `import_wallet_labels.py` → CH `wallet_labels` hot cache, merged through
`domains/labels/federated.py` with `LabelMergeStrategy` (SOURCE_PRIORITY →
HIGHEST_CONFIDENCE → LATEST_VERIFIED):
- **Nansen** (Wallet Profiler, Smart Money, token-holder tags), **Arkham** (entity + exchange
  labels), **Etherscan/Solscan** labels, **MetaSleuth**, **Chainbase**, **OKLink/CoinHolmes**,
  plus the local `sources/*` label CSVs already wired (etherscan_csv, etherscan_malicious,
  solana_labels_csv, mbal, open_labels, internal_labels, rmi_scanner, clickhouse_labels).
- High-value label categories to seed from: sanctioned, scam, exploiter, mixer, phishing,
  deployer_behavior (`repeat_deployer_*`), funding-source labels.

### Tier C — Exposé / curated reports (high precision, low recall — the strongest seeds)
- **ZachXBT** threads and **@dethective** (and similar on-chain sleuths): extract every
  address + named entity + linked KOL handle. Each exposé is a mini seed packet: parse
  addresses, thread timestamps, linked tokens, named perpetrators.
- **Chainabuse** reports (wire already exists in `sources/chainabuse.py`), **ScamSniffer**
  alerts, **CryptoScamDB** (`sources/cryptoscamdb.py`), **De.Fi Rekt** database,
  **RugDoc / Token Sniffer / Honeypot.is** rug-pull listings.
- **Internal organic signals** — reuse outputs of existing detectors as seeds:
  `auto_labeler` labels (rapid_deploy_drain, scam_funding_source, sybil_deployer),
  webhook providers (scam_interaction, creator_funded, liquidity_removed), and any
  `kol.*` events already in the event registry.

**Seed hygiene:** every seed carries `seed_source`, `captured_at`, `captured_by`, and the
raw URL/evidence. Seeds from Tier C may directly nominate T1/T0; Tier A seeds always enter
as T2 watch until the expansion loop corroborates them.

## 2. The Expansion Loop (BFS over the attribution graph)

The core engine. Given a scoundrel seed, walk the graph outward until closure:

```
seed KOL (handle)
  │  L1–L4 attribution ladder (self-disclosure → public tags → verified lists → on-chain inference)
  ▼
KOL wallets ──► inbound funding (fan-in) ──► FUNDING SOURCES (funder wallets)
                                                  │  outbound funding
                                                  ▼
                                          DEPLOYERS funded by the source
                                                  │  creator_funded / pool_created edges
                                                  ▼
                              OTHER KOLs whose wallets were funded by the SAME source/deployer
                                              = SYNDICATE members (see §3)
                                                  │
                                                  └──► enqueue as new seeds, repeat until frontier empty
```

**Step-by-step:**
1. **Attribution:** from a seed handle, resolve wallets via the L1–L4 ladder (per
   SHILL-TRACKING §3). L1 (self-disclosed CA/address in pinned tweet/bio, ENS/CNS ownership
   proof) is the only rung that independently places a KOL on-chain; L2–L4 corroborate.
   Store as probability + evidence links, never a bare claim.
2. **Funding fan-in:** for each attributed wallet, pull inbound transfer graph (existing
   blockchain_indexer / transfer graph). All inbound funders above a dust threshold become
   **funding-source candidates**. Rank by (a) total value sent, (b) number of distinct
   scoundrel wallets they fund, (c) freshness.
3. **Deployer expansion:** from each high-value funding source, follow *outbound* funding
   to every deployer it bankrolled (`creator_funded` edges; reuse `scam_funding_source`
   logic — a wallet that funds known scam deployers is itself a scoundrel-class node).
   Deployers that deployed ≥3 tokens (`repeat_deployer_3`) or drain within 24h
   (`rapid_deploy_drain`) are immediate scoundrel-candidates.
4. **Co-funded KOL expansion (the syndicate hop):** from each funding source / deployer,
   enumerate OTHER KOLs whose attributed wallets received funding from the same source or
   bought into the same deployer's tokens within the launch window. These are the
   **syndicate members** — the highest-value expansion step.
5. **Closure & bookkeeping:** maintain `visited` sets (handle, wallet, funding source,
   deployer) to avoid re-expansion; push new nodes onto the BFS frontier; cap depth
   (default 4 hops) and breadth (fan-in/out per node) with configurable limits. The cycle
   is exhaustive when the frontier drains and §1 seeds are re-drained (see §5).

**Bidirectional enumeration:** also run the loop **wallet-first** — from Tier B/C *scam
wallets*, walk out to KOLs that hold/buy their tokens or receive their funding, then
expand as above. Wallet-first catches scoundrels whose social layer is unlabelled.

## 3. Syndicate Clustering

Merge three graphs over the enumerated nodes and take connected components:

| Graph | Edge definition | Built from |
|---|---|---|
| **Shared-funding graph** | wallet_A, wallet_B both funded by common source S (weighted by $ and count) | expansion loop fan-in |
| **Shared-deployer graph** | wallets share a deployer or a token factory (`sybil_deployer` reuse) | expansion loop hop 3–4 |
| **Co-shill graph** | KOLs calling the same tokens within 48h of launch (Jaccard/PMI, decayed) | SHILL-TRACKING §5.1 |

- Union the three graphs; a connected component is a **candidate syndicate**. The
  **repeated-pair rule** from SHILL-TRACKING applies (≥3 distinct co-shilled tokens / 30d
  or shared funder) to filter noise.
- **Hub scoring:** a funding source that bankrolls ≥N scoundrel deployers, or a deployer
  behind ≥N KOL wallets, is a **hub node** — it defines the syndicate's center of mass and
  gets its own entry (funder/deployer as entity) in `known_scammers.json`.
- **Coordination signals** (raise cluster weight): same launch window, shared bag,
  simultaneous exits — per SHILL-TRACKING §5.2.
- **Output & guardrail:** emit `kol.syndicate_detected` only above weight threshold;
  below it, `kol.syndicate_candidate` watch. Every cluster gets a `syndicate_id`; members
  cross-reference the Bad-Boy List (mutual reinforcement). Automated clusters are internal
  intelligence — **public naming requires a human-confirmed evidence packet** (graph edges,
  tx hashes, archived tweets), per SHILL-TRACKING §5.3.

## 4. Re-Verification & False-Positive Control

Enumeration is high-recall by design; every enumerated node must pass the same evidence
bar as the Bad-Boy List before it surfaces:

1. **Tier gating:** seeds and expansion hits land as T2 watch by default. T1 requires
   archived tweet(s) + outcome data (`kol.call_outcome` dumps ≥40% within 72h). T0 requires
   **two independent evidence classes OR one immutable on-chain class** (deploy→drain, LP
   removal, funding to known scam deployer, attributed sell into a call). No hearsay —
   chat screenshots seed a watch entry only.
2. **Label conflict resolution:** a wallet with contradicting labels (e.g. "exchange" tag
   vs "scam") resolves via `LabelMergeStrategy`: SOURCE_PRIORITY (INTERNAL=5 > RMI_SCANNER=10
   > ETHERSCAN_MALICIOUS=15 …) → HIGHEST_CONFIDENCE → LATEST_VERIFIED. A conflict that
   survives resolution demotes the node to watch until re-verified.
3. **Cluster dedup / sybil control:** expansion often finds the same human behind many
   wallets. Wallet-cluster fingerprints (same funding source fan-out, same factory, same
   gas funding pattern) collapse N wallets into one *entity node* — prevents double-counting
   inflation of syndicate size.
4. **Human-in-the-loop gates:** automated T0/T1 listings and syndicate publications require
   a manual review packet (archived tweets, tx hashes, graph edges) before they surface in
   alerts or feeds. Audit-log every mutation (add/demote/clear) — nothing hard-deleted.
5. **Precision measurement:** monthly false-positive review — sample cleared/expired/watch
   entries, measure precision (listed entities that had real dumps + attributed sells),
   tune expansion depth, fan-in thresholds, and cluster weights. Watch entries expire after
   90d without confirmation; re-nomination requires new evidence.
6. **Demotion rule:** 12 months of clean behavior demotes one tier unless a new event
   lands (quarterly re-review).

## 5. Continuous Update Cadence (keep it exhaustive)

Enumeration rots fast: handles rename, wallets rotate, syndicates spin up new deployers.
Fixed cadence:

| Job | Cadence | Action |
|---|---|---|
| **Leaderboard re-scrape** (kolscan, GMGN, BullX, Biteye, Ave) | **Daily** | Pull top-KOL lists; diff against registry; new/rising handles → T2 seeds → expansion loop |
| **Label-feed ingest** (Nansen, Arkham, Chainabuse, ScamSniffer, CryptoScamDB, local label CSVs) | **Daily** | Incremental import via `import_wallet_labels.py`; new scam/sanctioned/mixer labels → wallet-first expansion |
| **Exposé sweep** (ZachXBT, @dethective, sleuth feeds) | **On-post + daily backfill** | Monitor target accounts; parse addresses/entities from each thread into seed packets |
| **Internal signal drain** (auto_labeler, webhook providers) | **On-event** | New `rapid_deploy_drain` / `scam_funding_source` / `sybil_deployer` / `liquidity_removed` → enqueue seeds |
| **Expansion loop** | **Daily incremental; weekly full closure** | Re-run BFS from all new seeds; weekly run from ALL seeds to catch missed hops and verify closure |
| **Syndicate graph recompute** | **Monthly** | Rebuild §3 graphs, decay old co-shill weights, drop stale edges |
| **Bad-Boy List re-review** | **Quarterly** | Demote/clear stale entries; appeals processed (per SHILL-TRACKING §6.2) |
| **Precision audit** | **Monthly** | False-positive sampling + threshold tuning (§4.5) |

**Closure check per cycle:** after each weekly full run, assert frontier-empty AND
seed-drained; if either fails, the cycle is not exhaustive and continues next run. A daily
"no new nodes found" streak over ≥2 weeks against live leaderboards is the empirical
signal that the current seed set is close to complete.

## 6. Outputs & Storage

- `app/databus/known_scammers.json` — scoundrel registry: `{kol_uid, handles[], wallets[],
  tier, risk_score, syndicate_id, evidence[], confidence, last_verified}`. Append-only
  with supersede rows; mirrors Bad-Boy audit rules (SHILL-TRACKING §6.1 retention: archive
  write-once, PII access-controlled, never published raw).
- `kol_rug_history.json` — per-KOL call/outcome history (feeds re-verification §4 and
  T1/T0 evidence).
- Events: `kol.scoundrel_nominated`, `kol.wallet_attributed`, `kol.funding_source`,
  `kol.syndicate_member`, `kol.syndicate_detected` into the existing event registry.

---

## [REVIEW] Flags
- **[REVIEW] Data files are greenfield** — `app/databus/known_scammers.json` and
  `kol_rug_history.json` do not exist; schema above is proposed. Nothing persists today,
  so the whole enumeration loop is design-only until the registry lands.
- **[REVIEW] No KOL identity registry** — `kol_uid` minting, handle-history ledger, and
  profile-hash polling (SHILL-TRACKING §2.1) are unbuilt; seed dedup across renames
  depends on them.
- **[REVIEW] L1 attribution is unbuilt** — self-disclosure + ENS/CNS ownership proof is
  design-only; until it lands, wallet-attributed seeds rely on L2/L3/L4 and T0 listings
  should be withheld unless two independent on-chain classes exist.
- **[REVIEW] L4 funding-graph expansion assumes transfer-graph data** — the fan-in/fan-out
  walk depends on blockchain_indexer coverage and on `creator_funded`/`scam_funding_source`
  edges being populated at scale; validate graph completeness before trusting closure.
- **[REVIEW] Seed-source access is unproven** — kolscan/GMGN/BullX scraping terms, rate
  limits, and Nansen/Arkham API keys are not yet negotiated; Tier A harvest may need
  rotation through SearXNG/FlareSolverr layers per the frugality stack.
- **[REVIEW] Thresholds are initial guesses** — expansion depth 4, fan-in dust threshold,
  hub "≥N deployers", syndicate weights need calibration against labeled historical data;
  do not hard-code into alerting until monthly precision audit has run.
