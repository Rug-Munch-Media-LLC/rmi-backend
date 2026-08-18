"""
RugCharts Daily Intelligence Briefing
======================================
THE daily market briefing. AI-researched, AI-written, human-quality.
Published 6:30 AM ET to X (@CryptoRugMunch), Telegram, Ghost CMS.

Pipeline:
  1. Gather - all DataBus sources (prices, news, CT, sentiment, fear/greed, memes)
  2. Research - OpenRouter free model analyzes everything
  3. Write - OpenRouter free model produces the final report
  4. Publish - X/Twitter, Telegram, Ghost CMS

Free models used (zero cost):
  Research: nvidia/nemotron-3-super-120b-a12b:free (1M ctx, 120B MoE)
  Writing:  google/gemma-4-26b-a4b-it:free (262K ctx, excellent prose)
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime
from typing import Any

import httpx
from telegram.error import TelegramError

from app.core.logging import get_logger

logger = get_logger("daily_intel")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Free models for each phase
RESEARCH_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
WRITING_MODEL = "google/gemma-4-26b-a4b-it:free"
# Fallback if primary unavailable
FALLBACK_RESEARCH = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
FALLBACK_WRITING = "moonshotai/kimi-k2.6:free"

# Publishing targets
X_ACCOUNT = "CryptoRugMunch"
GHOST_URL = os.getenv("GHOST_URL", "http://172.19.0.3:2368")
GHOST_KEY = os.getenv("GHOST_ADMIN_API_KEY", "") or os.getenv("GHOST_CONTENT_API_KEY", "")


async def _openrouter_chat(model: str, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.5) -> str:
    """Call OpenRouter with a free model."""
    if not OPENROUTER_KEY:
        return ""

    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://rugmunch.io",
                    "X-Title": "RugCharts Daily Intel",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            else:
                logger.warning(f"OpenRouter {model}: {r.status_code} {r.text[:200]}")
                return ""
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"OpenRouter error: {e}")
        return ""


async def _gather_all_data() -> dict[str, Any]:
    """Gather comprehensive data from ALL DataBus sources."""
    data: dict[str, Any] = {
        "market": {},
        "fear_greed": {},
        "news": {},
        "ct": {},
        "social": {},
        "prediction_markets": {},
    }

    # Market data
    try:
        from app.databus.news_provider import get_market_brief

        data["market"] = await get_market_brief()
    except (AttributeError, ImportError):
        pass

    # News intel
    try:
        from app.databus.news_intel import aggregate_all_news

        data["news"] = await aggregate_all_news(limit=20)
    except (AttributeError, ImportError):
        pass

    # CT Rundown
    try:
        from app.databus.x_intel import fetch_ct_rundown

        data["ct"] = await fetch_ct_rundown(limit=15)
    except (AttributeError, ImportError):
        pass

    # Social metrics
    try:
        from app.databus.social_intel import get_social_metrics

        data["social"] = await get_social_metrics()
    except (AttributeError, ImportError):
        pass

    # Fear & Greed
    try:
        from app.databus.news_provider import get_fear_greed

        data["fear_greed"] = await get_fear_greed()
    except (AttributeError, ImportError):
        pass

    # Prediction markets
    try:
        from app.databus.news_provider import get_prediction_markets

        data["prediction_markets"] = await get_prediction_markets(limit=5)
    except (AttributeError, ImportError):
        pass

    return data


def _build_research_context(data: dict[str, Any]) -> str:
    """Build comprehensive context for the research model."""
    parts = []

    # Market snapshot
    market = data.get("market", {})
    if market.get("brief"):
        parts.append(f"## MARKET SNAPSHOT\n{market['brief']}")

    # Fear & Greed
    fg = data.get("fear_greed", {})
    if fg.get("value"):
        parts.append(f"## FEAR & GREED INDEX\n{fg['value']}/100 - {fg.get('classification', 'Neutral')}")

    # News headlines
    news = data.get("news", {})
    articles = news.get("articles", [])
    if articles:
        headlines = "\n".join(
            f"- [{a.get('sentiment', {}).get('sentiment', '➖')}] {a.get('title', '')}" for a in articles[:15]
        )
        parts.append(f"## TOP HEADLINES\n{headlines}")

    # CT Pulse
    ct = data.get("ct", {})
    rundown = ct.get("rundown", [])
    if rundown:
        ct_pulse = "\n".join(f"- @{s.get('author_handle', '?')}: {s.get('text', '')[:150]}" for s in rundown[:10])
        parts.append(f"## CRYPTO TWITTER PULSE\n{ct_pulse}")

    # Social metrics
    social = data.get("social", {})
    if social.get("trending_topics"):
        topics = social["trending_topics"]
        parts.append(f"## TRENDING TOPICS\n{', '.join(list(topics.keys())[:10])}")

    # Prediction markets
    pm = data.get("prediction_markets", {})
    pmarkets = pm.get("markets", [])
    if pmarkets:
        pm_str = "\n".join(f"- {m.get('title', '')[:80]}: ${m.get('volume', 0):,.0f} vol" for m in pmarkets[:3])
        parts.append(f"## PREDICTION MARKETS\n{pm_str}")

    return "\n\n".join(parts)


WRITING_STANDARDS = """You are a senior financial writer for RugCharts Daily Intelligence.
You write the kind of daily briefing that professionals pay for: sharp,
conversational, data-rich, and worth forwarding. Every sentence earns its place.

WRITING STANDARDS:
- Voice: Like a well-informed friend who actually trades. Direct, confident,
  occasionally wry. Never stiff. Never hype. Just sharp analysis.
- No AI-isms: never use "delve", "tapestry", "landscape", "robust", "moreover",
  "furthermore", "in the realm of", "it is worth noting". Write like a human.
- Lead with the hook. First 2 sentences must grab the reader: what happened,
  why it matters, and where the market stands RIGHT NOW.
- Be specific: every claim gets a number, name, or percentage. "Bitcoin is up"
  is weak — "BTC ripped 4.2% to $67,800, blowing through resistance" is sharp.
- Paragraph structure: short paragraphs with proper line breaks. Every
  paragraph is 2-4 sentences max. No wall of text. No bullet lists in the
  body — bullets only for the TOP STORIES section.
- Sections: each section has a brief intro paragraph THEN the detail. Not
  just a list of facts. Tell the story, don't dump data.

FORMAT — Follow this EXACT structure with proper paragraph spacing:

# RUGCHARTS DAILY INTELLIGENCE
## {Date}

### MARKET SNAPSHOT
[2-3 sentences. Opening hook that sets the scene: what moved, by how much,
the key driver. Then context: why it happened, what it means for today.]

[1-2 sentences on what to watch next — levels, events, or key data points
that will determine the next move.]

### TOP STORIES
[3-5 bullet points. Each bullet: the story in bold, then a brief why-it-matters
in plain text. Use numbers, names, percentages.]

• **Story headline** — Why it matters in one or two sentences with specific data.
• **Next story** — Context and significance.
• **Third story** — What happened and what comes next.

### SENTIMENT CHECK
[1-2 paragraphs on market mood. Fear & Greed index number, social sentiment,
what Crypto Twitter is arguing about today. Include specific tweets or takes
when notable. End with whether the crowd is right or wrong.]

### MEMES & CULTURE
[The fun section. What's trending on CT right now — the memes, the narratives,
the cultural moments. One paragraph of sharp observation. This is where your
voice shines.]

### RISK RADAR
[1-2 paragraphs on what to watch out for today: active hacks, regulatory
moves, scam channels shilling something dangerous, smart money de-risking.
Be specific: name the token, the hack, the regulator action.]

### BOTTOM LINE
[ONE short paragraph. 2-3 sentences max. The actionable takeaway. What the
reader should DO with this information today.]

---
Published by RugCharts Daily Intelligence
📊 rugmunch.io/news  |  🤖 @RugMunchBot  |  🐦 @CryptoRugMunch"""


async def generate_daily_intel(publish: bool = False, **kw) -> dict[str, Any] | None:
    """Generate the complete Daily Intelligence Briefing with quality review.

    Pipeline: Gather → Research → Write → Review → Fix → Publish
    All AI calls through model_registry (free models only).
    Ghost is canonical. X/Telegram are syndication.

    Args:
        publish: If True, publish to Ghost (primary) + X/Telegram (syndication)
    """
    from app.databus.model_registry import ai_call, review_content

    # ── PHASE 0: Gather all data ──
    logger.info("Daily Intel: gathering data...")
    data = await _gather_all_data()
    context = _build_research_context(data)

    if len(context) < 100:
        return {"error": "Insufficient data gathered"}

    # ── PHASE 1: Research ──
    logger.info("Daily Intel: research phase (free model)...")
    research_notes = await ai_call(
        "research",
        "You are a senior crypto research analyst. Analyze data and produce structured research notes with specific numbers and names.",
        f"Analyze today's crypto market data. Identify top 3 stories, sentiment drivers, risks, cultural trends, and on-chain signals:\n\n{context}",
        max_tokens=1200,
        temperature=0.3,
    )
    if not research_notes:
        research_notes = "Research phase: raw data analysis (no AI available).\n\n" + context[:2000]

    # ── PHASE 2: Writing ──
    logger.info("Daily Intel: writing phase (free model)...")
    now = datetime.now(UTC)
    date_str = now.strftime("%A, %B %d, %Y")

    writing_prompt = f"""Write today's RugCharts Daily Intelligence.

Today: {date_str}

Research notes:
{research_notes}

Raw context:
{context[:2500]}

FORMAT:
# RUGCHARTS DAILY INTELLIGENCE
## {date_str}

### MARKET SNAPSHOT
2-3 sentences on overall market direction and key drivers.

### TOP STORIES
3-5 bullet points with specific numbers, names, and context.

### SENTIMENT CHECK
Market mood, social sentiment, fear/greed, what CT is saying.

### MEMES & CULTURE
What's trending on CT. Notable narratives. Cultural moments.

### RISK RADAR
Scams, hacks, regulatory actions. What to avoid today.

### BOTTOM LINE
1-2 sentence actionable takeaway.
"""

    final_report = await ai_call("writing", WRITING_STANDARDS, writing_prompt, max_tokens=2000, temperature=0.7)

    if not final_report or len(final_report) < 200:
        headlines = data.get("news", {}).get("articles", [])
        final_report = f"""# RUGCHARTS DAILY INTELLIGENCE
## {date_str}

### MARKET SNAPSHOT
{data.get("market", {}).get("brief", "Market data unavailable")}

### TOP STORIES
{chr(10).join("- " + a.get("title", "") for a in headlines[:5])}

### SENTIMENT CHECK
Fear & Greed: {data.get("fear_greed", {}).get("value", "?")}/100

### BOTTOM LINE
Stay sharp. Data-driven decisions only."""

    # ── PHASE 3: Review ──
    logger.info("Daily Intel: quality review...")
    review = await review_content(final_report, "daily_briefing")

    if not review["pass"] and review.get("fixed_version"):
        logger.info(f"Daily Intel: auto-fixed (score {review['score']}/100)")
        final_report = review["fixed_version"]
    else:
        logger.info(f"Daily Intel: passed review ({review['score']}/100)")

    report_data = {
        "report": final_report,
        "date": date_str,
        "research_model": "free_openrouter",
        "writing_model": "free_openrouter",
        "review_score": review["score"],
        "review_issues": review.get("issues", []),
        "data_sources": sum(1 for v in data.values() if v),
        "generated_at": datetime.now(UTC).isoformat(),
        "published": False,
        "source": "daily_intel_briefing",
    }

    # ── PHASE 4: Publish (Ghost first, then syndicate) ──
    if publish:
        pub_results = await _publish_briefing(final_report, date_str)
        report_data["published"] = True
        report_data["publish_results"] = pub_results

    return report_data


async def _publish_briefing(report: str, date_str: str) -> dict[str, Any]:
    """Publish the briefing to all channels."""
    results = {}

    # ── X/Twitter via xurl ──
    x_result = await _publish_to_x(report, date_str)
    results["x"] = x_result

    # ── Ghost CMS ──
    ghost_result = await _publish_to_ghost(report, date_str)
    results["ghost"] = ghost_result

    # ── Telegram (via send_message or bot) ──
    tg_result = await _publish_to_telegram(report, date_str)
    results["telegram"] = tg_result

    return results


async def _publish_to_x(report: str, date_str: str) -> dict[str, Any]:
    """Publish briefing summary to X @CryptoRugMunch via xurl."""
    # Extract top story + TLDR for tweet thread
    lines = report.split("\n")
    headline = ""
    tldr = ""

    for line in lines:
        if line.startswith(("### MARKET SNAPSHOT", "##")):
            continue
        if not headline and len(line.strip()) > 20:
            headline = line.strip().lstrip("#- ")[:240]
        if "BOTTOM LINE" in line:
            # Grab the next line
            idx = lines.index(line)
            if idx + 1 < len(lines):
                tldr = lines[idx + 1].strip().lstrip("- ")[:240]

    if not headline:
        headline = f"RugCharts Daily Intelligence - {date_str}"

    tweet_text = f"📊 {headline}\n\n{tldr}\n\nFull report: https://rugmunch.io/news"

    try:
        result = subprocess.run(
            ["xurl", "post", tweet_text, "--auth", "oauth2"], capture_output=True, text=True, timeout=20, shell=False
        )
        if result.returncode == 0:
            return {"status": "posted", "platform": "x", "length": len(tweet_text)}
        else:
            return {"status": "failed", "platform": "x", "error": result.stderr[:200]}
    except Exception as e:
        return {"status": "error", "platform": "x", "error": str(e)[:200]}


async def _publish_to_ghost(report: str, date_str: str) -> dict[str, Any]:
    """Publish briefing to Ghost CMS under 'daily' tag."""
    if not GHOST_URL or not GHOST_KEY:
        return {"status": "skipped", "reason": "Ghost not configured"}

    try:
        # Extract title from report
        report.split("\n")
        title = f"Daily Intelligence - {date_str}"

        # Convert markdown to Ghost HTML
        html = _markdown_to_html(report)

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{GHOST_URL}/ghost/api/admin/posts/",
                headers={
                    "Authorization": f"Ghost {GHOST_KEY}",
                    "Content-Type": "application/json",
                    "Accept-Version": "v5.0",
                },
                json={
                    "posts": [
                        {
                            "title": title,
                            "html": html,
                            "status": "published",
                            "tags": ["daily", "intelligence", "briefing"],
                            "feature_image": "",
                        }
                    ]
                },
            )
            if r.status_code in (200, 201):
                return {"status": "published", "platform": "ghost"}
            else:
                return {"status": "failed", "platform": "ghost", "error": r.text[:200]}
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {"status": "error", "platform": "ghost", "error": str(e)[:200]}


async def _publish_to_telegram(report: str, date_str: str) -> dict[str, Any]:
    """Send briefing to Telegram channel with clean HTML formatting."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    channel = os.getenv("CHANNEL_NEWS", "") or os.getenv("CHANNEL_ALERTS", "")

    if not bot_token or not channel:
        return {"status": "skipped", "reason": "Telegram not configured"}

    lines = report.split("\n")
    tg_parts: list[str] = []
    current_section = ""
    in_bullets = False

    for line in lines:
        stripped = line.strip()

        # Title
        if stripped.startswith("# ") and not stripped.startswith("##"):
            title = stripped.lstrip("# ")
            tg_parts.append(f"<b>{title}</b>")
            continue

        # Date subtitle
        if stripped.startswith("## ") and "date" not in stripped.lower() and "intelligence" not in stripped.lower():
            tg_parts.append(f"<i>{stripped.lstrip('## ').strip()}</i>\n")
            continue

        # Section headers
        if stripped.startswith("### "):
            if in_bullets:
                tg_parts.append("")
                in_bullets = False
            current_section = stripped.lstrip("### ").strip()
            tg_parts.append(f"\n<b>{current_section}</b>\n")
            continue

        # Bullet points
        if stripped.startswith("- ") or stripped.startswith("• "):
            in_bullets = True
            bullet_text = stripped.lstrip("- •").strip()
            # Handle **bold** markers in bullets
            bullet_text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", bullet_text)
            if " — " in bullet_text:
                parts = bullet_text.split(" — ", 1)
                tg_parts.append(f"  • <b>{parts[0]}</b> — {parts[1]}")
            else:
                tg_parts.append(f"  • {bullet_text}")
            continue
        else:
            if in_bullets:
                tg_parts.append("")
                in_bullets = False

        # Empty line = paragraph break
        if not stripped:
            tg_parts.append("")
            continue

        # Regular paragraph text
        paragraph = stripped
        paragraph = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", paragraph)
        paragraph = re.sub(r"\*(.+?)\*", r"<i>\1</i>", paragraph)
        tg_parts.append(paragraph)

    # Now build the final Telegram message
    tg_text = "\n".join(tg_parts)

    # Split into multiple messages if too long (4096 char limit)
    max_len = 4000
    messages = []
    if len(tg_text) > max_len:
        # Try to split on section boundaries
        sections = tg_text.split("\n<b>")
        current = ""
        for section in sections:
            if section.startswith("RUGCHARTS"):
                current = f"<b>{section}" if not current else section
                continue
            section_tag = f"<b>{section}" if not section.startswith("<b>") else section
            if len(current) + len(section_tag) < max_len:
                current += section_tag
            else:
                if current:
                    messages.append(current[:max_len])
                current = section_tag
        if current:
            messages.append(current[:max_len])
    else:
        messages = [tg_text]

    results = []
    for msg in messages[:4]:  # Max 4 messages per briefing
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": channel,
                        "text": msg.strip(),
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                    },
                )
                if r.status_code == 200:
                    results.append({"status": "sent", "length": len(msg)})
                else:
                    results.append({"status": "failed", "error": r.text[:200]})
        except (httpx.HTTPError, httpx.TimeoutException, TelegramError) as e:
            results.append({"status": "error", "error": str(e)[:200]})

    return {"status": "sent" if any(r["status"] == "sent" for r in results) else "failed", "messages": results}


def _markdown_to_html(md: str) -> str:
    """Simple markdown to HTML conversion for Ghost."""
    html = md
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = html.replace("\n\n", "</p><p>").replace("\n", "<br>")
    html = f"<p>{html}</p>"
    html = html.replace("<p><h", "<h").replace("</h2></p>", "</h2>").replace("</h1></p>", "</h1>")
    return html
