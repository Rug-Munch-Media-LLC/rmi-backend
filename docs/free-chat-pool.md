# Free chat pool (`chat-free`)

`chat-free` is the assistant gateway's default lane. It is built from **external
APIs with request/token quotas only**. When every lane is down or out of quota it
**fails closed** with `free_pool_exhausted` — it never falls back to a paid model,
an HF shim, or local `llama.cpp`.

Code: `app/assistant_gateway/free_pool/` · probe: `scripts/free-chat-health.py --secrets-dir $FREE_POOL_SECRETS_DIR`
· LiteLLM group: `litellm/chat-free.yaml` · endpoint: `GET /api/v1/assistant/pool/health`

## Why the previous groups broke

| symptom | root cause (verified 2026-10-24) |
|---|---|
| `fleet-free` → `Connection error` | every entry pointed at Kilo proxies `127.0.0.1:18091-18093`; all three are dead. The working path is `https://api.kilocode.ai/api/gateway/v1` **through the proxy egress** |
| `fleet-text` → `client_connect_invalid_ip` | OpenRouter proxy `:18101` egresses from an IP the OR edge rejects |
| `free-mix` randomly hanging | mixed dead proxies + HF shim `:20199` (local, banned) in one group |
| `fleet-free` "free" but weak | 7 of 7 entries were the HF shim — not external, and 0 GPU |

Stale key files `openrouter_account1_api_key` / `openrouter_account2_api_key`
return `401 {"message":"User not found."}`. The live keys are
`OPENROUTER_API_KEY` / `OPENROUTER_BACKUP_API_KEY` (the secrets mount (`FREE_POOL_SECRETS_DIR`)).

## Fallback order (data: `free_pool/pool.json`)

| # | provider id | model | base | measured |
|---|---|---|---|---|
| 1 | `openrouter-main` | `cohere/north-mini-code:free` | `https://openrouter.ai/api/v1` | 200, 0.58 s, cost 0 |
| 2 | `openrouter-backup` | `cohere/north-mini-code:free` | same, account 2 | 200, 0.92 s |
| 3 | `kilo-direct` | `cohere/north-mini-code:free` | `https://api.kilocode.ai/api/gateway/v1` | 200, 2.19 s (proxy egress) |
| 4 | `groq-gpt-oss-120b` | `openai/gpt-oss-120b` | `https://api.groq.com/openai/v1` | 200, 0.29 s |
| 5 | `groq-qwen3.8-27b` | `qwen/qwen3.8-27b` | same | 200, 0.08 s |
| 6 | `gemini-flash-lite` | `gemini-flash-lite-latest` | `generativelanguage.googleapis.com/v1beta` | 200, 0.57 s |
| 7 | `nvidia-nemotron-ultra` | `nvidia/nemotron-3-ultra-550b-a55b` | `https://integrate.api.nvidia.com/v1` | 200, 0.87 s (proxy) |
| 8 | `nvidia-gpt-oss-20b` | `openai/gpt-oss-20b` | same | 200, 1.97 s (proxy) |
| — | `mistral-open-nemo` | `open-mistral-nemo` | `https://api.mistral.ai/v1` | **disabled** — optional; `mistral-small` 429, `mistral-large` 403 |

## Capacity math (per day, at the measured limits)

| lane | requests/day | binding limit | notes |
|---|---|---|---|
| OpenRouter main | 1 000 | free-model cap, `is_free_tier:false` | scaled from account usage history |
| OpenRouter backup | 1 000 | same | second account, separate budget |
| Kilo direct | 200 (self-imposed) | **no quota headers exposed** | [REVIEW] see caveat below |
| Groq (2 models, 1 key) | 1 000 shared | `x-ratelimit-limit-requests: 1000`, `limit-tokens: 8000`/min | key-level, split 500/500 in the pool |
| Gemini flash-lite | 1 000 | free tier 15 RPM / 1 000 RPD | measured RPM 15 |
| NVIDIA NIM (2 models, 1 key) | 500 shared | free API credits | split 250/250 |
| **total** | **≈ 4 700 requests/day** | | |

At ~1 000 tokens per assistant turn (≈700 prompt + 300 completion) that is
**≈ 4 700 turns/day ≈ 1 500 three-turn conversations/day**. Token-side headroom is
larger than the request caps: Groq alone allows `8000 TPM × 1440 = 11.5 M` tokens/day.

## Fail-closed contract

```python
from app.assistant_gateway.free_pool import FreePoolExhausted, pick_model

try:
    provider = pick_model(spec, state)      # first healthy lane, in order
except FreePoolExhausted as exc:
    exc.as_error()   # {"error": "free_pool_exhausted", "reason": ..., "fallback": None}
```

* no `fallbacks:` entry for `chat-free` in LiteLLM — exhaustion is terminal
* `as_error()["fallback"]` is always `None`
* `tests/assistant_gateway/test_free_pool.py` asserts no `127.0.0.1`, no
  `:8085/:8086/:20199/:20201/:18091-93`, no `fleet-free|fleet-hf|fleet-text|hf-*`,
  no paid model id can appear in `pool.json`

## Quota + circuit breaker

`QuotaWindow` tracks RPM and a rolling daily count per lane; `Breaker` opens after
2 consecutive failures and allows a probe after 60 s. `PoolState.snapshot()` feeds
`GET /api/v1/assistant/pool/health` (breaker state, used-today, served, last error).

## Live proof (2026-10-24, Talos)

```
$ FREE_POOL_SECRETS_DIR=<secrets mount> HTTPS_PROXY=<proxy egress> \
    python3 scripts/free-chat-health.py --failover-demo --json
pool: chat-free probed: 8 healthy: 8
openrouter-main        200  0.75  OK
openrouter-backup      200  3.35  OK
kilo-direct            200  1.79  OK
groq-gpt-oss-120b      200  0.46  OK
groq-qwen3.8-27b       200  0.31  OK
gemini-flash-lite      200  0.66  OK
nvidia-nemotron-ultra  200  0.99  OK
nvidia-gpt-oss-20b     200  1.06  OK
failover: {"forced_failure":"openrouter-main","forced_error":"429 rate_limited (injected)",
           "re_picked":"openrouter-backup","breaker_state":"open"}
quota (groq): x-ratelimit-limit-requests=1000 x-ratelimit-limit-tokens=8000
              remaining_requests=991 remaining_tokens=7860
```

Eight lanes, eight real `OK` answers; a forced 429 on lane 1 re-picked lane 2 and
left its breaker `open`. `--failover-demo` is a deterministic selector proof (no
network), so it keeps working even when every provider is down.

```
$ python3 -m pytest tests/assistant_gateway/ -q
55 passed
```

`GET /api/v1/assistant/pool/health` (smoke): `{"pool":"chat-free",
"selected":{"provider":"openrouter-main","model":"cohere/north-mini-code:free"}}`.

## Excluded, with the reason (no fabricated availability)

| provider | verdict |
|---|---|
| NVIDIA `meta/llama-3.3-70b-instruct`, `llama-3.1-8b`, `nemotron-super-49b`, `nemotron-3-nano` | **410 Gone** — model EOL on NIM (2026-08/09), not a pool candidate |
| NVIDIA host-direct (no proxy) | TLS handshake timeout — reachable only via the LiteLLM container egress |
| `openrouter_account1/2_api_key` files | **401 User not found** — stale; use `openrouter.env` keys |
| Kilo proxies `18091/18092/18093` | dead; replaced by the direct base |
| OpenCode Go (`step-5-preview-free`, `space-bunny-free`) | `Model is unavailable` |
| Together (`tgp_v1_…`) | 403 — paid credits, no free tier |
| Cerebras / GitHub Models | no key present |
| Mistral `mistral-small-latest` | 429 `code 1300`; `mistral-large-latest` 403 tier |
| Gemini `gemini-2.5-flash`, `gemini-3-flash`, `gemini-2.5-flash-lite` | 404 (retired / renamed) |
| Gemini `gemini-3.8-flash` | 200 but 50–64 s reasoning-first; keep as manual backup, not a pool lane |
| local `llama.cpp` `:8085/:8086/:8009/:20201`, HF shim `:20199` | **banned** — 0 GPU, would overload the box |
| Kilo Code / Cline 40-min-per-day agent plans | **banned** — time-capped, not request-quota |

### [REVIEW] open items

1. **Kilo quota basis.** `api.kilocode.ai` returns 200 with **no** quota/rate
   headers; the 40-min/day cap applies to the consumer agent plan, not verifiably
   to the gateway. The pool therefore self-caps it at 200 req/day and ranks it 3rd
   (not 1st). If it turns out to be time-capped, set `"enabled": false` — one line,
   no code change.
2. **OpenRouter free-model cap.** 1 000/day is the documented cap for accounts with
   credit history; confirm per-account in the OR dashboard, adjust `daily_requests`.
3. **Deploy step.** `litellm/chat-free.yaml` is a fragment; merging it into
   the running proxy config + restarting `rmi-litellm` is an operator
   action — the app-side pool works today without it (it talks to providers directly).
