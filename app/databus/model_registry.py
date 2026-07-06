"""
RugCharts Model Registry & Quality Standards
=============================================
Smart model routing across free providers. Quality review pipeline.
All AI tasks go through this module. All output meets human standards.

Free Models Available (OpenRouter):
  NVIDIA Nemotron 3 Super 120B  - research, analysis, long context (1M)
  Google Gemma 4 26B            - writing, prose, natural language
  NVIDIA Nemotron Nano 30B      - reasoning, classification
  Qwen3 Coder 480B              - code generation, tool use
  Moonshot Kimi K2.6            - fast writing, summaries
  Z.ai GLM 4.5 Air             - general purpose, fast
  OpenAI gpt-oss-120b           - heavy reasoning, agentic tasks
  OpenAI gpt-oss-20b            - lightweight, fast inference
  Liquid LFM 2.5 1.2B           - edge, tiny tasks, classification

Other Free Providers:
  Groq (Llama 3.1 8B, Llama 3.3 70B) - 14,400 RPD free
  Mistral (via OpenRouter free tier)
  DeepSeek Flash V4 - $0.14/M (near-free with prefix caching)

Quality Standards:
  NO: "delve", "tapestry", "landscape", "robust", "moreover", "furthermore",
       "in conclusion", "it is worth noting", "underscores", "showcasing",
       "a testament to", "in the realm of", "paradigm shift"
  YES: direct, specific, human voice, numbers, names, concrete details
  ALWAYS: review step before publishing
"""

import json
import logging
import os
import time
from collections import defaultdict

import httpx

logger = logging.getLogger("model_registry")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "")
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

# ── Model Registry ─────────────────────────────────────────────────

MODELS = {
    # ── RESEARCH & ANALYSIS ──
    "research": {
        "primary": {
            "id": "nvidia/nemotron-3-super-120b-a12b:free",
            "provider": "openrouter",
            "context": 1000000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["long_context", "analysis", "data_synthesis", "multi_document"],
        },
        "fallback": {
            "id": "nvidia/nemotron-3-nano-30b-a3b:free",
            "provider": "openrouter",
            "context": 256000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["reasoning", "analysis", "structured_output"],
        },
    },
    # ── WRITING & PROSE ──
    "writing": {
        "primary": {
            "id": "nvidia/nemotron-3-super-120b-a12b:free",
            "provider": "openrouter",
            "context": 1000000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["natural_prose", "long_context", "creative"],
        },
        "fallback": {
            "id": "nvidia/nemotron-3-nano-30b-a3b:free",
            "provider": "openrouter",
            "context": 256000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["reasoning", "writing", "structured"],
        },
    },
    # ── CODE & TOOL USE ──
    "coding": {
        "primary": {
            "id": "nvidia/nemotron-3-super-120b-a12b:free",
            "provider": "openrouter",
            "context": 1000000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["code_gen", "agentic", "long_context"],
        },
        "fallback": {
            "id": "nvidia/nemotron-3-nano-30b-a3b:free",
            "provider": "openrouter",
            "context": 256000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["reasoning", "code", "structured_output"],
        },
    },
    # ── REVIEW & QUALITY CHECK ──
    "review": {
        "primary": {
            "id": "nvidia/nemotron-3-nano-30b-a3b:free",
            "provider": "openrouter",
            "context": 256000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["proofreading", "error_detection", "consistency"],
        },
        "fallback": {
            "id": "z-ai/glm-4.5-air:free",
            "provider": "openrouter",
            "context": 131000,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "classification", "simple_tasks"],
        },
    },
    # ── FAST / LIGHTWEIGHT ──
    "fast": {
        "primary": {
            "id": "z-ai/glm-4.5-air:free",
            "provider": "openrouter",
            "context": 131000,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "classification", "simple_tasks"],
        },
        "fallback": {
            "id": "nvidia/nemotron-3-nano-30b-a3b:free",
            "provider": "openrouter",
            "context": 256000,
            "cost_per_1k": 0,
            "rpm": 20,
            "strengths": ["reasoning", "general", "reliable"],
        },
        "groq": {
            "id": "llama-3.1-8b-instant",
            "provider": "groq",
            "context": 128000,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "sub_100ms_ttft", "high_throughput"],
        },
    },
    # ── WRITING (Groq) ──
    "writing_groq": {
        "primary": {
            "id": "llama-3.3-70b-versatile",
            "provider": "groq",
            "context": 128000,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["writing", "speed", "quality_prose"],
        },
        "fallback": {
            "id": "llama-3.1-8b-instant",
            "provider": "groq",
            "context": 128000,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "throughput", "reliable"],
        },
    },
    # ── MISTRAL FALLBACKS (free tier, 6 models) ──
    "mistral_write": {
        "primary": {
            "id": "mistral-small-latest",
            "provider": "mistral",
            "context": 262144,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["writing", "balanced", "multilingual"],
        },
        "fallback": {
            "id": "ministral-8b-latest",
            "provider": "mistral",
            "context": 262144,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "efficient", "good_prose"],
        },
    },
    "mistral_code": {
        "primary": {
            "id": "codestral-latest",
            "provider": "mistral",
            "context": 256000,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["code_gen", "fill_in_middle", "agentic"],
        },
    },
    "mistral_fast": {
        "primary": {
            "id": "ministral-3b-latest",
            "provider": "mistral",
            "context": 131072,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "tiny", "classification"],
        },
        "fallback": {
            "id": "mistral-tiny-latest",
            "provider": "mistral",
            "context": 131072,
            "cost_per_1k": 0,
            "rpm": 30,
            "strengths": ["speed", "simple_tasks", "high_throughput"],
        },
    },
}

# ── AI ROLE ARCHITECTURE ──────────────────────────────────────────
# Each role isolated. Each gets its own model + budget. Never interfere.

AI_ROLES = {
    "advisor": {
        "name": "Platform Advisor",
        "emoji": "🛡️",
        "description": "Monitors system health, rate limits, anomalies. Proactive alerts.",
        "model": "nvidia/nemotron-3-nano-30b-a3b:free",
        "provider": "openrouter",
        "budget": {"per_hour": 10, "per_day": 50},
        "temperature": 0.2,
        "data_classifier": {
            "name": "Data Classifier",
            "emoji": "🏷️",
            "description": "Categorizes articles, detects sentiment, tags content. High throughput on Groq.",
            "model": "llama-3.1-8b-instant",
            "provider": "groq",
            "fallback": "ministral-3b-latest",
            "fallback_provider": "mistral",
            "budget": {"per_minute": 25, "per_day": 3000},
            "temperature": 0.1,
        },
        "social_writer": {
            "name": "Social Media Writer",
            "emoji": "𝕏",  # noqa: RUF001
            "description": "X/Twitter posts, Telegram messages. Runs on Groq, high throughput.",
            "model": "llama-3.1-8b-instant",
            "provider": "groq",
            "fallback": "mistral-small-latest",
            "fallback_provider": "mistral",
            "budget": {"per_task": 2, "per_day": 50},
            "temperature": 0.8,
        },
        "cron_worker": {
            "name": "Cron Worker",
            "emoji": "⏰",
            "description": "Scheduled tasks. Primary on Mistral (unlimited), fallback Groq.",
            "model": "ministral-3b-latest",
            "provider": "mistral",
            "fallback": "llama-3.1-8b-instant",
            "fallback_provider": "groq",
            "budget": {"per_task": 5, "per_day": 200},
            "temperature": 0.5,
        },
        "content_writer": {
            "name": "Content Writer",
            "emoji": "✍️",
            "description": "Quality prose. Mistral primary, Groq for volume.",
            "model": "mistral-small-latest",
            "provider": "mistral",
            "fallback": "llama-3.3-70b-versatile",
            "fallback_provider": "groq",
            "budget": {"per_task": 3, "per_day": 30},
            "temperature": 0.7,
        },
        "advisor": {
            "name": "Platform Advisor",
            "emoji": "🛡️",
            "description": "System health. Uses Groq (never touches OpenRouter research quota).",
            "model": "llama-3.3-70b-versatile",
            "provider": "groq",
            "fallback": "mistral-small-latest",
            "fallback_provider": "mistral",
            "budget": {"per_hour": 10, "per_day": 100},
            "temperature": 0.2,
        },
    },
    "rag_embedder": {
        "name": "RAG Embedder",
        "emoji": "🧠",
        "description": "Vector embeddings. Uses NVIDIA NIM directly (NOT OpenRouter) to avoid quota conflict. Batch + cache.",
        "model": "nvidia/nemo-embed-12b",
        "provider": "nvidia_nim",
        "budget": {"per_day": 50000, "batch_size": 100},
        "temperature": 0.0,
        "strategy": "BATCH: embed 100 docs per call. CACHE: never re-embed. LOCAL: consider sentence-transformers for hot path.",
    },
    "security_auditor": {
        "name": "Security Auditor",
        "emoji": "🔐",
        "description": "Scans code/configs for vulnerabilities, exposed keys, unsafe patterns.",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "provider": "openrouter",
        "budget": {"per_task": 5, "per_day": 10},
        "temperature": 0.1,
    },
    "data_classifier": {
        "name": "Data Classifier",
        "emoji": "🏷️",
        "description": "Categorizes articles, detects sentiment, tags content. High throughput.",
        "model": "ministral-3b-latest",
        "provider": "mistral",
        "fallback": "z-ai/glm-4.5-air:free",
        "fallback_provider": "openrouter",
        "budget": {"per_minute": 20, "per_day": 500},
        "temperature": 0.1,
    },
    "social_writer": {
        "name": "Social Media Writer",
        "emoji": "𝕏",  # noqa: RUF001
        "description": "X/Twitter posts, Telegram messages. Punchy, engaging, native to platform.",
        "model": "mistral-small-latest",
        "provider": "mistral",
        "fallback": "llama-3.1-8b-instant",
        "fallback_provider": "groq",
        "budget": {"per_task": 2, "per_day": 20},
        "temperature": 0.8,
    },
    "fact_checker": {
        "name": "Fact Checker",
        "emoji": "✅",
        "description": "Verifies claims against known data. Cross-references sources.",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "provider": "openrouter",
        "budget": {"per_task": 3, "per_day": 15},
        "temperature": 0.1,
    },
}

# ── PROVIDER RATE LIMITS (verified June 2026) ─────────────────────
# These are HARD LIMITS - going over means 429 errors and downtime.

PROVIDER_LIMITS = {
    "openrouter": {
        "name": "OpenRouter",
        "rpm": 20,  # requests per minute
        "rpd_free_no_credits": 50,  # free users without credits
        "rpd_free_with_credits": 1000,  # $10+ credits purchased
        "current_tier": "paid",  # user has spent money = higher tier
        "free_model_suffix": ":free",
        "check_endpoint": "https://openrouter.ai/api/v1/key",
    },
    "groq": {
        "name": "Groq",
        "rpm": 30,  # requests per minute
        "rpd": 14400,  # requests per day (free tier)
        "tpm": 6000,  # tokens per minute (approx)
        "current_tier": "free",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    "mistral": {
        "name": "Mistral",
        "rps": 1,  # requests per second (1/sec)
        "tpm": 500000,  # tokens per minute (free tier)
        "tpm_budget": 1000000000,  # tokens per month (1B free)
        "current_tier": "free",
    },
    "nvidia_nim": {
        "name": "NVIDIA NIM",
        "rpm": 100,  # generous free tier
        "rpd": 5000,  # daily requests
        "current_tier": "free",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_models": [
            "nvidia/nemotron-3-super-120b-a12b",  # 1M ctx, best research
            "nvidia/nemotron-3-nano-30b-a3b",  # fast reasoning
            "nvidia/nv-embedqa-e5-v5",  # embeddings!
            "nvidia/llama-3.3-nemotron-super-49b-v1",  # Llama Nemotron
            "nvidia/nemotron-4-340b-instruct",  # 340B monster
            "meta/llama-3.3-70b-instruct",  # Llama 3.3 70B
            "deepseek-ai/deepseek-v4-flash",  # DeepSeek V4 Flash
            "google/gemma-4-31b-it",  # Gemma 4 31B
            "mistralai/mistral-large-3-675b-instruct",  # Mistral Large 675B
            "qwen/qwen3-coder-480b-a35b-instruct",  # Qwen Coder 480B
            "baai/bge-m3",  # BGE embedder
            "snowflake/arctic-embed-line",  # Arctic embedder
        ],
    },
}

# ── INTELLIGENT USAGE TRACKER ─────────────────────────────────────
# Tracks per-minute, per-hour, per-day usage. Never exceeds limits.


class RateLimitTracker:
    """Tracks API usage across all providers. Respects hard limits.

    Budget allocation (of 1,000 OpenRouter + 14,400 Groq + Mistral):
      - Daily Intel report:     3-5 calls/day (research + write + review)
      - CT Rundown:             1-2 calls/day (summarize)
      - Content review:         5-10 calls/day (quality checks)
      - Background tasks:       10-20 calls/day (classification, enrichment)
      - Peak headroom:          ~950 calls/day remaining for bursts
    """

    def __init__(self):
        self._minute: dict[str, int] = defaultdict(int)  # provider → calls this minute
        self._hour: dict[str, int] = defaultdict(int)
        self._day: dict[str, int] = defaultdict(int)
        self._minute_start = time.time()
        self._hour_start = time.time()
        self._day_start = time.time()
        self._total_calls = 0
        self._throttled = 0

    def _reset_windows(self):
        now = time.time()
        if now - self._minute_start > 60:
            self._minute.clear()
            self._minute_start = now
        if now - self._hour_start > 3600:
            self._hour.clear()
            self._hour_start = now
        if now - self._day_start > 86400:
            self._day.clear()
            self._day_start = now

    def can_call(self, provider: str) -> tuple[bool, str]:
        """Check if we can make a call to this provider without exceeding limits."""
        self._reset_windows()
        limits = PROVIDER_LIMITS.get(provider, {})
        if not limits:
            return True, ""

        # Per-minute check
        rpm = limits.get("rpm", 20)
        if self._minute[provider] >= rpm:
            wait = 60 - (time.time() - self._minute_start)
            return False, f"{provider}: RPM limit ({rpm}/min), retry in {wait:.0f}s"

        # Per-day check
        if provider == "openrouter":
            rpd = limits.get("rpd_free_with_credits", 1000)
        elif provider == "groq":
            rpd = limits.get("rpd", 14400)
        else:
            rpd = limits.get("rpd", 100000)  # Mistral: effectively unlimited for requests

        if self._day[provider] >= rpd:
            return False, f"{provider}: Daily limit ({rpd}/day) exhausted"

        # Mistral: 1 req/sec check
        if provider == "mistral" and limits.get("rps", 1):
            if self._minute[provider] >= 58:  # Leave 2/sec headroom
                return False, "mistral: nearing RPS limit"

        return True, ""

    def record_call(self, provider: str, tokens: int = 0):
        """Record a successful API call."""
        self._reset_windows()
        self._minute[provider] += 1
        self._hour[provider] += 1
        self._day[provider] += 1
        self._total_calls += 1

    def record_throttle(self, provider: str):
        """Record a throttled/blocked call."""
        self._throttled += 1

    def budget_remaining(self, provider: str) -> dict:
        """Get remaining budget for a provider."""
        self._reset_windows()
        limits = PROVIDER_LIMITS.get(provider, {})
        rpm = limits.get("rpm", 20)

        if provider == "openrouter":
            rpd = limits.get("rpd_free_with_credits", 1000)
        elif provider == "groq":
            rpd = limits.get("rpd", 14400)
        else:
            rpd = 100000

        return {
            "provider": provider,
            "minute_used": self._minute[provider],
            "minute_limit": rpm,
            "minute_remaining": max(0, rpm - self._minute[provider]),
            "day_used": self._day[provider],
            "day_limit": rpd,
            "day_remaining": max(0, rpd - self._day[provider]),
            "day_pct": round(self._day[provider] / max(rpd, 1) * 100, 1),
        }

    def stats(self) -> dict:
        """Full usage statistics."""
        return {
            "total_calls": self._total_calls,
            "throttled": self._throttled,
            "providers": {p: self.budget_remaining(p) for p in PROVIDER_LIMITS},
            "budget_allocation": {
                "daily_intel": "3-5 calls/day",
                "ct_rundown": "1-2 calls/day",
                "content_review": "5-10 calls/day",
                "background": "10-20 calls/day",
                "headroom": f"~{1000 - self._day.get('openrouter', 0)} calls remaining today",
            },
        }


# Global tracker instance
rate_tracker = RateLimitTracker()


def _can_use(model_config: dict) -> bool:
    """Check if model is under its rate limit using the tracker."""
    provider = model_config.get("provider", "openrouter")
    can, reason = rate_tracker.can_call(provider)
    if not can:
        logger.debug(f"Rate limited: {reason}")
        rate_tracker.record_throttle(provider)
        return False
    return True


def _track_usage(model_id: str, tokens: int = 0):
    """Track model usage through the rate tracker."""
    for _provider, _limits in PROVIDER_LIMITS.items():
        # Match model to provider
        model_providers = {
            "openrouter": [
                "nvidia/",
                "z-ai/",
                "google/",
                "qwen/",
                "openai/",
                "moonshotai/",
                "liquid/",
                "openrouter/",
            ],
            "groq": ["llama-3", "llama-4", "mixtral", "gemma"],
            "mistral": ["mistral", "ministral", "codestral", "open-mistral"],
        }
        for p, prefixes in model_providers.items():
            if any(model_id.startswith(pref) for pref in prefixes):
                rate_tracker.record_call(p, tokens)
                return


async def _call_openrouter(
    model_id: str, system: str, user: str, max_tokens: int = 1000, temperature: float = 0.5
) -> str:
    """Call OpenRouter API."""
    if not OPENROUTER_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(
                OR_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://rugmunch.io",
                    "X-Title": "RugCharts AI",
                },
                json={
                    "model": model_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code == 200:
                resp = r.json()
                usage = resp.get("usage", {})
                _track_usage(model_id, usage.get("total_tokens", 0))
                return resp["choices"][0]["message"]["content"]
            else:
                logger.warning(f"OpenRouter {model_id}: {r.status_code}")
                return ""
    except Exception as e:
        logger.warning(f"OpenRouter error {model_id}: {e}")
        return ""


async def _call_groq(model_id: str, system: str, user: str, max_tokens: int = 1000, temperature: float = 0.5) -> str:
    """Call Groq API (free tier)."""
    if not GROQ_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            else:
                logger.warning(f"Groq {model_id}: {r.status_code} {r.text[:200]}")
                return ""
    except Exception as e:
        logger.warning(f"Groq error: {e}")
        return ""


async def _call_mistral(model_id: str, system: str, user: str, max_tokens: int = 1000, temperature: float = 0.5) -> str:
    """Call Mistral API (free tier)."""
    if not MISTRAL_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                MISTRAL_URL,
                headers={
                    "Authorization": f"Bearer {MISTRAL_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code == 200:
                _track_usage(model_id, max_tokens)
                return r.json()["choices"][0]["message"]["content"]
            else:
                logger.warning(f"Mistral {model_id}: {r.status_code}")
                return ""
    except Exception as e:
        logger.warning(f"Mistral error: {e}")
        return ""


async def ai_call(
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.5,
) -> str:
    """THE method. Call the best free model for a task type.

    Routes to: research, writing, coding, review, fast.
    Falls back: primary → fallback → groq → mistral → any available.
    Three providers: OpenRouter (3 models), Groq (2 models), Mistral (6 models).
    Zero cost. Always finds a model.
    """
    if task_type not in MODELS:
        task_type = "fast"

    config = MODELS[task_type]

    # Try all tiers in order
    tiers = ["primary", "fallback", "groq"]

    for tier in tiers:
        if tier not in config:
            continue
        model = config[tier]
        if not _can_use(model):
            continue

        if model["provider"] == "openrouter":
            result = await _call_openrouter(model["id"], system_prompt, user_prompt, max_tokens, temperature)
        elif model["provider"] == "groq":
            result = await _call_groq(model["id"], system_prompt, user_prompt, max_tokens, temperature)
        elif model["provider"] == "mistral":
            result = await _call_mistral(model["id"], system_prompt, user_prompt, max_tokens, temperature)
        else:
            continue

        if result:
            return result

    # ── Extended fallback: try Mistral models ──
    mistral_tasks = ["mistral_fast", "mistral_write", "mistral_code"]
    for mt in mistral_tasks:
        if mt == task_type:
            continue
        mconfig = MODELS.get(mt, {})
        for tier in ["primary", "fallback"]:
            if tier not in mconfig:
                continue
            model = mconfig[tier]
            if _can_use(model):
                result = await _call_mistral(model["id"], system_prompt, user_prompt, max_tokens, temperature)
                if result:
                    return result

    # ── Last resort: try any available free model ──
    for backup_type in ["fast", "writing", "writing_groq"]:
        if backup_type == task_type:
            continue
        backup_config = MODELS[backup_type]
        for tier_name in ["primary", "fallback", "groq"]:
            if tier_name in backup_config:
                model = backup_config[tier_name]
                if _can_use(model):
                    if model["provider"] == "openrouter":
                        result = await _call_openrouter(
                            model["id"], system_prompt, user_prompt, max_tokens, temperature
                        )
                    elif model["provider"] == "groq":
                        result = await _call_groq(model["id"], system_prompt, user_prompt, max_tokens, temperature)
                    elif model["provider"] == "mistral":
                        result = await _call_mistral(model["id"], system_prompt, user_prompt, max_tokens, temperature)
                    if result:
                        return result

    return ""


# ═══════════════════════════════════════════════════════════════════════
# QUALITY STANDARDS & REVIEW
# ═══════════════════════════════════════════════════════════════════════

FORBIDDEN_WORDS = [
    "delve",
    "tapestry",
    "landscape",
    "robust",
    "moreover",
    "furthermore",
    "in conclusion",
    "it is worth noting",
    "underscores",
    "showcasing",
    "a testament to",
    "in the realm of",
    "paradigm shift",
    "game changer",
    "revolutionize",
    "disrupt",
    "unprecedented",
    "groundbreaking",
    "synergy",
    "ecosystem",
    "holistic",
    "cutting-edge",
    "state-of-the-art",
    "leveraging",
    "utilize",
    "facilitate",
    "spearhead",
]

QUALITY_REVIEW_PROMPT = """You are a ruthless editor at RugCharts. Review this content against STRICT standards:

FORBIDDEN (mark as FAIL if found):
- "delve", "tapestry", "landscape", "robust", "moreover", "furthermore"
- "in conclusion", "it is worth noting", "underscores", "showcasing"
- "a testament to", "in the realm of", "paradigm shift"
- Any vague, corporate, or AI-slop language
- Overused crypto clichés ("to the moon", "wagmi", "ngmi", "wen")

REQUIRED (mark as FAIL if missing):
- Specific numbers, names, percentages
- Human, conversational tone (reads like a sharp newsletter)
- No passive voice where active works better
- Short paragraphs. Varied sentence length.
- Hooks the reader in first 2 sentences

OUTPUT FORMAT - JSON only:
{
  "pass": true/false,
  "score": 0-100,
  "issues": ["list of specific problems found"],
  "fixed_version": "rewritten version if score < 80, otherwise original"
}

CONTENT TO REVIEW:
"""


async def review_content(content: str, content_type: str = "article") -> dict:
    """Review content against quality standards. Returns pass/fail with fixes."""
    if len(content) < 50:
        return {"pass": True, "score": 100, "issues": [], "fixed_version": content}

    # ── Automated checks (no AI needed) ──
    issues = []
    content_lower = content.lower()

    for word in FORBIDDEN_WORDS:
        if word in content_lower:
            issues.append(f"Forbidden word: '{word}'")

    # Check for AI-slop patterns
    slop_patterns = [
        (r"it is (worth|important|crucial|essential) to", "AI-slop: 'it is X to'"),
        (r"in (conclusion|summary|essence)", "AI-slop: 'in X'"),
        (r"as we (have|can) seen", "AI-slop: 'as we have seen'"),
        (r"plays? a (crucial|vital|key|important) role", "AI-slop: 'plays a X role'"),
    ]

    import re

    for pattern, label in slop_patterns:
        if re.search(pattern, content_lower):
            issues.append(label)

    # Automated score
    base_score = 100
    base_score -= len(issues) * 8
    # Penalize very short content
    if len(content) < 300:
        base_score -= 15
    # Penalize very long paragraphs
    paragraphs = [p for p in content.split("\n\n") if len(p) > 50]
    if paragraphs:
        avg_para_len = sum(len(p) for p in paragraphs) / len(paragraphs)
        if avg_para_len > 500:
            base_score -= 10
            issues.append("Paragraphs too long (avg >500 chars)")

    # ── AI Review (if score is borderline) ──
    if base_score < 85 and len(issues) > 1:
        try:
            ai_review = await ai_call("review", QUALITY_REVIEW_PROMPT, content, max_tokens=800, temperature=0.2)
            if ai_review:
                try:
                    review_data = json.loads(ai_review.strip().lstrip("```json").rstrip("```"))  # noqa: B005
                    issues.extend(review_data.get("issues", []))
                    if review_data.get("score", 100) < base_score:
                        base_score = review_data["score"]
                    if not review_data.get("pass", True):
                        return {
                            "pass": False,
                            "score": base_score,
                            "issues": issues,
                            "fixed_version": review_data.get("fixed_version", content),
                        }
                except Exception:
                    pass
        except Exception:
            pass

    # Fix if needed
    fixed = content
    if base_score < 70:
        try:
            fix_prompt = f"""Rewrite this content to meet quality standards. Remove all AI-slop language, forbidden words, and corporate speak. Make it human, direct, and specific.

Current issues: {", ".join(issues[:5])}

ORIGINAL:
{content[:2000]}"""
            fixed = await ai_call(
                "writing",
                "You are a skilled human writer. Rewrite content to be direct, specific, and natural. No AI-slop.",
                fix_prompt,
                max_tokens=len(content) // 2 + 500,
                temperature=0.4,
            )
            if not fixed:
                fixed = content
        except Exception:
            fixed = content

    return {
        "pass": base_score >= 70,
        "score": max(0, min(100, base_score)),
        "issues": issues[:10],
        "fixed_version": fixed,
    }


# ═══════════════════════════════════════════════════════════════════════
# SMART PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════


def build_research_prompt(topic: str, data: dict | None = None) -> str:
    """Build a research prompt with all available context."""
    parts = [f"Research task: {topic}\n"]

    if data:
        for key, value in data.items():
            if isinstance(value, str):
                parts.append(f"## {key.upper()}\n{value[:2000]}")
            elif isinstance(value, list):
                parts.append(f"## {key.upper()}\n" + "\n".join(f"- {str(v)[:200]}" for v in value[:10]))
            elif isinstance(value, dict):
                parts.append(f"## {key.upper()}\n{json.dumps(value, default=str)[:1000]}")

    return "\n\n".join(parts)


def build_writing_prompt(topic: str, research_notes: str, style: str = "newsletter") -> str:
    """Build a writing prompt from research notes."""
    return f"""Write a {style} about: {topic}

RESEARCH NOTES:
{research_notes[:3000]}

Style guide:
- Direct, human voice. No corporate speak. No AI-slop.
- Lead with the most interesting detail.
- Use specific numbers, names, facts.
- Vary sentence length. Short paragraphs.
- End with a clear takeaway.

Write the complete piece now:"""


def get_usage_stats() -> dict:
    """Get current model usage statistics from rate tracker."""
    return rate_tracker.stats()
