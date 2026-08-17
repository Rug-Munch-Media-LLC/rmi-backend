# LLM Model Selection — CANONICAL

> **Status: CANONICAL — 2026-08.** All production chat/analysis runs **remote via
> OpenRouter** (`OPENROUTER_BACKUP_API_KEY`, from gopass):
> - **snappy** = `poolside/laguna-s-2.1:free` (fast chat)
> - **deep/long** = `nvidia/nemotron-3-ultra-550b-a55b:free` (reasoning analysis)
> - **paid fallback** = `qwen/qwen-2.5-7b-instruct:lowest` (last resort when free
>   tiers are rate-limited)
>
> **Local llama.cpp is DEV/PLAY ONLY.** The old Qwen3.5-9B weights, ports
> (:8085/:8086) and the local serving stack were removed from the production
> chat path. A local **Qwen 3.5 4B quant** may be kept on llama.cpp purely to
> play with and test — it is never part of the chat stack and has no production
> endpoint contract.
>
> Model routing details: see [`docs/MODEL-ROUTING.md`](MODEL-ROUTING.md).
