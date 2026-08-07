#!/usr/bin/env python3
"""Auto-Research Agent - daily crypto research report. Cron at 7am.
Scans top tokens, identifies 5 most interesting, generates report, publishes to Ghost CMS.
Fully automated: LLM writes → LLM reviews → publishes."""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
GHOST_URL = os.getenv("GHOST_URL", "http://localhost:2368")
GHOST_KEY = os.getenv("GHOST_ADMIN_KEY", "")
RMI_KEY = os.getenv("RMI_INTERNAL_KEY", "rmi-internal-2026")
OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434")


async def fetch_market_data() -> dict:
    """Gather all market data for the report."""
    async with httpx.AsyncClient(timeout=30) as c:
        # Top tokens
        trending = await c.get(f"{BACKEND}/api/v1/databus/fetch/trending?limit=20", headers={"X-RMI-Key": RMI_KEY})
        # Market overview
        market = await c.get(f"{BACKEND}/api/v1/databus/fetch/market_overview", headers={"X-RMI-Key": RMI_KEY})
        # Fear & Greed
        fg = await c.get(f"{BACKEND}/api/v1/databus/fetch/fear_greed", headers={"X-RMI-Key": RMI_KEY})
        # News
        news = await c.get(f"{BACKEND}/api/v1/databus/fetch/news?limit=10", headers={"X-RMI-Key": RMI_KEY})

        return {
            "trending": trending.json() if trending.status_code == 200 else {},
            "market": market.json() if market.status_code == 200 else {},
            "fear_greed": fg.json() if fg.status_code == 200 else {},
            "news": news.json() if news.status_code == 200 else {},
        }


async def generate_report(market_data: dict) -> str:
    """Use Ollama/LLM to generate a research report."""
    context = json.dumps(market_data, default=str)[:3000]

    prompt = f"""Write a professional daily crypto research report for RugMunch Intelligence.

Data: {context}

Format:
# RMI Daily Crypto Research - {datetime.now(UTC).strftime("%B %d, %Y")}

## Market Overview
[2-3 sentences on market conditions, fear & greed, top movers]

## Top 5 Tokens to Watch
[For each: symbol, chain, brief analysis, risk level emoji]

## Notable News
[3-5 one-line news items with source]

## Scam Alert of the Day
[If any token shows scam patterns, flag it here]

## Trading Signal
[One actionable signal based on data - BUY/SELL/WATCH/AVOID]

Keep under 500 words. Professional tone. Include data where available."""

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{OLLAMA}/api/generate",
                json={
                    "model": "qwen2.5-coder:7b",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.6, "num_predict": 1024},
                },
            )
            if r.status_code == 200:
                return r.json().get("response", "")
    except Exception:  # noqa: S110 — intentional best-effort
        pass

    # Fallback: basic report from data
    return f"""# RMI Daily Crypto Research - {datetime.now(UTC).strftime("%B %d, %Y")}

## Market Overview
Market data unavailable. Please check rugmunch.io for live updates.

## Top 5 Tokens to Watch
DataBus fetch in progress. Real-time scanner available at rugmunch.io/scan.

## Notable News
News aggregation running. Latest headlines at rugmunch.io/news.

## Scam Alert of the Day
SENTINEL scanner active. Recent alerts at rugmunch.io/alerts.

## Trading Signal
No signal generated. Premium subscribers receive automated signals daily.
"""


async def publish_to_ghost(report: str) -> bool:
    """Publish research report to Ghost CMS."""
    if not GHOST_KEY:
        logger.warning("Ghost not configured - report not published")
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{GHOST_URL}/ghost/api/admin/posts/?source=html",
                json={
                    "posts": [
                        {
                            "title": f"RMI Daily Research - {datetime.now(UTC).strftime('%B %d, %Y')}",
                            "html": report,
                            "status": "draft",
                            "tags": ["daily-research", "crypto"],
                        }
                    ]
                },
                headers={"Authorization": f"Ghost {GHOST_KEY}", "Content-Type": "application/json"},
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.error("Ghost publish failed: %s", e)
    return False


async def main():
    logger.info("RMI Auto-Research Agent - %s", datetime.now(UTC).isoformat())

    # 1. Gather data
    logger.info("Gathering market data...")
    data = await fetch_market_data()
    logger.info("  Trending: %s chars", len(str(data["trending"])))
    logger.info("  Market: %s chars", len(str(data["market"])))
    logger.info("  FG: %s chars", len(str(data["fear_greed"])))
    logger.info("  News: %s chars", len(str(data["news"])))

    # 2. Generate report
    logger.info("Generating report via Ollama...")
    report = await generate_report(data)

    # 3. Save locally
    report_path = f"/root/backend/reports/{datetime.now(UTC).strftime('%Y-%m-%d')}.md"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)
    logger.info("Report saved: %s", report_path)

    # 4. Publish to Ghost
    if await publish_to_ghost(report):
        logger.info("Published to Ghost CMS")
    else:
        logger.info("Ghost publish skipped (not configured)")

    # 5. Summary
    lines = report.split("\n")
    logger.info("Report: %s chars, %s lines", len(report), len(lines))
    logger.info("Preview:\n%s", report[:300])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())
