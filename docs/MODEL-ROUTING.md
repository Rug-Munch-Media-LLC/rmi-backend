# Model Routing — OpenRouter (remote-only, no local LLMs)

> **Status: CANONICAL — 2026-08.** Local model serving (llama.cpp `qwen-chat` :8085 /
> `qwen-analysis` :8086, Ollama) is **decommissioned**. Chat and analysis are
> **remote-only** through the OpenRouter free tier. This doc replaces the old
> `docs/LLM_MODEL_SELECTION.md` (kept as a decommissioned stub).

## Model assignments (all via OpenRouter)

| Tier | Role | OpenRouter model |
|------|------|------------------|
| **snappy** | Fast chat / short answers (replaces the old 4B chat tier) | `poolside/laguna-s-2.1:free` |
| **deep** | Heavy analysis / forensic / long-form (replaces the old 9B analysis tier) | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| **paid fallback** | Last-resort paid model when free-tier models are unavailable/rate-limited | `qwen/qwen-2.5-7b-instruct:lowest` |

Routing is deterministic by question type (regex classifier, no LLM in the hot path):

- definitional / "what is / explain / how does X work" → **snappy**
- forensic / contract / complex / market-analysis questions → **deep**
- simple / quick / chat → **snappy**

## Credentials

- **API key:** `OPENROUTER_BACKUP_API_KEY` — sourced **from gopass** (never commit the
  value, never hardcode it). Set in the backend/worker environment via the standard
  secrets flow.
- **Endpoint:** `https://openrouter.ai/api/v1` (direct, no proxy).

## Context sources (question router)

The question router (`app/domains/telegram/rugmunchbot/handlers/chat_ai/_llm.py` +
`tools/web.py`) assembles context before the remote LLM call:

| Source | What it provides |
|--------|------------------|
| **RAG** (Chat RAG Bridge) | Knowledge-base retrieval for conceptual / education / lore questions (`rag_context`) |
| **SearXNG** (self-hosted, `[INTERNAL]:8089`) | Live web/news results for time-sensitive / market questions (`web_context`) |
| Market digest + scanner context | Daily market overview and scan summaries appended to the prompt |

Time-sensitive questions must never be answered from stale memory — the router marks
them and pulls current SearXNG results instead.

## Balance / usage

- **Admin `/credits`** (owner-only, `app/domains/telegram/rugmunchbot/handlers/owner.py`)
  shows the live OpenRouter credit balance — usage / limit / free-tier status (fail-open).
- Free-tier models (`:free` suffix) have generous daily limits; the paid fallback
  `qwen/qwen-2.5-7b-instruct:lowest` should rarely be hit.

## Guardrails

Server-side guardrails (prompt-injection block, known-scam block, gibberish gate,
per-tier quotas, flood control) remain the only safety layer — the remote models are
stock, not abliterated. Do not expect model-level refusal handling.
