"""Model access map - who has tokens for what.

Updated whenever API keys change. Run with `python -m infra.model_map` from
the repo root, or `bash infra/model_map.sh` for a shell-only view.

ACTIVE providers (real keys):
  - openrouter  → DeepSeek, MiniMax, Gemini, Mistral, Llama (universal)
  - deepseek    → direct DeepSeek API
  - groq        → fast inference (llama-3.3-70b, llama-3.1-8b)

PLACEHOLDER (no real key, skipped):
  - gemini, zai, minimax, mistral-direct, kimi

Per v4.0 master stack: all hosted APIs are routed through LiteLLM proxy at
http://rmi-litellm:4000 with the sovereign OpenAI-compatible interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Provider:
    name: str
    active: bool
    key_len: int
    key_prefix: str
    models: list[str]


def _read_env(name: str) -> str:
    return os.environ.get(name, "")


def _check(name: str, env_var: str, models: list[str], min_len: int = 30) -> Provider:
    key = _read_env(env_var)
    active = len(key) >= min_len
    return Provider(
        name=name,
        active=active,
        key_len=len(key),
        key_prefix=key[:8] if key else "",
        models=models,
    )


def get_model_map() -> list[Provider]:
    """Read all API keys from env. Returns provider status."""
    return [
        _check(
            "openrouter",
            "OPENROUTER_API_KEY",
            [
                "deepseek/deepseek-chat",
                "MiniMax/MiniMax-M3",
                "google/gemini-2.5-flash",
                "mistralai/mistral-large-latest",
                "meta-llama/llama-3-70b-instruct",
            ],
        ),
        _check("deepseek", "DEEPSEEK_API_KEY", ["deepseek-chat"], min_len=30),
        _check(
            "groq",
            "GROQ_API_KEY",
            [
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
            ],
        ),
        _check("gemini", "GEMINI_API_KEY", ["gemini-2.5-flash"], min_len=30),
        _check("minimax", "MiniMax_API_KEY", ["MiniMax-M3"], min_len=30),
        _check("mistral", "MISTRAL_API_KEY", ["mistral-large-latest"], min_len=30),
        _check("kimi", "KIMI_API_KEY", ["kimi-k2-0711-preview"], min_len=30),
        _check("ollama-cloud", "OLLAMA_API_KEY", ["llama3.2", "qwen2.5"], min_len=30),
    ]


def print_map() -> None:
    """Print human-readable model access map."""
    print(f"{'PROVIDER':<14}  {'STATUS':<10}  {'LEN':<5}  MODELS")
    print("─" * 70)
    for p in get_model_map():
        status = "ACTIVE" if p.active else "PLACEHLD"
        models = ", ".join(p.models[:3])
        if len(p.models) > 3:
            models += f" +{len(p.models) - 3}"
        print(f"{p.name:<14}  {status:<10}  {p.key_len:<5}  {models}")


if __name__ == "__main__":
    print_map()
