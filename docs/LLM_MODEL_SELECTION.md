# LLM Model Selection — DECOMMISSIONED (local llama.cpp / GGUF)

> **Status: DECOMMISSIONED — 2026-08.** The local CPU-only llama.cpp serving stack
> (`qwen-chat` on :8085, `qwen-analysis` on :8086) has been **torched**. There is no
> local model serving anymore — chat/analysis is **remote-only**.
>
> **Current model setup:** see [`docs/MODEL-ROUTING.md`](MODEL-ROUTING.md).
> All routing is via OpenRouter free tier (`OPENROUTER_BACKUP_API_KEY`, from gopass):
> snappy = `poolside/laguna-s-2.1:free`, deep = `nvidia/nemotron-3-ultra-550b-a55b:free`,
> paid fallback = `qwen/qwen-2.5-7b-instruct:lowest`.
>
> The previous GGUF research record (2026-08-14 deployment of
> `Huihui-Qwen3.5-4B/9B-Abliterated.Q6_K.gguf`) is superseded and intentionally not
> retained — local weights, ports and GGUFs are gone.
