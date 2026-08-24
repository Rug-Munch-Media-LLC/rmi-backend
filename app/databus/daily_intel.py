"""
The Daily Rug — Premium Crypto Intelligence Briefing
=====================================================
THE daily market briefing people would pay for.
AI-researched, human-written quality, no hype, no filler.
Published 6:30 AM ET to Ghost CMS, Telegram (@rmicryptonews),
X (@CryptoRugMunch), and Farcaster.

Pipeline:
  1. Gather - all DataBus sources (prices, news, CT, sentiment, fear/greed, memes)
  2. Research - OpenRouter free model analyzes everything
  3. Write - OpenRouter free model produces the final report
  4. Publish - Ghost CMS → syndicate to X, Telegram, Farcaster

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
# Fallbacks (all verified live 2026-08-18)
FALLBACK_RESEARCH = "nvidia/nemotron-3-nano-30b-a3b:free"
FALLBACK_WRITING = "google/gemma-4-31b-it:free"

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
                    "X-Title": "The Daily Rug",
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
    """Gather comprehensive data from ALL DataBus sources + RAG knowledge base."""
    data: dict[str, Any] = {
        "market": {},
        "fear_greed": {},
        "news": {},
        "ct": {},
        "social": {},
        "prediction_markets": {},
        "rag_top_stories": [],
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

    # ── RAG-powered top stories from our knowledge base ──
    try:
        from app.integrations.rugbrain_client import rugbrain_search

        # Query our ingested news_v2 for the most impactful stories right now
        rag_stories = await rugbrain_search(
            query="most important crypto news today market moving events",
            collections=["news_v2"],
            limit=10,
        )
        if rag_stories:
            data["rag_top_stories"] = rag_stories
            logger.info("Daily Rug: pulled %d top stories from RAG news_v2", len(rag_stories))

        # Also search scam_intel for any active threats to include
        rag_scams = await rugbrain_search(
            query="active scams exploits hacks rug pulls this week",
            collections=["threat_intel_v2"],
            limit=5,
        )
        if rag_scams:
            data["rag_scam_intel"] = rag_scams
            logger.info("Daily Rug: pulled %d scam intel hits from RAG", len(rag_scams))
    except (ImportError, AttributeError, Exception) as e:
        logger.debug("Daily Rug: RAG top stories unavailable: %s", e)


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

    # RAG top stories (reranker-scored from our news_v2 ingestion)
    rag_stories = data.get("rag_top_stories", [])
    if rag_stories:
        rag_lines = []
        for s in rag_stories[:8]:
            text = s.get("text", "")
            score = s.get("score", 0)
            coll = s.get("collection", "news_v2")
            title = text[:120] if text else "(no text)"
            rag_lines.append(f"- [score={score:.2f}] {title}  |  source={coll}")
        parts.append("## RAG-IMPORTANT STORIES (reranker-scored)\n" + "\n".join(rag_lines))

    # RAG scam intel
    rag_scams = data.get("rag_scam_intel", [])
    if rag_scams:
        scam_lines = []
        for s in rag_scams[:5]:
            text = s.get("text", "")
            score = s.get("score", 0)
            scam_lines.append(f"- [activeness={score:.2f}] {text[:150]}")
        parts.append("## ACTIVE SCAMS / HACKS / EXPLOITS (from RAG)\n" + "\n".join(scam_lines))

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


WRITING_STANDARDS = """You are the senior writer for The Daily Rug — a premium
crypto intelligence briefing that people would actually pay to read.

You write at the level of Axios Pro, Sherwood News, or Blockworks Research.
Every issue must deliver signal the reader cannot get elsewhere.

VOICE & TONE:
- Sharp, editorial, confident. Write like Matt Levine crossed with a crypto
  trader who has seen it all. Dry humor is welcome. Cynicism is earned.
- Every sentence earns its place. If it's obvious, cut it. If it's vague,
  make it specific. If it's boring, kill it.
- Lead with the hook. First paragraph must reward the reader for opening:
  what happened, why it matters, and what comes next — all in 3-4 sentences.

WRITING STANDARDS (these are hard rules):
- No AI-isms ever: "delve", "tapestry", "landscape", "robust", "moreover",
  "furthermore", "in the realm of", "it is worth noting", "a myriad of",
  "a plethora of" — all banned. Write like a human.
- Be specific: every claim gets a number, name, or percentage. "Bitcoin is
  up" is weak — "BTC ripped 4.2% to $67,800, blowing through resistance on
  2.3x average volume" is what we pay for.
- Paragraph structure: 2-4 sentences max per paragraph. Short, punchy,
  authoritative. No wall of text. Never. A paragraph is one idea, well said.
- BOLD in TOP STORIES: each bullet starts with the story headline in bold,
  then a plain-text why-it-matters sentence. Like: "• **BTC reclaims $68K** —
  The move came on $347M in spot ETF inflows, the largest single day since May."
- Narrative arc: each section tells a story, not a list of facts. Market
  Snapshot opens the scene. Sentiment Check tells you what traders feel and
  whether they're right. Risk Radar names specific tokens, wallets, or
  protocols. Bottom Line gives ONE actionable takeaway.

FORMAT — Follow this structure exactly:

# THE DAILY RUG
## {Date}

### MARKET SNAPSHOT
[Opening hook — 3-4 sentences. What moved, by how much, the key driver.
Then context: why it happened, what it means for today's session.]

[1-2 sentences on the key level or event to watch.]

### TOP STORIES
[3-5 bullet points. Each: bold headline + context with data.]

• **{Story}** — Why it matters in 1-2 sentences with specific numbers.
• **{Story}** — Context and significance. Never vague.
• **{Story}** — What happened and what the market should watch next.

### SENTIMENT CHECK
[1-2 paragraphs. Fear & Greed, social sentiment, what CT is arguing about.
Quote a notable tweet if it captures the mood. End with whether the crowd
is positioned correctly or about to get wrecked.]

### MEMES & CULTURE
[1 paragraph. What's actually funny, interesting, or culturally notable in
crypto right now. The memes, the narratives, the moments that define the
vibe. This is where your voice gets to have fun.]

### RISK RADAR
[1-2 paragraphs on real threats: active exploits, regulatory moves, scam
channels shilling obvious rugs, smart money rotation that reveals fear.
Name names. Specific protocols, tokens, wallets, or actors.]

### BOTTOM LINE
[ONE short paragraph. 2-3 sentences. The actionable takeaway. What the
reader should DO or WATCH today. Nothing vague — be direct.]

---
The Daily Rug — Premium Crypto Intelligence
📊 feed.rugmunch.io  |  🤖 @RugMunchBot  |  🐦 @CryptoRugMunch"""


async def generate_daily_intel(publish: bool = False, **kw) -> dict[str, Any] | None:
    """Generate The Daily Rug with quality review.

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

    writing_prompt = f"""Write today's The Daily Rug — the premium crypto briefing

Today: {date_str}

Research notes:
{research_notes}

Raw context:
{context[:2500]}

FORMAT:
# THE DAILY RUG
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
        final_report = f"""# THE DAILY RUG
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
        "source": "the_daily_rug",
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

    # ── Telegram ──
    tg_result = await _publish_to_telegram(report, date_str)
    results["telegram"] = tg_result

    # ── Farcaster via Neynar API ──
    fc_result = await _publish_to_farcaster(report, date_str)
    results["farcaster"] = fc_result

    return results


async def _publish_to_farcaster(report: str, date_str: str) -> dict[str, Any]:
    """Post The Daily Rug to Farcaster via Neynar API."""
    api_key = os.getenv("NEYNAR_API_KEY", "")
    signer_uuid = os.getenv("FARCASTER_SIGNER_UUID", "")
    if not api_key or not signer_uuid:
        return {"status": "skipped", "reason": "Farcaster not configured"}

    # Extract a concise cast from the report
    lines = report.split("\n")
    cast_lines = []
    cast_lines.append(f"📊 The Daily Rug — {date_str}\n")

    for line in lines:
        s = line.strip()
        if s.startswith("### MARKET SNAPSHOT"):
            # Grab the next 2 non-empty, non-header lines
            idx = lines.index(line)
            grabbed = 0
            for ln in lines[idx + 1 :]:
                ls = ln.strip()
                if ls and not ls.startswith("#"):
                    cast_lines.append(ls[:240])
                    grabbed += 1
                    if grabbed >= 3:
                        break
        if s.startswith("### TOP STORIES"):
            idx = lines.index(line)
            for ln in lines[idx + 1 : idx + 6]:
                ls = ln.strip()
                if ls and ls.startswith(("- ", "• ")):
                    cast_lines.append(ls[:200])

        if s.startswith("### BOTTOM LINE"):
            idx = lines.index(line)
            for ln in lines[idx + 1 : idx + 3]:
                ls = ln.strip()
                if ls and not ls.startswith("#") and not ls.startswith("---"):
                    cast_lines.append(f"\n{ls[:240]}")

    cast_text = "\n".join(cast_lines)[:1024]  # Farcaster 1024 char limit
    cast_text += "\n\n🔗 feed.rugmunch.io"

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.neynar.com/v2/farcaster/cast",
                headers={
                    "api_key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "signer_uuid": signer_uuid,
                    "text": cast_text,
                },
            )
            if r.status_code in (200, 201):
                return {"status": "posted", "platform": "farcaster", "hash": r.json().get("hash", "")}
            else:
                return {"status": "failed", "platform": "farcaster", "error": r.text[:200]}
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return {"status": "error", "platform": "farcaster", "error": str(e)[:200]}


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
        headline = f"The Daily Rug - {date_str}"

    tweet_text = f"📊 The Daily Rug — {date_str}\n\n{headline}\n\n{tldr}\n\nFull issue: https://feed.rugmunch.io"

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
        title = f"The Daily Rug - {date_str}"

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
                            "tags": ["the-daily-rug", "market-intel", "crypto-briefing"],
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
            tg_parts.append(f"<i>{stripped.removeprefix('## ').strip()}</i>\n")
            continue

        # Section headers
        if stripped.startswith("### "):
            if in_bullets:
                tg_parts.append("")
                in_bullets = False
            current_section = stripped.removeprefix("### ").strip()
            tg_parts.append(f"\n<b>{current_section}</b>\n")
            continue

        # Bullet points
        if stripped.startswith(("- ", "• ")):
            in_bullets = True
            bullet_text = stripped.removeprefix("- ").removeprefix("• ").strip()
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
            if section.startswith("THE DAILY RUG"):
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
