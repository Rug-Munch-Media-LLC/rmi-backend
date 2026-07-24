"""
Alibaba Cloud Connector - Tongyi Wanxiang AI for Image Generation.
Generate professional graphics for cards, scorecards, marketing assets.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Circuit breaker for Alibaba Cloud image generation API
_alibaba_cb = AsyncCircuitBreaker(
    name="alibaba",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── Alibaba Cloud Config ─────────────────────────────────────

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

# Tongyi Wanxiang endpoints
IMAGE_GENERATION_ENDPOINT = f"{DASHSCOPE_BASE_URL}/services/aigc/text-generation/generation"

class AlibabaConnector:
    """Alibaba Cloud AI services connector."""

    def __init__(self) -> None:
        self.api_key = DASHSCOPE_API_KEY
        self._session = None

    def _get_session(self) -> Any:
        if self._session is None:
            self._session = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        style: str = "professional",
        negative_prompt: str | None = None,
    ) -> dict:
        """
        Generate image using Tongyi Wanxiang.

        Args:
            prompt: Text description of image to generate
            size: Image size (e.g., "1024x1024", "1200x675")
            style: Art style ("professional", "cartoon", "realistic", etc.)
            negative_prompt: What to avoid in the image

        Returns:
            Dict with image_url, thumbnail_url, and metadata
        """
        if not self.api_key:
            logger.error("DASHSCOPE_API_KEY not configured")
            return {"error": "Alibaba API key not configured"}

        # Parse size
        width, height = size.split("x")

        # Build request
        payload = {
            "model": "wanx-v1",  # Tongyi Wanxiang model
            "input": {
                "prompt": prompt,
                "negative_prompt": negative_prompt
                or "blurry, low quality, distorted, ugly, text, watermark",
                "size": f"{width}*{height}",
                "style": style,
            },
            "parameters": {
                "n": 1,  # Number of images
                "seed": 42,  # For reproducibility
            },
        }

        # Circuit breaker check
        if not await _alibaba_cb.can_call():
            stale = _alibaba_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _alibaba_cb.name,
                _alibaba_cb.state,
                "Alibaba circuit OPEN — failing fast",
            )

        try:
            session = self._get_session()
            response = await session.post(IMAGE_GENERATION_ENDPOINT, json=payload)

            if response.status_code == 200:
                result = response.json()

                # Extract image URLs
                images = result.get("output", {}).get("results", [])
                if images and len(images) > 0:
                    output = {
                        "status": "success",
                        "image_url": images[0].get("url"),
                        "thumbnail_url": images[0].get("thumbnail_url"),
                        "id": images[0].get("task_id"),
                        "prompt": prompt,
                        "size": size,
                        "style": style,
                    }
                    _alibaba_cb.set_stale_result(output)
                    await _alibaba_cb.record_success()
                    return output
                else:
                    await _alibaba_cb.record_failure(is_server_error=False)
                    return {"error": "No images generated", "raw": result}
            elif response.status_code >= 500:
                logger.error(f"Alibaba API error: {response.status_code} - {response.text[:200]}")
                await _alibaba_cb.record_failure(is_server_error=True)
                return {
                    "error": f"API error: {response.status_code}",
                    "details": response.text[:500],
                }
            else:
                logger.error(f"Alibaba API error: {response.status_code} - {response.text[:200]}")
                await _alibaba_cb.record_failure(is_server_error=False)
                return {
                    "error": f"API error: {response.status_code}",
                    "details": response.text[:500],
                }

        except Exception as e:
            logger.error(f"Alibaba image generation failed: {e}")
            await _alibaba_cb.record_failure(is_server_error=True)
            return {"error": str(e)}

    async def generate_marketing_image(self, campaign_type: str, content: dict) -> dict:
        """Generate marketing image for campaigns."""

        prompts = {
            "launch": """
            Professional crypto platform launch announcement,
            dark theme, neon accents, "RMI Intelligence Platform" text,
            futuristic trading interface background,
            high quality, 4K, professional marketing graphic
            """,
            "feature_showcase": f"""
            Professional feature showcase graphic,
            "{content.get("feature_name", "Feature")}" prominently displayed,
            trading platform UI elements, charts, graphs,
            dark mode, neon green accents,
            clean modern design, marketing quality
            """,
            "stats_announcement": f"""
            Professional stats announcement graphic,
            "{content.get("stat_value", "1000")}" large number display,
            "{content.get("stat_label", "Users")}" label,
            crypto trading platform aesthetic,
            dark background, neon accents,
            high quality marketing graphic
            """,
            "kol_ranking": """
            Professional KOL ranking graphic,
            leaderboard style, top 10 layout,
            crypto influencer theme,
            dark mode, purple and gold accents,
            trading platform quality,
            high resolution marketing graphic
            """,
        }

        prompt = prompts.get(campaign_type, content.get("custom_prompt", ""))

        return await self.generate_image(
            prompt=prompt,
            size="1200x628",  # Facebook/Twitter link preview size
            style="professional",
            negative_prompt="blurry, low quality, distorted, ugly, amateur, cluttered",
        )

    async def generate_social_post_image(self, post_type: str, data: dict) -> dict:
        """Generate image for social media posts."""

        if post_type == "win_alert":
            prompt = f"""
            Big win celebration graphic,
            crypto trading win alert,
            "+${data.get("pnl_usd", 0):,.0f}" large display,
            green neon style,
            dark background,
            professional trading platform aesthetic,
            high quality social media graphic
            """
        elif post_type == "loss_alert":
            prompt = f"""
            Loss porn graphic,
            crypto trading loss alert,
            "-${data.get("pnl_usd", 0):,.0f}" large display,
            red neon style,
            dark background,
            professional trading platform aesthetic,
            high quality social media graphic
            """
        elif post_type == "rug_alert":
            prompt = """
            Rugpull warning graphic,
            crypto scam alert,
            "RUG PULL" large warning text,
            orange and red warning colors,
            dark background,
            professional security alert aesthetic,
            high quality social media graphic
            """
        else:
            prompt = data.get("custom_prompt", "Professional crypto graphic")

        return await self.generate_image(
            prompt=prompt,
            size="1200x675",  # Twitter optimized
            style="professional",
            negative_prompt="blurry, low quality, distorted, ugly, text overlay, watermark",
        )

    def status(self) -> dict:
        """Check connector status."""
        return {
            "api_key_configured": bool(self.api_key),
            "api_key_prefix": self.api_key[:20] + "..." if self.api_key else "NOT SET",
            "base_url": DASHSCOPE_BASE_URL,
            "models_available": ["wanx-v1"],
        }

# Singleton
_alibaba: AlibabaConnector | None = None

def get_alibaba_connector() -> AlibabaConnector:
    global _alibaba
    if _alibaba is None:
        _alibaba = AlibabaConnector()
    return _alibaba
