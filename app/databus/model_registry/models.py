"""
Model Registry - Model & Provider Definitions
==============================================
All AI model configurations, role definitions, and provider limits.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("model_registry")

# ── Model Registry ─────────────────────────────────────────────────

MODELS: dict[str, dict[str, dict[str, Any]]] = {
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
    # ── MISTRAL FALLBACKS ──
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

# ── AI ROLE ARCHITECTURE ────────────────────────────────────────

AI_ROLES = {
    "data_classifier": {
        "name": "Data Classifier",
        "emoji": "\U0001f3f7\ufe0f",
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
        "emoji": "\U0001d54d\ufe0f",
        "description": "X/Twitter posts, Telegram messages. Punchy, engaging.",
        "model": "mistral-small-latest",
        "provider": "mistral",
        "fallback": "llama-3.1-8b-instant",
        "fallback_provider": "groq",
        "budget": {"per_task": 2, "per_day": 20},
        "temperature": 0.8,
    },
    "fact_checker": {
        "name": "Fact Checker",
        "emoji": "\u2705",
        "description": "Verifies claims against known data. Cross-references sources.",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "provider": "openrouter",
        "budget": {"per_task": 3, "per_day": 15},
        "temperature": 0.1,
    },
    "security_auditor": {
        "name": "Security Auditor",
        "emoji": "\U0001f510",
        "description": "Scans code/configs for vulnerabilities, exposed keys, unsafe patterns.",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "provider": "openrouter",
        "budget": {"per_task": 5, "per_day": 10},
        "temperature": 0.1,
    },
    "rag_embedder": {
        "name": "RAG Embedder",
        "emoji": "\U0001f9e0",
        "description": "Vector embeddings. Uses NVIDIA NIM directly.",
        "model": "nvidia/nemo-embed-12b",
        "provider": "nvidia_nim",
        "budget": {"per_day": 50000, "batch_size": 100},
        "temperature": 0.0,
        "strategy": "BATCH: embed 100 docs per call. CACHE: never re-embed.",
    },
}

# ── PROVIDER RATE LIMITS (verified June 2026) ─────────────────────

PROVIDER_LIMITS = {
    "openrouter": {
        "name": "OpenRouter",
        "rpm": 20,
        "rpd_free_no_credits": 50,
        "rpd_free_with_credits": 1000,
        "current_tier": "paid",
        "free_model_suffix": ":free",
        "check_endpoint": "https://openrouter.ai/api/v1/key",
    },
    "groq": {
        "name": "Groq",
        "rpm": 30,
        "rpd": 14400,
        "tpm": 6000,
        "current_tier": "free",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    "mistral": {
        "name": "Mistral",
        "rps": 1,
        "tpm": 500000,
        "tpm_budget": 1000000000,
        "current_tier": "free",
    },
    "nvidia_nim": {
        "name": "NVIDIA NIM",
        "rpm": 100,
        "rpd": 5000,
        "current_tier": "free",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_models": [
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-nano-30b-a3b",
            "nvidia/nv-embedqa-e5-v5",
            "nvidia/llama-3.3-nemotron-super-49b-v1",
            "meta/llama-3.3-70b-instruct",
            "google/gemma-4-31b-it",
            "deepseek-ai/deepseek-v4-flash",
            "mistralai/mistral-large-3-675b-instruct",
            "qwen/qwen3-coder-480b-a35b-instruct",
        ],
    },
}
