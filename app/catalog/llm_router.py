"""T27 LLM Router - sovereign-first LiteLLM proxy for catalog AI.

Per v4.0 §T28 (News analysis), §T29 (Report generation).

Self-hosted LiteLLM proxy at litellm.rugmunch.io routes to:
  - DeepSeek-V3 (cost-effective analysis)
  - Qwen (summaries)
  - Local Llama-3 (free-tier fallback)

We never call OpenAI directly. If LiteLLM is unreachable, the catalog
operations that need LLM analysis return None/empty with a logged warning
rather than failing the whole request.
"""

from __future__ import annotations

import os

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Config ─────────────────────────────────────────────────────────
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm.rugmunch.io")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
DEFAULT_MODEL = os.getenv("LITELLM_DEFAULT_MODEL", "deepseek-v3")

NEWS_ANALYSIS_PROMPT = """You are an analyst at RugMunch Intelligence, a crypto
scam-detection platform. Analyze the following news item and produce a
structured Markdown summary.

NEWS ITEM:
- Title: {title}
- Source: {source}
- Published: {published_at}
- Body: {body_truncated_to_2000_chars}

Produce a summary with these sections (use Markdown headers):

## Summary
2-3 sentence plain-English summary.

## Affected Tokens
List any tokens mentioned, with their chain and address if known.

## Affected Wallets
List any wallets mentioned.

## Sentiment
One of: bullish | bearish | neutral | risk-elevating | risk-reducing
1-sentence justification.

## RugMunch Action
What should our platform do in response? Options:
- (none)
- (flag mentioned tokens for re-scan)
- (alert subscribers)
- (update deployer reputation)
- (cross-chain entity resolution trigger)

Be concise. Do not speculate beyond what the article says.
"""

class LLMRouter:
    """Async client for the self-hosted LiteLLM proxy.

    Falls back to None if the proxy is unreachable - catalog operations
    that need LLM output will skip the AI analysis but still complete
    the rest of the workflow.
    """

    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        self.url = (url or LITELLM_URL).rstrip("/")
        self.api_key = api_key or LITELLM_API_KEY
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(base_url=self.url, headers=headers, timeout=30.0)
        return self._client

    async def chat(
        self,
        prompt: str,
        model: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str | None:
        """Send a chat completion. Returns text or None on failure."""
        try:
            client = await self._get_client()
            r = await client.post(
                "/chat/completions",
                json={
                    "model": model or DEFAULT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            if r.status_code != 200:
                log.warning("llm_http_%d: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning("llm_chat_fail: %s", e)
            return None

    async def analyze_news(
        self,
        news_item: NewsItem,  # type: ignore
    ) -> str | None:
        """Generate AI analysis for a NewsItem. Per v4.0 §T28."""
        prompt = NEWS_ANALYSIS_PROMPT.format(
            title=news_item.title,
            source=news_item.source,
            published_at=news_item.published_at.isoformat(),
            body_truncated_to_2000_chars=(news_item.body_markdown or news_item.summary)[:2000],
        )
        return await self.chat(prompt, max_tokens=800, temperature=0.3)
