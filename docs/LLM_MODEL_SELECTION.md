# LLM Model Selection — CPU-only (llama.cpp / GGUF) — 2026 Review

> **Status:** RESEARCH FINDINGS (read + web only). No code, no commit.
> **Date:** 2026-08-14
> **Scope:** Replace `deepseek-r1-distill-qwen3-8b-abliterated-i1-q4_k_m` (REASONING model that degenerates on short prompts) with a **non-reasoning** 8B-class abliterated model, and confirm/upgrade the 4B slot (`qwen3-4b-abliterated-q4_k_m`).
> **Host:** Talos EPYC 9645, 16 vCPU, 62GB RAM, CPU-only llama.cpp. Quality > speed.
> **Method:** All model/GGUF pages verified live via HuggingFace API on 2026-08-14. HF URLs cited inline. Anything not directly re-verified is flagged `[REVIEW]`.

---

## TL;DR — FINAL RECOMMENDATION

| Slot | Model (HF repo) | GGUF source (repo) | GGUF file | License | Gated? |
|------|-----------------|--------------------|-----------|---------|--------|
| **8B-class analysis/chat** | `huihui-ai/Huihui-Qwen3.5-9B-abliterated` | `mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF` | `Huihui-Qwen3.5-9B-abliterated.Q6_K.gguf` | Apache-2.0 | **No** (ungated) |
| **4B fast chat** | `huihui-ai/Huihui-Qwen3.5-4B-abliterated` | `mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF` | `Huihui-Qwen3.5-4B-abliterated.Q6_K.gguf` | Apache-2.0 | **No** (ungated) |

Direct download (resolve URLs, no auth — repos confirmed `gated: false`):

```
https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-9B-abliterated.Q6_K.gguf
https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF/resolve/main/Huihui-Qwen3.5-4B-abliterated.Q6_K.gguf
```

Or via CLI (recommended for deploy agent):

```bash
huggingface-cli download mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF --include "Huihui-Qwen3.5-9B-abliterated.Q6_K.gguf"
huggingface-cli download mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF --include "Huihui-Qwen3.5-4B-abliterated.Q6_K.gguf"
```

**Why Q6_K:** 62GB RAM makes memory a non-issue; `Q6_K` is near-lossless vs BF16 while `Q4_K_M` costs a measurable quality step on chat. `Q6_K` is the "quality-first, CPU-viable" sweet spot. `Q4_K_M` exists in the same repo as a fallback if token/s is ever the bottleneck.

**Runtime note (llama.cpp):** Qwen3.5 chat template has `enable_thinking`; run with `--reasoning off` (llama.cpp ≥ Qwen3-support era; Qwen3.5 is `qwen35` GGUF arch — requires a recent llama.cpp build, `[REVIEW]` exact min build). If the deployed llama.cpp is older and cannot parse `qwen35` arch, fall back to `huihui-ai/Huihui-Qwen3-8B-abliterated-v2` (arch `qwen3`, 40K ctx, same huihui abliteration family) — see §4.

---

## 1. Why the current 8B is broken (the problem we're solving)

`deepseek-r1-distill-qwen3-8b-abliterated` is a **DeepSeek-R1 reasoning distill**. Its chat template unconditionally wraps output in `<think>...</think>`; it was trained to think and cannot be switched to non-reasoning mode via `--reasoning off` the way Qwen3/Qwen3.5 hybrids can. Observed symptoms (from ticket): `<think>` chains even with reasoning disabled, and degenerate repetition ("ioneerioneer…") on short prompts — a classic failure when a reasoning model gets a short prompt + greedy/sampled decoding without a proper non-thinking path.

**Fix direction:** replace with a **hybrid model whose native non-thinking mode is first-class** (Qwen3.5 / Qwen3 instruct), abliterated, at the same size class. This eliminates the think-chain failure mode entirely — `enable_thinking=false` (or llama.cpp `--reasoning off`) produces direct answers by design.

---

## 2. TOP 8B-CLASS NON-REASONING ABLITERATED PICKS (ranked)

Context: as of Aug 2026 there is **no 2026-generation 8B abliterated Llama/Mistral** in the top-tier download space (Llama-4 has no 8B dense release; Mistral's newest abliterated is old Mistral-Nemo 12B). The 2026 dense ~8-9B class is owned by **Qwen3.5** (2B/4B/9B dense + 27B + MoE). So the ranking is:

### #1 — `huihui-ai/Huihui-Qwen3.5-9B-abliterated` ⭐ RECOMMENDED
- **HF page (verified):** https://huggingface.co/huihui-ai/Huihui-Qwen3.5-9B-abliterated
  - 9.65B params (BF16 9,653,100,528), **apache-2.0**, `gated: false`, created 2026-03-09, 125 likes / ~9.6K dl-mo.
  - Base model: `Qwen/Qwen3.5-9B` (official, apache-2.0, ungated, 13.9M dl — verified https://huggingface.co/Qwen/Qwen3.5-9B).
- **Why it beats `deepseek-r1-distill-qwen3-8b`:**
  - Native hybrid chat template with `enable_thinking` toggle → **true non-reasoning mode** (no think chains, no degeneration on short prompts). Verified in GGUF chat template metadata (contains `enable_thinking is defined and enable_thinking is false` branch).
  - **262,144 native context** (extensible ~1M) vs 40K on the old Qwen3-8B GGUF — free long-context upgrade.
  - **Gated DeltaNet (linear attention) hybrid architecture** — mostly linear attention (32 layers: 24 DeltaNet + 8 attention) → substantially faster CPU decode and better long-context memory scaling than full-attention Qwen3-8B. This is the "best CPU architecture" property, not just a chat-quality win.
  - **Official benchmark signal (verified from Qwen3.5-9B model card):** MMLU-Pro **82.5**, MMLU-Redux **91.1**, GPQA Diamond **81.7** (beats GPT-OSS-120B 80.1 and Qwen3-Next-80B 77.2), IFEval **91.5** (best in table), IFBench 64.5, MultiChallenge 54.5, LongBench v2 55.2, BFCL-V4 **66.1** (function calling — relevant for tool-calling chat), TAU2-Bench 79.1, MMMLU 81.2, 201 languages. `[REVIEW]` — the Qwen3.5 table doesn't split thinking/non-thinking mode; treat as "best mode" numbers; non-thinking IFEval expected slightly lower but the hybrid is explicitly designed for fast non-thinking chat.
  - Abliteration quality: huihui's newer method (same family as the v2 fix for garbled codes); HarmBench pass rate for the family is 100% (`[REVIEW]` exact number for 3.5-9B; 100% verified for Qwen3-8B-abliterated and Qwen3-4B-abliterated on their pages).
- **GGUF (verified ungated, 27.7K dl):** `mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF` — https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF (API confirms `gated: false`, arch `qwen35`, ctx 262144). Files include `.Q4_K_M`, `.Q5_K_M`, `.Q6_K`, `.Q8_0`, `.f16`, `IQ4_XS`, plus `mmproj` for optional vision.
- **Best quant for CPU: `Q6_K`** (~7.2GB est). Rationale: 62GB RAM ⇒ memory irrelevant; Q6_K ≈ near-lossless, ~15-25% slower than Q4_K_M — acceptable under "quality > speed". Fallback `Q4_K_M` (~5.4GB) if throughput ever matters more.

### #2 — `huihui-ai/Huihui-Qwen3-8B-abliterated-v2` (strict-8B fallback / older-llama.cpp)
- **HF page (verified):** https://huggingface.co/huihui-ai/Huihui-Qwen3-8B-abliterated-v2 — 8B, apache-2.0, base `Qwen/Qwen3-8B`, 3.5K dl-mo. huihui explicitly deprecates v1 in favor of v2 ("Changed the 0 layer to eliminate the problem of garbled codes").
- **Why it beats deepseek-r1-distill:** same non-reasoning `enable_thinking=false` path as Qwen3.5 (Qwen3 is hybrid), 40K ctx, proven huihui abliteration (100% harmbench pass verified on v1 page), huge community adoption (29.9K GGUF dl).
- **Benchmarks (Qwen3-8B, `[REVIEW]` — not re-fetched this session):** Qwen3-8B non-thinking IFEval ≈ 85.9, MMLU ≈ 91.7/MMLU-Pro ≈ 75.8 class. Newer-gen Qwen3.5-9B (#1) is clearly stronger on the verified table (IFEval 91.5, GPQA 81.7).
- **GGUF (verified ungated):** `mradermacher/Huihui-Qwen3-8B-abliterated-v2-GGUF` — https://huggingface.co/mradermacher/Huihui-Qwen3-8B-abliterated-v2-GGUF — files `.Q4_K_M/.Q5_K_M/.Q6_K/.Q8_0/.f16/IQ4_XS`. Quant: `Q6_K` (~6.1GB) same rationale; fallback `Q4_K_M` (~4.9GB).
- **Use when:** llama.cpp build can't do `qwen35` arch, or a hard ≤8B ceiling is enforced. Otherwise #1 wins on every axis (quality, context, CPU speed).

### #3 — `huihui-ai/Qwen3-8B-abliterated` (v1 — do NOT deploy fresh)
- https://huggingface.co/huihui-ai/Qwen3-8B-abliterated — 1.7K dl-mo, 100% harmbench pass verified. **Superseded by v2** per huihui's own note; v1 had layer-0 garbling issues. Listed only to explain why existing fleet references to "qwen3-8b-abliterated-q4_k_m" should move to v2 (or 3.5-9B).

### Honorable mention / adjacent (not 8B, noted for future)
- `Bahushruth/Qwen3.6-35B-A3B-abliterated-v4` — 35B-total/3B-active MoE, **1.17M downloads** (top abliterated on HF by far), apache-2.0, 2026-06. Runs in RAM on Talos (Q4 ~20GB) but MoE CPU decode is slow-ish and it's not the 8B slot requested. `[REVIEW]` if you want to revisit for a "heavy analysis" tier.
- `mlabonne/Qwen3-30B-A3B-abliterated` — 30B-A3B MoE, 2025-04, 450K dl. Same caveat.
- No 2026-era abliterated Llama-4-8B / Mistral-8B found in top-download search — the "newer Llama/Mistral abliterated 8B" hypothesis does not pan out as of this date.

---

## 3. TOP 4B ABLITERATED PICKS (ranked)

### #1 — `huihui-ai/Huihui-Qwen3.5-4B-abliterated` ⭐ RECOMMENDED (supersedes current qwen3-4b)
- **HF page (verified):** https://huggingface.co/huihui-ai/Huihui-Qwen3.5-4B-abliterated — 4.66B params (BF16 4,659,861,248), **apache-2.0**, `gated: false`, created 2026-03-07, 17.7K dl-mo (highest of any 4B abliterated).
- Base: `Qwen/Qwen3.5-4B` (official, apache-2.0, ungated, 7.5M dl — verified https://huggingface.co/Qwen/Qwen3.5-4B).
- **Benchmarks (verified from Qwen3.5-4B model card):** MMLU-Pro **79.1**, MMLU-Redux **88.8**, GPQA Diamond **76.2**, IFEval **89.8**, IFBench 59.2, MultiChallenge 49.0, LongBench v2 50.0, BFCL-V4 50.3, **TAU2-Bench 79.9** (tops the table), MMMLU 76.1. Same hybrid `enable_thinking` template, 262K ctx, DeltaNet hybrid → fast CPU.
- **GGUF (verified ungated):** `mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF` — https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF — files `.Q4_K_M/.Q5_K_M/.Q6_K/.Q8_0/.f16/IQ4_XS` (+mmproj). **Quant: `Q6_K`** (~3.5GB est — trivially small; near-lossless).

### #2 — `huihui-ai/Huihui-Qwen3-4B-abliterated-v2` (drop-in upgrade of current model, Qwen3 gen)
- https://huggingface.co/huihui-ai/Huihui-Qwen3-4B-abliterated-v2 — 4B, apache-2.0, 1.5K dl-mo; v2 fixes v1 garbling. GGUF: `mradermacher/Huihui-Qwen3-4B-abliterated-v2-GGUF` (verified ungated; also `-i1-GGUF` imatrix variant exists). 40K ctx.
- **Use when:** staying on Qwen3 gen (llama.cpp compat) — but Qwen3.5-4B is strictly newer/better and same price class, so prefer #1.

### #3 — `huihui-ai/Qwen3-4B-abliterated` (v1 — current deployment base)
- https://huggingface.co/huihui-ai/Qwen3-4B-abliterated — what the fleet runs today (`qwen3-4b-abliterated-q4_k_m`). Superseded by v2, then by Qwen3.5-4B.

**Answer to "is qwen3-4b-abliterated still the best 4B?":** **No.** `Huihui-Qwen3.5-4B-abliterated` (Mar 2026) is the new best 4B — newer architecture (DeltaNet hybrid, 262K ctx), higher verified benchmarks (IFEval 89.8 vs Qwen3-4B class ≈ 72-78 `[REVIEW]`), and it's the most-downloaded 4B abliterated.

---

## 4. Abliterated vs stock + guardrails — the tradeoff, explicitly

**Abliterated (recommended, keep current policy):**
- Pros: removes refusal vectors at the **weight level** (huihui HarmBench pass: stock Qwen3-8B = 60.94% vs abliterated = **100%**, verified on model page; Qwen3-4B 81.56% → 100%). No model-layer pre-emption of the pipeline — the server guardrails are the *only* enforcement layer, which is exactly the current architecture. Abliteration is a surgical weight edit; the abliterated models retain the base's benchmark profile (Qwen3.5 abliterated carries the same arch/config as stock).
- Cons: no model-level safety net — if the server-side guardrail misses, nothing catches. Minor quality/fluency cost possible; huihui v1 had layer-0 garbling (fixed in v2). Requires gated-access acceptance for huihui's own repos (see §5).
- **Fleet relevance:** crypto security research (rugpull analysis, exploit writeups, token forensics) routinely trips stock-model refusals. With guardrails already enforced server-side, abliteration removes the *double-refusal* failure mode. **Decision: stay abliterated; keep server guardrails as enforcement (defense in depth).**

**Stock model + guardrails (the alternative considered):**
- Keeps model safety intact, and Qwen3.5 base models are ungated (verified `gated: false`) — but every crypto-adjacent prompt risks a model-level refusal before the guardrail is even reached, defeating the pipeline's purpose and adding retry latency. If compliance posture ever demands it, stock `Qwen/Qwen3.5-9B` (apache-2.0, ungated) is a drop-in swap of the same GGUF plumbing — no re-architecture needed.

**Bottom line:** abliterated + existing server guardrails is the right configuration for this platform; revisit only if compliance requires model-level safety.

---

## 5. Gating / download gotchas (verified)

- **huihui-ai's OWN GGUF repos are gated** — e.g. `huihui-ai/Qwen3-8B-abliterated-GGUF` returns HTTP 401 without login/ToS acceptance (verified this session). Any deploy agent hitting huihui GGUF repos will fail anonymously.
- **mradermacher mirrors are `gated: false` (verified via API for all 4 repos cited above)** → use these for unattended download. They also carry the full quant ladder (IQ4_XS → f16).
- Base models `Qwen/Qwen3.5-9B`, `Qwen/Qwen3.5-4B`: `gated: false`, apache-2.0 (verified).
- No API key needed for any recommended download.

---

## 6. Deployment checklist for the deploy agent

1. Download the two GGUFs (§ TL;DR URLs — ungated).
2. Verify checksums (compare against repo page / `--include` output; `sha256sum`).
3. llama.cpp: use a build that supports **`qwen35`** GGUF arch (recent 2026 release). `[REVIEW]` — exact minimum build number not verified this session; if the installed llama.cpp predates Qwen3.5 support, use fallback #2 (Qwen3-8B-v2, `qwen3` arch, known-supported).
4. Serve with **`--reasoning off`** (maps to `enable_thinking=false` in the chat template) so no think chains are produced. `[REVIEW]` — behavior of the empty `<think>` prefix in the Qwen3.5 template under `--reasoning off` should be smoke-tested; if an empty `<think></think>` artifact appears, strip it in the serving wrapper.
5. Sanity prompts: short one-liner ("hi", "what is bitcoin?") — must NOT emit `<think>` or degenerate repetition (the exact bug being fixed); a crypto-adjacent prompt (rugpull analysis) — must NOT refuse.
6. Keep the current model files until smoke tests pass (rollback = swap GGUF path back).

---

## 7. Verification log (what was fetched, when)

All verified live on **2026-08-14** via HuggingFace API / model-card fetch:
- https://huggingface.co/huihui-ai/Huihui-Qwen3.5-9B-abliterated (exists, apache-2.0, ungated, 9.65B)
- https://huggingface.co/Qwen/Qwen3.5-9B (official base; benchmarks table fetched from raw README)
- https://huggingface.co/Qwen/Qwen3.5-4B (official base; benchmarks table fetched)
- https://huggingface.co/huihui-ai/Huihui-Qwen3.5-4B-abliterated (exists, apache-2.0, ungated, 4.66B)
- https://huggingface.co/huihui-ai/Huihui-Qwen3-8B-abliterated-v2 + `-4B-abliterated-v2` (exist; huihui-recommended over v1)
- https://huggingface.co/huihui-ai/Qwen3-8B-abliterated + `Qwen3-4B-abliterated` (v1 pages; HarmBench 100% pass rates + "new version available" note)
- https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF (ungated, file list incl. Q6_K)
- https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF (ungated, file list incl. Q6_K)
- https://huggingface.co/mradermacher/Huihui-Qwen3-8B-abliterated-v2-GGUF and `-4B-abliterated-v2-GGUF` (ungated, file lists)
- https://huggingface.co/huihui-ai/Qwen3-8B-abliterated-GGUF → **401 gated** (confirmed gating behavior)
- HF API searches: `search=abliterated` (top-60 by downloads) — 2026 landscape scan (Qwen3.5/3.6, Gemma-4, DeepSeek-V4-Flash-abliterated, gpt-oss-20b, GLM-5.2, etc.); `search=Qwen3.5-4B-abliterated`, `search=Huihui-Qwen3-4B-abliterated-v2-GGUF`.

`[REVIEW]` items: exact min llama.cpp build for `qwen35`; Qwen3.5 non-thinking-mode split of benchmark numbers; Qwen3-8B/4B exact MMLU/IFEval (not re-fetched, cited as "class" estimates); HarmBench % for the 3.5-series abliterated models specifically (family verified 100% on Qwen3 pages); estimated GGUF file sizes (computed from BF16 param counts, not downloaded).
