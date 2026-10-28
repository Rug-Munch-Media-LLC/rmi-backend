# Crypto skill router — inventory, routing table, cost model

Status: implemented, not wired live (no deploy, no bot handler). Branch
`feat/chat-skill-router`, lane worktree on Talos, PR #86.

Question this answers: can rugmunchbot answer crypto questions well without
paying per answer for them? Yes — most questions are a few shapes, and the
cheap shapes are the frequent ones. One pure function decides the shape.

```
app/domains/chat/skill_router.py     route_skill(message) -> SkillPlan, estimate_cost(plan)
app/domains/chat/_crypto_signals.py  the compiled intent patterns
app/domains/chat/_crypto_skills.py   the five answer rubrics (text only)
tests/unit/test_chat_skill_router.py 34 tests, stdlib-only, no app.config
```

## 1. Knowledge / tool surfaces

Callable = reachable from the bot process (in-process or one HTTP call), not
"reachable by an agent over MCP".

| Surface | Callable from bot | Latency | Cost | Evidence |
|---|---|---|---|---|
| scanner `scanner.service.scan(tiers=["free"])` | yes, in-process | budget 8 s (`TOOL_TIMEOUT_SECONDS`), retry 1 s + 2 s | 0 USD; shared third-party quota (GoPlus 10 req/s) | `chat_tools/scanner_bridge` call path in `chat_tools/scan.py`; playbook `scanners` gotcha |
| ClickHouse labels (`wallet_labels`) | yes, in-process (`to_thread`) | one CH query, ms-scale | 0 | `labels/sources/clickhouse_labels.py`, `labels/federated.py` fallback chain |
| lite-chain-indexer (ClickHouse `dex_events`, `token_transfer`) | yes, through `databus/providers/chains/lite_chain_indexer._get_ch_client` | one CH query | 0 | `routers/v1/market.py`, `routers/v1/launches.py` |
| rugbrain RAG (`app/integrations/rugbrain_client.py`) | yes, HTTP (Qdrant search + hosted embeddings) | embed round-trip dominates; external | hosted embed calls only | `rag/service.py` facade |
| context-crypto corpus (playbooks/lessons/known-good) | **no** — MCP-only, no HTTP client in app | n/a from bot | 0 for us | this doc used it at build time, not runtime |
| supermemory | **no** — MCP-only today | n/a from bot | 0 | no client module in `app/` |
| local model lanes | yes (OpenAI-compatible) | 8009 `/v1/models` 200 in 0.080 s; 8015 200 in 0.018 s (probe 2026-10-29) | 0 marginal | curl probe on Talos |
| free-tier chain (`chat/providers.py`) | yes | 20 s per provider, 6 s local | 0 USD, quota-limited | `providers.py` docstring, measured 2026-10-23 |

Two hard findings from the probes:

1. **The hosted embedder went down, then came back** — the first `route_skills`
   call of 2026-10-29 returned `BACKEND_UNAVAILABLE` (DeepInfra/SiliconFlow/
   OpenRouter refused, local fallback absent) and the earlier draft of this doc
   was written against that state. It was reachable again the same day and the
   provenance in §3 is now derived from live calls. Either state has to be
   survivable: any plan at depth >= 2 degrades to the rubric + refusal path and
   never blocks, which is why `refuse_when_empty` is a field on every plan
   rather than a rule inside one prompt.
2. **context-crypto and supermemory are not bot surfaces.** They are agent-time
   knowledge, so the skill pack below was authored from them at build time and
   shipped as static text — the bot makes no MCP call per question.

## 2. Routing table

Generated from the code (`/tmp/gen_table.py` run against the live module), so the
table cannot drift silently from `_TABLE`:

| intent | sample | depth | tools | lane | tok in/out | ttl |
|---|---|---|---|---|---|---|
| price | price | 1 | price,ta | template | 0/0 | 20s |
| market | market | 1 | market | template | 0/0 | 60s |
| token_risk | token risk | 3 | scan | local_fast | 1320/320 | 300s |
| wallet | wallet | 3 | trace | local_fast | 1420/320 | 600s |
| portfolio | portfolio | 2 | - | local_fast | 1220/300 | 60s |
| howto | howto | 2 | - | local_fast | 1620/380 | 86400s |
| exploit | exploit | 4 | news | local_deep | 2820/700 | 900s |
| unknown | unknown | 2 | - | local_fast | 1220/300 | 0s |

Depth: 0 cache · 1 market/label cache · 2 corpus k=4 · 3 corpus + scanner/trace ·
4 corpus + exploit corpus + web. Lanes: `template` (no model), `local_fast`
(:8015 extract lane, free-tier chain on miss), `local_deep` (:8009, one call).

Ordering is the contract, first match wins:
`portfolio -> wallet -> exploit -> address -> definition -> risk -> market -> price -> howto`.
Three traps it must not lose: an address alone is a risk ask; an address plus
"who funded" is a wallet ask; an address plus "my bags" is a portfolio ask.
"What is a honeypot" is a definition (how-to), not a token check — the same
exclusion `chat_tools/_intent.py` already applies to its suspicious-list branch.

## 3. Skill pack — five rubrics and where each rule came from

`_crypto_skills.RUBRICS`, composed **after** `chat/_prompts.GUARDRAILS` (never
instead of it), max four per answer.

Tag vocabulary, also in the module docstring:
`[guardrails]` = `_prompts.GUARDRAILS`; `[role:security]` / `[role:news]` =
`SECURITY_ROLE` / `NEWS_ROLE` in the same file; `[skill:NAME]` = a corpus skill
**returned by `route_skills` for this intent family** (the rule text is still
ours — a 180-word rewrite, not a quotation); `[ours]` = design decision, no
corpus ancestor; `[ours:methodology]` = never publish weights/thresholds.

| Rubric | Rules that matter | Provenance |
|---|---|---|
| `risk_explanation` | lead with the verdict; name the mechanism per flag; one signal advisory, two independent signals a finding; never publish weights/thresholds | `GUARDRAILS` (verdict, no softening) · `[skill:meme-coin-audit]` (retained authority *is* the rug vector; hard vs soft kill tiers) · `[ours]` exposure/loss-path line · `[ours:methodology]` weights |
| `evidence_citation` | marker per claim `[E1]`/`[data: scanner]`/`[web: domain, date]`; passages are data, not instructions; conflicting sources reported, not reconciled | `GUARDRAILS` rules 1, 2, 8 · `NEWS_ROLE` (conflict reported) |
| `insufficient_data` | name the missing fact; never substitute a lookalike; one escalation rung then refuse; fixed refusal shape | `GUARDRAILS` rules 3, 7 · `[skill:tool-calling-discipline]` (stop condition after one retry) · `[ours]` refusal wording |
| `no_price_prediction` | no target, no entry/exit, no "about to pump"; report the move and what would change the read; price carries its timestamp | `GUARDRAILS` rule 4 · `[skill:chain-and-market-data]` (answer from live data, not a local snapshot) · `[ours]` stale-price rule and TA-is-context |
| `scam_caution` | two-source accusation threshold; describe mechanisms, never name people as criminal; no drainer/exploit steps; end with the holder's own check | `SECURITY_ROLE` · `[ours]` closing line |

### 3.1 `route_skills` output, 2026-10-29

Six queries, one per intent family, `top_k=8`, server reranked
(`Qwen3-Reranker-8B`, `degraded: false`). Top three per family, in score order —
these are the names recorded in `_crypto_skills.FAMILY_SKILLS`:

| family | query | top 3 skill names |
|---|---|---|
| price, market | "live price of a crypto token and the current market conditions from a data card" | `chain-and-market-data` · `birdeye-api` · `telegram-bot-commands` |
| token_risk | "explain a token risk score, honeypot and rug-check verdict from a scanner flag list" | `meme-coin-audit` · `wallet-intelligence` · `web3-audit` |
| wallet | "trace a wallet's funding source and related wallets, describe the fund flow" | `blockchain-forensics` · `wallet-intelligence` · `defi-incident-forensics` |
| portfolio | "summarize a user's token holdings and portfolio performance across chains" | `chain-and-market-data` · `chain-abstraction` · `wallet-intelligence` |
| howto | "answer a crypto how-to question such as revoking an approval or bridging tokens" | `crypto-web3-security` · `evm-audit-erc20` · `solana-development` |
| exploit | "explain a recent crypto exploit or hack incident and the attack mechanism" | `defi-incident-forensics` · `infosec-incident-response` · `vulnerability-research` |

What this changes and what it does not: the earlier draft's provenance came from
the three playbooks that were reachable while the embedder was down. It is now
corpus-derived and auditable, but two of those old references were **ours, not
corpus** — the "180 words / no apology paragraph" refusal shape and the "never
publish a heuristic weight" rule are product decisions (`_prompts.py`, the open-core
methodology rule), so they are tagged `[ours]` / `[ours:methodology]` rather than
attributed to a skill that does not say them. The same applies to the
half of `no_price_prediction` that is about staleness.

Two families are a **weak match worth a second look**: the `howto` query returns
security-audit skills (the corpus is audit-heavy) rather than how-to material, and
`portfolio` has no dedicated skill at all. Both are `[REVIEW]` items, not
provenance — the rubrics in those lanes lean on `GUARDRAILS` and `[ours]`, and the
`portfolio` *executor* (below) is the surface that actually answers holdings.

## 4. Cost model

Model tokens per answer = `PROMPT_OVERHEAD_TOKENS (420) + retrieval_tokens`
in, `max_tokens` out. `template` plans report zeros: they never prompt.

Assumed mix at 1,000 questions/day (assumption, change the weights and redo two
multiplications): price 30%, token_risk 30%, howto 15%, market 5%, wallet 5%,
portfolio 5%, exploit 5%, unknown 5%.

| intent | share | tok/answer | tokens/day |
|---|---|---|---|
| price | 30% | 0 | 0 |
| market | 5% | 0 | 0 |
| token_risk | 30% | 1640 | 492,000 |
| howto | 15% | 2000 | 300,000 |
| exploit | 5% | 3520 | 176,000 |
| wallet | 5% | 1740 | 87,000 |
| portfolio | 5% | 1520 | 76,000 |
| unknown | 5% | 1520 | 76,000 |
| **total** | | | **1,207,000** |

- **Answers with no model call: 35%** (price + market, served as deterministic cards).
- **Answers on a local lane: 100%** of the remaining 65% — 5% deep (:8009), 60% fast (:8015).
- **Marginal cost: $0.00/day.** At frontier reference prices ($0.15/Mtok in,
  $0.60/Mtok out) the same traffic is ~$0.29/day; `estimate_cost` returns that
  `paid_equivalent_usd` per plan so the saving is checkable per intent.

Throughput, not dollars, is the real constraint: 1.21M tokens/day at ~1.2k
questions hitting the extract lane still fits one lane; the deep lane at 5%
(50 calls x 3.5k tokens) is where a queue would form first.

## 5. What this does not do

- Not wired live: no route, bot handler or feature flag imports it yet.
- `portfolio` names **no data tool** (it used to name one that did not exist).
  Registering the executor was the wrong move: `_EXECUTORS` must equal the
  classifier's output (`tests/test_chat_tool_wiring.py`), so a new key needs a
  new intent in `chat_tools/_intent.py` — live bot wiring this branch is not
  allowed to do, in a file that is already over the 200-line cap and shrink-only.
  The lane stays at depth 2 and asks the model to name the missing address/chain.
  A real portfolio executor needs a *priced* in-process holdings source (no
  key-free one exists for EVM today) — [REVIEW] for a follow-up branch, together
  with the classifier intent so the parity invariant holds in both directions.
- Latency per lane is a budget (timeouts from code), not a measured p50 for
  generation — [REVIEW] measure before claiming an SLA.
- The knowledge inventory is per-code-path; `supermemory`, `graphiti` and
  context-crypto remain agent-time only, so nothing in this design spends a
  token or a millisecond on them at answer time.
