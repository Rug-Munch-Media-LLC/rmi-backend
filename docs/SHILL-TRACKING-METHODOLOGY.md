# SHILL-TRACKING-METHODOLOGY.md

> Canonical methodology for the **Bad-Boy List** (KOL blacklist) and shill-tracking pipeline.
> Status: design (greenfield) · Owner: RugMunch intelligence · Last updated: 2026-08-13
> **Grounding note:** The originally-referenced files (`app/databus/known_scammers.json`,
> `docs/KOL-CALL-TRACKING.md`, `docs/KOL-TRACKING-DESIGN.md`, `app/databus/shill_detector.py`)
> do not exist. This doc is the canonical design and is written against the modules that DO
> exist: `app/auto_labeler.py`, `app/domains/labels/{models,federated}.py`,
> `app/domains/labels/sources/*`, `app/webhooks/providers/*`, `import_wallet_labels.py`,
> `app/feed_pipeline/providers_twitter.py`, `app/webhooks/models.py`.

---

## 1. The Bad-Boy List — Curation Process

### 1.1 What goes on the list
A **KOL/entity** (Twitter/X identity, Telegram channel, or wallet cluster) whose promotion of
tokens correlates with retail losses, pump-and-dump behavior, or known scam involvement.

### 1.2 Evidence standards (minimum bar per tier)
Every entry must carry an evidence record: `evidence_id`, `source`, `urls` (tweet IDs,
tx hashes, chain explorers), `captured_at`, `captured_by`, and `confidence`. No entry without
a replayable evidence link is admitted.

| Evidence class | Examples | Admissible? |
|---|---|---|
| On-chain (immutable) | deploy→drain within 24h, LP removal, funding to scam deployer, attributed wallet selling into a call | **Yes — strongest** |
| Archived social (tweet snapshot, pinned bio) | signed/archived screenshot, own-ingestion capture, Wayback | Yes |
| Live social (no archive) | a tweet that still exists | Yes, but must be archived at ingestion |
| Hearsay / chat screenshots | third-party claims, unverifiable DMs | **No** (can seed a *watch* entry, not a listing) |

### 1.3 Severity tiers (maps to `LABEL_CATEGORY_RISK_LEVELS` + `AUTO_LABELS` risk_score)

| Tier | Risk band | Definition | Example triggers |
|---|---|---|---|
| **T0 — Malicious** | 85–100 | Proven loss causation or scam behavior | repeat deploy+drain (auto: `rapid_deploy_drain` 90), funds known scam deployer (`scam_funding_source` 85), LP rug (`liquidity_removed`), sanctioned/mixer adjacency |
| **T1 — Serial Shiller** | 60–84 | Repeated calls to tokens that dump, no wallet proof | ≥3 calls, ≥60% dump within 72h, deleted/renamed after calls |
| **T2 — Watch** | 30–59 | Pattern forming, insufficient evidence to list | 1–2 calls with dump, syndicate co-occurrence rising |
| **T3 — Cleared** | <30 | Investigated, no action / stale & unconfirmed | auto-expiry of watch entries after 90d without confirmation |

T0/T1 are "listed" (surface in alerts); T2 is watch-only; T3 is purged from the list but
retained in the audit log.

### 1.4 Keeping it accurate + updated
- **Nominate → Verify → Tier → Publish** workflow; T0 requires two independent evidence
  classes or one on-chain class; T1 requires archived tweet(s).
- **Quarterly re-review** (or on-trigger): every listed entry is re-scored against fresh
  calls. A KOL with 12 months of clean behavior is **demoted one tier** unless a new event lands.
- **Appeals**: subjects may file via a stable endpoint; appeals trigger re-review with the
  original evidence retained in the audit trail (never deleted).
- **Watch expiry**: T2 entries auto-expire after 90d without confirmation; re-nomination
  requires new evidence, not the same stale links.

## 2. Account-Switch / Deleted-Tweet Tracking

**Problem:** a KOL renames their handle or deletes shill tweets to erase the paper trail.
**Principle:** bind identity to an *immutable U-ID*, never a screen name.

### 2.1 Identity binding
- On first observation, mint a `kol_uid` and bind: original screen name, display name hash,
  avatar hash, bio hash, follower-count snapshot, and *handle-history ledger*.
- Renames are detected by comparing profile-hash deltas on every poll; the U-ID persists
  across renames; a `handle_history` array records `(old_handle, new_handle, detected_at)`.

### 2.2 Tweet archival (defense against deletion)
- Every shill-candidate tweet is **archived at ingestion** (own ingestion into ClickHouse +
  pushed to Wayback/archive.today best-effort), storing: full text, CAs/contract addresses,
  media hash, timestamps, engagement counts, and the raw API payload.
- The archive is **write-once**: shill-candidate rows are never updated or deleted; a delete
  event creates a *tombstone* row (`tweet_id`, `deleted_at`, `detected_by`) that supersedes
  but never erases the original.

### 2.3 Delete/rename detection
- **Delete:** scheduled diff — if a tracked tweet_id 404s, emit `kol.tweet_deleted` with the
  archived copy attached. **Deletion after a pump is itself a T1-tiering signal.**
- **Rename/account-switch:** profile-hash change + follower drift (account with 100k followers
  suddenly has 10k = switched/abandoned) triggers `kol.identity_changed`; the ledger keeps the
  old handle searchable. A KOL who renames within 48h of a call dump gets an automatic
  suspicion flag.

## 3. Wallet-Attribution Evidence Ladder

Attribution confidence is graded by an **evidence ladder**. Never treat a lower rung as
sufficient for T0.

| Rung | Evidence | Confidence | Method |
|---|---|---|---|
| **L1 Self-disclosed** | CA/address in pinned tweet, bio, ENS/CNS domain owned by the handle, signed message | 0.9–1.0 | regex extract + ownership proof |
| **L2 Public tags** | Etherscan labels, MetaSleuth, Chainbase, Solana label CSVs | 0.6–0.8 | federated lookup (`domains/labels/federated.py`) |
| **L3 Verified lists** | `wallet_memory.wallet_labels` high-value categories (sanctioned, scam, exploiter, mixer…) via `import_wallet_labels.py`; CryptoScamDB/ChainAbuse | 0.7–0.9 | CH → Redis hot cache |
| **L4 On-chain inference** | funding flow: wallet funded the deployer (`creator_funded`), funded a known scam deployer (`scam_funding_source`), shares factory with KOL wallet (sybil detection), timed transfers from KOL wallet to fresh wallets pre-call | 0.5–0.75 | `auto_labeler` + transfer graph |

- L1 is the only rung that can *independently* place a KOL on-chain; L2–L4 are corroboration.
- Conflicts resolve via `LabelMergeStrategy`: `SOURCE_PRIORITY` (INTERNAL=5 > RMI_SCANNER=10
  > ETHERSCAN_MALICIOUS=15 …) then `HIGHEST_CONFIDENCE`, then `LATEST_VERIFIED`.
- Attribution is stored as *probability + evidence links*, never a bare "KOL owns wallet X".

## 4. Shill-Detection Pipeline

Pipeline stages, each emitting structured events into the existing webhook/event registry:

```
1. CALL       2. OUTCOME        3. WALLET SELL        4. ALERT
capture  →    measure dump  →   attributed sell  →   emit + escalate
```

1. **Call capture** (`kol.call`): detect CA/cashtag/pump-language in ingested tweets
   (`feed_pipeline` + `news_pipeline/helpers.py` keyword categorization). Archive tweet,
   extract `contract_address`, mint `kol_uid`, record `called_at`.
2. **Outcome tracking** (`kol.call_outcome`): at **+15m / +1h / +24h / +72h** snapshots,
   price & mcap vs. call-time baseline (DEX price feed). Dump = peak-to-trough decline ≥40%
   within 72h with peak occurring after the call.
3. **Wallet sell detection** (`kol.wallet_sell`): if attribution ≥ L1/L3, join attributed
   wallet transfers against the token; a sell >10% of the KOL's call-era bag within 72h
   post-call = `pump_dump` signal (reuse `rapid_deploy_drain` 24h-window logic).
4. **Alert** (`kol.shill_alert`): tier-gated — T0 → immediate high-priority alert;
   T1 → digest; T2 → watch queue. Alert payload links call tweet archive, outcome curve,
   wallet tx evidence, current tier.

Escalation rule: **call → dump → attributed sell** in sequence is an automatic T1 listing
and a T0 nomination.

## 5. Syndicate Detection (the Solana Syndicate Pattern)

**Definition:** a persistent cluster of KOLs who repeatedly shill the same tokens in
coordinated windows, typically with shared funding/deployer infrastructure.

### 5.1 Co-shill graph construction
- Build a bipartite graph **KOL × token** (edge = call within 48h of token launch).
- Project to **KOL–KOL** edges weighted by Jaccard (shared tokens / union) and PMI
  (co-occurrence vs. chance). Weight decays with age of the co-occurrence window.
- **Repeated-pair rule:** two KOLs co-shilling ≥3 distinct tokens within 30 days = candidate
  pair; a connected component of ≥3 such pairs = candidate syndicate.

### 5.2 Coordination signals (raise weight)
- **Same launch window:** calls land within minutes/hours of each other and of `token.deployed`
  (`pool_created`, `first_swap` events).
- **Shared bag:** attributed wallets (L1–L4) buy in a single funding cluster; deployer funded
  by the same funding source (`creator_funded` / `scam_funding_source` fan-in).
- **Shared deployer/factory:** tokens share a factory contract (existing `sybil_deployer`
  detection) or the same deployer wallet.
- **Simultaneous exit:** attributed wallets sell in near-identical windows post-dump.

### 5.3 Output & guardrails
- Emit `kol.syndicate_detected` only above a **weight threshold**; below it, keep as
  `kol.syndicate_candidate` in watch mode.
- **Manual review gate before public naming** — automated syndicate flags are internal
  intelligence; publication requires a human-confirmed evidence packet (graph edges, tx hashes,
  archived tweets).
- Every listed syndicate gets a `syndicate_id`; KOLs in it are cross-referenced to the
  Bad-Boy List (mutual reinforcement on both sides).

## 6. Data Retention + Accuracy Review

### 6.1 Retention tiers
| Tier | Data | Policy |
|---|---|---|
| **Archive** (write-once) | shill tweet snapshots, evidence links, tombstones | Indefinite (append-only), per-tweet record never mutated |
| **Operational** | call/outcome/sell events, alerts | 2 years hot (ClickHouse), then cold storage |
| **Ephemeral** | profile hashes, engagement snapshots, watch-only candidates | 90 days, then purge unless promoted |
| **PII/identity** | handle ledger, wallet attribution | Access-controlled; only used internally; never published |

### 6.2 Accuracy review cadence
- **Weekly:** automated re-score of T2 watch entries; purge or promote.
- **Monthly:** syndicate graph re-computation (weights decay); stale edges dropped.
- **Quarterly:** full Bad-Boy List re-review (demote/clear per §1.4); appeals processed.
- **On-event:** any new on-chain evidence re-triggers review of the linked entry.

### 6.3 Audit + appeals
- Every mutation (add/demote/clear/purge) writes an immutable audit row: `who, what,
  evidence_id, when, reason`. Nothing is ever hard-deleted from the audit log.
- Monthly false-positive review: sample cleared/expired entries, measure
  precision (listed entities that had a *real* dumps + attributed sells), tune thresholds.

---

## Implementation mapping (existing modules)
| Need | Module |
|---|---|
| Behavioral risk labels (deploy/drain, funding source, sybil) | `app/auto_labeler.py` |
| Federated label sources + priority + merge | `app/domains/labels/{models,federated}.py`, `sources/*` |
| Scam-address cross-reference | `app/webhooks/providers/scam_interaction.py` |
| Rug-prep / LP-removal signals | `app/webhooks/providers/{creator_funded,liquidity_removed}.py` |
| High-value wallet labels hot cache | `import_wallet_labels.py` |
| Tweet ingestion / archiving | `app/feed_pipeline/providers_twitter.py` + UnifiedPost |
| Event registry for new `kol.*` events | `app/webhooks/models.py` |

---

## [REVIEW] Flags
- **[REVIEW] Identity registry does not exist yet** — `kol_uid` minting, handle-history
  ledger, and profile-hash polling are net-new components; no current module tracks handles
  across renames. L1 self-disclosure via ENS ownership proof is also unbuilt.
- **[REVIEW] Tweet deletion detection is speculative** — `providers_twitter.py` is a fixture
  stub; real streaming + archive.push-to-Wayback and tombstone logic must be validated against
  the production ingest path before the T1 "deleted-after-pump" rule is enforced.
- **[REVIEW] Wallet-attribution rungs are partially built** — L2/L3 exist
  (federated labels + CH `wallet_labels`); L1 (self-disclosed + ownership proof) and L4
  (KOL-wallet funding graph) are design-only. T0 listings should be withheld until L1/L4
  land or two independent on-chain classes exist.
- **[REVIEW] Syndicate thresholds are initial guesses** — "3 distinct tokens / 30 days",
  ±40% dump, 10% bag-sell need calibration against labeled historical data; do not hard-code
  into alerting until precision is measured.
- **[REVIEW] Risk-band boundaries** (85/60/30) are aligned to `AUTO_LABELS`/`LABEL_CATEGORY_RISK_LEVELS`
  but should be re-validated after the first quarterly review cycle.
