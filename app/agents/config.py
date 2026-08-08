"""Agent configuration for the Pydantic AI agent framework.

Single source of truth for the agent fleet settings. The LLM backend is the
local llama.cpp server (Qwen3-4B chat / DeepSeek-R1-8B analysis, API at :8085/:8086).

Per repo convention (app/config.py consolidation notice), configuration
values live in config modules — downstream agent code imports from here
instead of calling ``os.getenv`` directly.
"""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# ── Local LLM backend (llama.cpp, OpenAI-compatible) ─────────────────
LLM_BASE_URL: str = os.getenv("AGENT_LLM_BASE_URL", "http://localhost:8085/v1")
LLM_MODEL: str = os.getenv("AGENT_LLM_MODEL", "qwen-chat")
# llama.cpp serves an OpenAI-compatible API without auth; the OpenAI client
# still requires a non-empty api_key, hence the placeholder.
LLM_API_KEY: str = os.getenv("AGENT_LLM_API_KEY", "not-needed")

# ── Specialist service endpoints ─────────────────────────────────────
SCANNER_API_URL: str = os.getenv("SCANNER_API_URL", "http://localhost:8000/api/v2/scanner/token/scan")
RAG_API_URL: str = os.getenv("RAG_API_URL", "http://localhost:8012/api/v1/search")

# Optional service-to-service auth. Never hardcoded — set in the environment.
SCANNER_API_KEY: str = os.getenv("SCANNER_API_KEY", "")
# RAG auth: fall back to the canonical RMI_RAG_API_KEY (the rmi-rag-api
# X-API-Key required by the fleet — see app/config.py + rmi-rag/.env.rag).
RAG_API_KEY: str = os.getenv("RAG_API_KEY") or os.getenv("RMI_RAG_API_KEY", "")

# ── Runtime tuning ───────────────────────────────────────────────────
HTTP_TIMEOUT: float = float(os.getenv("AGENT_HTTP_TIMEOUT", "10.0"))
DEFAULT_CHAIN: str = os.getenv("AGENT_DEFAULT_CHAIN", "ethereum")
DEFAULT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "0.3"))


def get_agent_model() -> OpenAIChatModel:
    """Build the OpenAI-compatible chat model bound to the local llama.cpp server.

    Returns a model instance (not a provider string) so agents do not need
    an ``OPENAI_API_KEY`` — the llama.cpp endpoint is unauthenticated.
    """
    return OpenAIChatModel(
        LLM_MODEL,
        provider=OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY),
    )
