# RugMunchBot + RMIAdminBot — Improvement Plan

**Date:** 2026-08-13 · **Author:** AI planning pass (read-only audit) · **Scope:** feature quality, AI chat, knowledge base

## 0. Audit snapshot (what exists today)

**RugMunchBot** (`app/domains/telegram/rugmunchbot/`)
- Single free-text pipeline: `handlers/chat_ai/__init__.py::chat_ai_response` (shared by free text, `/chat`, `/ask`).
- Context assembly: bot fetches **bridge context** (`http://127.0.0.1:8095/chat/context` from `compose/chat/chat-rag-bridge.py` in fleet-infra) + **scan summary** (`/api/v1/bot/scan`) + **daily market context** (`daily_context.py`) → concatenates into one prompt string.
- LLM routing: `_call_zen_llm` (OpenCode Zen DeepSeek V4 Flash Free, `127.0.0.1:18081`) → fallback `_call_local_llm` (Qwen3-4B :8085, DeepSeek-R1-8B :8086). **The backend `POST /api/v1/chat` auto-RAG path (`CHAT_ENDPOINT`, `ai_chat.py`) is deprecated and NOT called** — module docstrings still describe it.
- Guardrails: prompt-injection block, known-scam block, gibberish gate, weekly per-tier quota (15/75/250/500), flood control, HTML-safe chunking.
- ~70 commands registered (scan family, market, research, trace, holders, watchlist, alerts, defi, prediction, premium).

**RMIAdminBot** (`app/domains/telegram/adminbot/`)
- Every command `@owner_only` (OWNER_IDS allowlist + ADMIN_CHAT_ID). Surface: `/sh` shell (blocklist), terminal mode (allowlist), systemd service control (confirm on stop/restart), docker restart/prune (confirm), deploy, git/push, backups, health, logs, `/ai` + `/oc` (opencode CLI, up to 3 turns). Audit-logged (`admin_audit.log_action`).

## 1. AI-chat quality gaps (a)

| # | Gap | Evidence |
|---|-----|----------|
| A1 | **No user-visible citations.** Bridge returns `sources[]` but `_fetch_bridge_context` discards it; `rag_count` is hardcoded `0` in all three `_llm.py` clients → "⚡ Grounded in N sources" footer never fires with real numbers; no source links/attribution. | `chat_ai/__init__.py:249-305`, `_llm.py:178-184,302-308,432-438` |
| A2 | **Misleading dual-pipeline.** Backend auto-RAG (`/api/v1/chat` with news_v2 grounding + per-user rate limit) documented but dead; live path is bridge→Zen. Docs (`prompts.py`, `ai_chat.py`) describe the old flow. | `ai_chat.py:3-30`, `chat_ai/__init__.py:21-24,652-658` |
| A3 | **Context is blind string concat.** No per-block budget, delimiters, or dedup; bot `daily_context` (market digest) and bridge `market_context` duplicate the same data → wasted tokens + contradictory numbers. Bridge `actions` (scan buttons etc.) dropped. | `chat_ai/__init__.py:644-650`, bridge `_build_context` |
| A4 | **Crude intent routing.** Keyword lists (`_is_deep_question`, bridge `_classify`) — no query transformation (expansion/HyDE), no multi-hop, no intent→tool mapping. | `_helpers.py`, bridge `_classify` |
| A5 | **Rerank broken upstream** → raw ANN top-k quality. `[REVIEW]` in bridge: `rerank=true` hangs. | `chat-rag-bridge.py:774-776` |
| A6 | **History is thin**: SQLite last-N msgs, truncated 300 chars × 4, sent as inert block; no long-term user memory/preferences. | `_llm.py:245-266` |

## 2. Knowledge-base gaps (b)

| # | Gap | Evidence |
|---|-----|----------|
| B1 | **RAG store is Redis-ANN, not Qdrant.** Qdrant is feature-gated behind LlamaIndex (`app/supabase_vector.py` fallback, `app/rag/llama_adapter.py` NOT in production). RAG_MODERNIZATION targets Qdrant + bge-m3. | `RAG_STRATEGY.md §1.3`, `RAG_MODERNIZATION.md` |
| B2 | **3 inconsistent embedders** (nomic-embed-text 768d / bge-m3 1024d / bge-small 384d) across 4 overlapping pipelines (`rag_engine`, `rag_service`, `rag_supreme`, `crypto_embeddings`) → collection dims mismatch, retrieval variance. | `RAG_MODERNIZATION.md` Current State |
| B3 | **Collections bot can't answer from**: wallet_profiles, token_analysis, contract_audits, forensic_reports, market_intel exist but the bot bridge only queries threat-intel-style search (bridge `_rag_search` hits `/api/v1/search` on RugBrain 8012). No wallet/entity questions, no historical exploit attribution with amounts. | bridge `_rag_search`, `RAG_STRATEGY.md` |
| B4 | **Historical ingestion not wired**: Rekt DB / Chainabuse / SlowMist scrapers exist (`rag_historical.py`) but production path incomplete; no content-hash dedup, no structured chunking overlap. | `RAG_MODERNIZATION.md` Gaps 1-2 |
| B5 | **No chat-loop evaluation/feedback**: 50-query golden set exists but no per-answer hallucination/citation check, no "helpful?" feedback from bot users. | `app/rag/evaluation.py`, `rag_feedback.py` |

## 3. Menu / feature completeness gaps (c)
- C1: Bridge `actions` (scan/explore buttons) never surface in AI answers → lost conversion surface.
- C2: No `/settings` (chain filters, notification prefs, AI depth); no `set_my_commands` menu refresh seen.
- C3: No multi-token compare, no wallet-entity deep-dive, no "scan history" recall in chat.
- C4: No premium upsell inside AI answers (only hard paywall at quota).
- C5: AI answers can't carry inline buttons (web-app deep links to rugmunch.io dashboard).

## 4. rmiadminbot server-control surface (d)
**What it does:** read-only ops (status, top, disk, net, certs, logs, health, backups) + **mutating ops**: `/sh` (blocklist), terminal mode (allowlist), service start/stop/restart (confirm), docker restart / `prune -af --volumes` (confirm), `/deploy`, `/git`, `/push`, `/oc`/`/ai` (opencode CLI).

**Risks (owner-only mitigates, not removes):**
- R1: `/sh` blocklist is narrow — `systemctl stop|disable`, `docker rm -f`, `git push --force`, `kill -9`, `python -c`, `> file` all pass. Telegram-token compromise ⇒ host RCE.
- R2: `/oc`/`/ai` run opencode CLI with tool access (max_turns 1-3) ⇒ **agentic RCE-equivalent** through a single prompt; output truncated but side effects unlimited.
- R3: `/deploy`, `/push`, `/git` mutate production repos/remotes; `docker_prune -af --volumes` deletes volumes on a single confirm.
- R4: No second factor, no session timeout on terminal mode, no alerting on new device/user; hidden-bot silent-deny exists but is reactive.

---

# PRIORITIZED IMPROVEMENTS

## P0 — Fix the grounding & citation core (highest ROI)

### P0.1 Wire real sources/citations end-to-end (fix A1, C1)
- `_fetch_bridge_context` → return `(blocks_str, sources, actions)` instead of str; keep `sources[]` from bridge.
- `_call_zen_llm`/`_call_local_llm` → accept `sources` and return real `rag_count = len(sources)` so the existing "⚡ Grounded in N intelligence sources" footer (`_send_answer`) finally renders; append 2-3 compact source lines with URLs when `web`/`rag` sources exist.
- Render bridge `actions` as inline buttons on the answer (scan address, view exploit rundown) — one `InlineKeyboardMarkup` build in `chat_ai_response`.
- **Files:** `handlers/chat_ai/__init__.py` (`_fetch_bridge_context`, `chat_ai_response`, `_send_answer`), `handlers/chat_ai/_llm.py` (client return shapes).

### P0.2 Re-enable backend auto-RAG as primary path (fix A2)
- Restore `_call_backend_chat` → `POST /api/v1/chat` (auto-RAG news_v2, real `rag_count` from `X-RAG-Context`, per-user X-Forwarded-For rate limiting) as tier-1; keep Zen+local as fallback. This makes the documented pipeline true again and removes duplicate context assembly.
- Delete or clearly mark deprecated `ai_chat.py`; update module docstrings in `chat_ai/__init__.py` to match the live flow.
- **Files:** `chat_ai/__init__.py`, `ai_chat.py`, `prompts.py` docstring.

### P0.3 Unify KB on Qdrant + single embedder (fix B1, B2)
- Move primary retrieval to Qdrant (already the modernization target): one collection namespace per domain, `bge-m3` 1024d everywhere (fallback `bge-small` 384d), hybrid dense+BM25 with RRF fusion, MMR dedup.
- Fix bridge `rerank=true` hang upstream (`rmi-rag` rerank service) or implement cross-encoder rerank (top-20→5) in the RAG API; remove the `[REVIEW]` workaround.
- **Files:** `app/rag/` (rag_engine/supreme consolidation), `RAG_STRATEGY.md` follow-through, `fleet-infra/compose/chat/chat-rag-bridge.py` (`_rag_search`).

### P0.4 Close rmiadminbot RCE-equivalent holes (fix R1-R3)
- `/sh`: switch blocklist → **allowlist** of safe prefixes (status/logs/df/ps/uptime/curl GET...), drop shell redirection handling, require inline-confirm for any command touching services/docker/git.
- `/oc`/`/ai`: default `max_turns=1` and **`--no-tools`/read-only agent** for `/ai`; separate `/oc` into explicit "with-tools" command requiring a confirmation button; never echo full raw output.
- Add TOTP second factor (owner-only preflight before any mutating op), 30-min terminal-mode timeout, and alert on first use from a new device.
- **Files:** `adminbot/admin_ops.py` (`run_shell`), `adminbot/ai_gateway.py`, `adminbot/commands.py` (cmd_oc/cmd_shell/cmd_deploy), `adminbot/terminal.py`, `helpers.py`.

## P1 — Smarter answers, agentic flow, feature surface

- **P1.1 Tool-use agent in chat (A4):** give the LLM a structured tool schema (scan, price, holders, trace, watchlist, web search) and let `chat_ai_response` run 1-2 tool rounds (max 2 hops) instead of keyword routing; route `deep` by intent confidence, not keyword list.
- **P1.2 Context budgeter (A3):** cap per-block chars, add delimiters, dedup daily vs bridge market digest (single source: bridge), drop bot-side `daily_context` when bridge already returns market block.
- **P1.3 Richer history (A6):** keep last 8 msgs with role labels, store user prefs (chains, default depth) in SQLite, summarize old turns into a rolling "user profile" block.
- **P1.4 KB coverage (B3, B4):** surface `wallet_profiles`/`token_analysis`/`forensic_reports`/`contract_audits` collections in bridge search; wire `rag_historical.py` (Rekt/Chainabuse/SlowMist) + content-hash dedup + 15% overlap chunking.
- **P1.5 Chat feedback + eval (B5):** 👍/👎 reactions on AI answers → `rag_feedback.py` boost/penalty; log per-answer `sources` for the golden-set RAGAS run.
- **P1.6 Feature additions (C2-C5):** `/settings` (chain filter, AI depth, notifications), multi-token compare via chat, scan-history recall, premium upsell line in deep answers, `set_my_commands` refresh, dashboard deep-link button.

## P2 — Polish & scale

- **P2.1** Per-answer token telemetry from real `usage` (not `len//4`); per-tier token budgets; streaming answers (edit placeholder incrementally).
- **P2.2** Cross-bot shared AI client (`rugmunchbot/handlers/chat_ai/_llm.py` reused by adminbot via async HTTP instead of subprocess opencode).
- **P2.3** Multi-model A/B: Zen vs backend auto-RAG vs Qwen local, scored by the golden set; keep winners.
- **P2.4** Adminbot read-only command parity with rugmunchbot market/scan endpoints (chat-accessible `/scan`, `/market` in admin bot via backend API, no shell).

## Risks / notes
- Qdrant migration is cross-service (rmi-rag + bridge + backend) — sequence: unify embedder first, then store, then eval gate.
- Changing client return shapes (P0.1) touches tests in `tests/` — update `_call_*` unit tests in the same change.
- `[REVIEW]` rerank hang root cause unconfirmed (bridge workaround comment); verify `rmi-rag` rerank service health before P0.3.
