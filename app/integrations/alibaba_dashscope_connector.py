"""
Alibaba Cloud DashScope Connector - Qwen Models for Content Generation.
Supports: qwen-max, qwen-plus, qwen-turbo, qwen-coder, qwen-vl-max
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Circuit breaker for Alibaba DashScope (Qwen) API
_dashscope_cb = AsyncCircuitBreaker(
    name="dashscope",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── Alibaba DashScope Config ─────────────────────────────────

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

# Available Qwen models
QWEN_MODELS = {
    "qwen-max": {
        "context": 32000,
        "best_for": "Long-form content, detailed copy, highest quality",
        "cost": "$$",
    },
    "qwen-plus": {
        "context": 32000,
        "best_for": "Balanced quality/speed, marketing copy",
        "cost": "$",
    },
    "qwen-turbo": {
        "context": 8000,
        "best_for": "Quick drafts, social posts, fastest",
        "cost": "¢",
    },
    "qwen-coder": {
        "context": 32000,
        "best_for": "Technical docs, API guides, code",
        "cost": "$$",
    },
    "qwen-vl-max": {
        "context": 8000,
        "best_for": "Image + text, vision tasks",
        "cost": "$$$",
    },
}


class AlibabaDashScopeConnector:
    """Alibaba DashScope AI services connector."""

    def __init__(self) -> None:
        self.api_key = DASHSCOPE_API_KEY
        self._session: httpx.AsyncClient | None = None

    def _get_session(self) -> Any:
        if self._session is None:
            self._session = httpx.AsyncClient(
                timeout=120.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def generate_text(
        self,
        prompt: str,
        model: str = "qwen-plus",
        max_tokens: int = 1000,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate text using Qwen models.

        Args:
            prompt: User prompt
            model: Model name (qwen-max, qwen-plus, qwen-turbo, qwen-coder)
            max_tokens: Max tokens in response
            temperature: Creativity (0.0-1.0)
            system_prompt: System instructions

        Returns:
            Dict with generated text and metadata
        """
        if not self.api_key:
            logger.error("DASHSCOPE_API_KEY not configured")
            return {"error": "Alibaba API key not configured"}

        if model not in QWEN_MODELS:
            return {"error": f"Unknown model: {model}. Available: {list(QWEN_MODELS.keys())}"}

        # Build request
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "input": {"messages": messages},
            "parameters": {
                "max_tokens": max_tokens,
                "temperature": temperature,
                "result_format": "text",
            },
        }

        # Circuit breaker check
        if not await _dashscope_cb.can_call():
            stale = _dashscope_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _dashscope_cb.name,
                _dashscope_cb.state,
                "DashScope circuit OPEN — failing fast",
            )

        try:
            session = self._get_session()
            response = await session.post(
                f"{DASHSCOPE_BASE_URL}/services/aigc/text-generation/generation", json=payload
            )

            if response.status_code == 200:
                result = response.json()
                output = result.get("output", {})
                data = {
                    "status": "success",
                    "text": output.get("text", ""),
                    "model": model,
                    "usage": output.get("usage", {}),
                    "prompt": prompt[:100] + "...",
                }
                _dashscope_cb.set_stale_result(data)
                await _dashscope_cb.record_success()
                return data
            elif response.status_code >= 500:
                logger.error(f"DashScope API error: {response.status_code} - {response.text[:200]}")
                await _dashscope_cb.record_failure(is_server_error=True)
                return {
                    "error": f"API error: {response.status_code}",
                    "details": response.text[:500],
                }
            else:
                logger.error(f"DashScope API error: {response.status_code} - {response.text[:200]}")
                await _dashscope_cb.record_failure(is_server_error=False)
                return {
                    "error": f"API error: {response.status_code}",
                    "details": response.text[:500],
                }

        except Exception as e:
            logger.error(f"DashScope text generation failed: {e}")
            await _dashscope_cb.record_failure(is_server_error=True)
            return {"error": str(e)}

    async def generate_marketing_content(
        self, content_type: str, topic: str, details: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Generate marketing content for specific use cases."""

        details = details or {}

        # Content type templates
        templates = {
            "blog_post": {
                "system": "You are a professional crypto marketing copywriter. Write engaging, informative blog posts.",
                "prompt": f"""Write a {details.get("word_count", 600)}-word blog post about: {topic}

Key points to cover:
{chr(10).join(f"- {point}" for point in details.get("key_points", []))}

Tone: Professional but accessible
Include: Call to action at the end
Platform: RMI Intelligence Platform blog
""",
            },
            "twitter_thread": {
                "system": "You are a crypto Twitter expert. Write engaging threads that get shares.",
                "prompt": f"""Create a Twitter thread (8-12 tweets) about: {topic}

Key points:
{chr(10).join(f"- {point}" for point in details.get("key_points", []))}

Format:
- Tweet 1: Hook
- Tweets 2-10: Content
- Final tweet: CTA

Include emojis, hashtags, and @cryptorugmunch tag
Max 280 chars per tweet
""",
            },
            "telegram_post": {
                "system": "You write engaging Telegram posts for crypto communities.",
                "prompt": f"""Write a Telegram announcement about: {topic}

Key points:
{chr(10).join(f"- {point}" for point in details.get("key_points", []))}

Format:
- Start with emoji headline
- Use **bold** for emphasis
- Include links
- Add relevant hashtags

Tone: Exciting but professional
""",
            },
            "email_newsletter": {
                "system": "You write engaging email newsletters for crypto platforms.",
                "prompt": f"""Write an email newsletter about: {topic}

Key points:
{chr(10).join(f"- {point}" for point in details.get("key_points", []))}

Structure:
- Subject line (5 options)
- Opening hook
- Main content
- CTA
- Sign-off

Tone: Friendly, professional, valuable
Length: {details.get("word_count", 400)} words
""",
            },
            "press_release": {
                "system": "You write professional press releases for crypto companies.",
                "prompt": f"""Write a press release about: {topic}

Key points:
{chr(10).join(f"- {point}" for point in details.get("key_points", []))}

Format:
- FOR IMMEDIATE RELEASE
- Headline
- Dateline
- Body paragraphs
- About RMI
- Media contact

Tone: Professional, newsworthy
Length: {details.get("word_count", 500)} words
""",
            },
            "feature_announcement": {
                "system": "You write exciting feature announcements for crypto products.",
                "prompt": f"""Write a feature announcement for: {topic}

Feature details:
{chr(10).join(f"- {point}" for point in details.get("features", []))}

Benefits:
{chr(10).join(f"- {point}" for point in details.get("benefits", []))}

Include:
- Exciting headline
- What it does
- Why it matters
- How to use it
- CTA

Tone: Exciting, clear, benefit-focused
""",
            },
        }

        template = templates.get(content_type)
        if not template:
            return {"error": f"Unknown content type: {content_type}"}

        # Generate using qwen-plus by default
        model = details.get("model", "qwen-plus")

        return await self.generate_text(
            prompt=template["prompt"],
            system_prompt=template["system"],
            model=model,
            max_tokens=details.get("max_tokens", 1500),
            temperature=details.get("temperature", 0.7),
        )

    async def generate_variations(
        self, base_content: str, num_variations: int = 5, platform: str = "twitter"
    ) -> dict[str, Any]:
        """Generate multiple variations of content."""

        prompt = f"""Generate {num_variations} variations of this content for {platform}:

Original:
{base_content}

Requirements:
- Each variation should be unique
- Keep the core message
- Vary the tone slightly (some more excited, some more professional)
- All should be high quality
- Include relevant emojis for {platform}

Output format:
Variation 1: [content]
Variation 2: [content]
...
"""

        return await self.generate_text(prompt=prompt, model="qwen-plus", max_tokens=2000, temperature=0.8)

    async def summarize_content(self, content: str, summary_type: str = "bullet_points") -> dict[str, Any]:
        """Summarize long content into different formats."""

        summary_prompts = {
            "bullet_points": "Summarize this into 5-7 key bullet points:",
            "twitter_thread": "Convert this into a 5-tweet Twitter thread:",
            "one_liner": "Summarize this in one compelling sentence:",
            "email_blurb": "Summarize this into a 100-word email blurb:",
        }

        prompt = f"""{summary_prompts.get(summary_type, "Summarize:")}

{content[:3000]}  # Limit input length
"""

        return await self.generate_text(prompt=prompt, model="qwen-turbo", max_tokens=500, temperature=0.5)

    def list_models(self) -> list[dict[str, Any]]:
        """List available Qwen models."""
        return [{"id": model_id, **info} for model_id, info in QWEN_MODELS.items()]

    def status(self) -> dict[str, Any]:
        """Check connector status."""
        return {
            "api_key_configured": bool(self.api_key),
            "api_key_prefix": self.api_key[:20] + "..." if self.api_key else "NOT SET",
            "base_url": DASHSCOPE_BASE_URL,
            "models_available": list(QWEN_MODELS.keys()),
        }


# Singleton
_alibaba_dashscope: AlibabaDashScopeConnector | None = None


def get_alibaba_dashscope_connector() -> AlibabaDashScopeConnector:
    global _alibaba_dashscope
    if _alibaba_dashscope is None:
        _alibaba_dashscope = AlibabaDashScopeConnector()
    return _alibaba_dashscope
