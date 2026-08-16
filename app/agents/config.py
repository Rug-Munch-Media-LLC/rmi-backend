"""Agent configuration for the Pydantic AI agent framework.

Single source of truth for the agent fleet settings. The LLM backend is the
free remote snappy tier (DeepSeek V4 Flash Free, OpenAI-compatible). The
former local model default was removed 2026-08-16; operators can still point
elsewhere via env overrides.

Per repo convention (app/config.py consolidation notice), configuration
values live in config modules — downstream agent code imports from here
instead of calling ``os.getenv`` directly.
"""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# ── Remote LLM backend (OpenAI-compatible, Bearer auth) ─────────────────
# Defaults mirror ``app.llm_config.get_snappy_config()`` (fast tier). The
# base URL is the API base WITHOUT the ``/chat/completions`` suffix —
# ``OpenAIProvider`` appends it. The API key is resolved from the same env
# var the rest of the app uses.
LLM_BASE_URL: str = os.getenv("AGENT_LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL: str = os.getenv("AGENT_LLM_MODEL", "deepseek-v4-flash-free")
LLM_API_KEY: str = os.getenv("AGENT_LLM_API_KEY", "") or os.getenv("OPENROUTER_BACKUP_API_KEY", "")

# ── Specialist service endpoints ─────────────────────────────────────
SCANNER_API_URL: str = os.getenv("SCANNER_API_URL", "http://localhost:8000/api/v2/scanner/token/scan")
RAG_API_URL: str = os.getenv("RAG_API_URL", "http://localhost:8012/api/v1/search")

# Optional service-to-service auth. Never hardcoded — set in the environment.
SCANNER_API_KEY: str = os.getenv("SCANNER_API_KEY", "")
# RAG auth: fall back to the canonical RMI_RAG_API_KEY (the rmi-rag-api
# X-API-Key required by the fleet — see app/config.py + rmi-rag/.env.rag).
RAG_API_KEY: str = os.getenv("RAG_API_KEY", "") or os.getenv("RMI_RAG_API_KEY", "")

# ── Runtime tuning ───────────────────────────────────────────────────
HTTP_TIMEOUT: float = float(os.getenv("AGENT_HTTP_TIMEOUT", "10.0"))
DEFAULT_CHAIN: str = os.getenv("AGENT_DEFAULT_CHAIN", "ethereum")
DEFAULT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "0.3"))


def get_agent_model() -> OpenAIChatModel:
    """Build the OpenAI-compatible chat model bound to the remote snappy tier.

    Returns a model instance (not a provider string) authenticated with the
    configured API key (``OPENROUTER_BACKUP_API_KEY`` by default).
    """
    return OpenAIChatModel(
        LLM_MODEL,
        provider=OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY or "not-needed"),
    )
