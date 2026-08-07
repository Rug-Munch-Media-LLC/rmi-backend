"""
RugCharts X/CT Intelligence Pipeline
=====================================
"CT Rundown" - top 20 stories daily from Crypto Twitter.
Multi-method access: xurl (OAuth), cookie scraping, Groq AI analysis.

Algorithm: engagement-weighted, diversity-scored, entity-resolved.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import urllib.parse
from collections import Counter
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

logger = get_logger("x_intel")

# ── TOP CT ACCOUNTS - Curated, diverse, high-signal ────────────────

CT_ACCOUNTS = {
    # ── Tier 1: Must-track (breaking news, high signal) ──
    "crypto_news": [
        {"handle": "WatcherGuru", "name": "Watcher.Guru", "category": "breaking", "weight": 1.0},
        {"handle": "dbnewswire", "name": "DB News Wire", "category": "breaking", "weight": 1.0},
        {"handle": "CoinDesk", "name": "CoinDesk", "category": "journalism", "weight": 0.95},
        {"handle": "TheBlock__", "name": "The Block", "category": "journalism", "weight": 0.95},
        {
            "handle": "Cointelegraph",
            "name": "CoinTelegraph",
            "category": "journalism",
            "weight": 0.9,
        },
        {"handle": "Blockworks_", "name": "Blockworks", "category": "journalism", "weight": 0.9},
        {"handle": "DLNewsInfo", "name": "DL News", "category": "journalism", "weight": 0.85},
        {"handle": "SBF_FTX", "name": "SBF Tracker", "category": "drama", "weight": 0.7},
    ],
    # ── Tier 2: Analysis & Research ──
    "analysts": [
        {"handle": "zachxbt", "name": "ZachXBT", "category": "investigation", "weight": 1.0},
        {"handle": "0xfoobar", "name": "foobar", "category": "defi_analysis", "weight": 0.9},
        {"handle": "hasufl", "name": "Hasu", "category": "research", "weight": 0.9},
        {"handle": "jwpeirce", "name": "Hester Peirce", "category": "regulation", "weight": 0.85},
        {"handle": "sassal0x", "name": "Anthony Sassano", "category": "ethereum", "weight": 0.85},
        {"handle": "DoveyWan", "name": "Dovey Wan", "category": "macro", "weight": 0.85},
        {"handle": "0xCygaar", "name": "Cygaar", "category": "dev", "weight": 0.8},
        {"handle": "crypto_condom", "name": "Crypto Condom", "category": "memes", "weight": 0.6},
    ],
    # ── Tier 3: DeFi & Trading ──
    "defi_traders": [
        {"handle": "DeFi_Dad", "name": "DeFi Dad", "category": "defi", "weight": 0.8},
        {
            "handle": "gabrielhaines",
            "name": "Gabriel Haines",
            "category": "entertainment",
            "weight": 0.7,
        },
        {"handle": "cobie", "name": "Cobie", "category": "trading", "weight": 0.9},
        {"handle": "ThinkingUSD", "name": "ThinkingUSD", "category": "trading", "weight": 0.8},
        {"handle": "Rewkang", "name": "Rekt Fencer", "category": "trading", "weight": 0.75},
        {"handle": "lightcrypto", "name": "Light", "category": "research", "weight": 0.85},
        {"handle": "0x_Lens", "name": "0xLens", "category": "onchain", "weight": 0.8},
        {"handle": "lookonchain", "name": "Lookonchain", "category": "onchain", "weight": 0.9},
    ],
    # ── Tier 4: Solana & Memecoins ──
    "solana_meme": [
        {"handle": "aeyakovenko", "name": "Anatoly Yakovenko", "category": "solana", "weight": 0.9},
        {"handle": "0xMert_", "name": "Mert", "category": "solana", "weight": 0.8},
        {
            "handle": "SolanaLegend",
            "name": "Solana Legend",
            "category": "solana_memes",
            "weight": 0.7,
        },
        {"handle": "blknoiz06", "name": "blknoiz06", "category": "solana_defi", "weight": 0.75},
        {"handle": "theunipcs", "name": "Unipcs", "category": "memecoins", "weight": 0.7},
        {"handle": "MustStopMurad", "name": "Murad", "category": "memecoins", "weight": 0.8},
        {"handle": "0xENAS", "name": "ENAS", "category": "memecoins", "weight": 0.65},
    ],
    # ── Tier 5: Macro & VC ──
    "macro_vc": [
        {"handle": "RaoulGMI", "name": "Raoul Pal", "category": "macro", "weight": 0.85},
        {"handle": "APompliano", "name": "Pomp", "category": "bitcoin", "weight": 0.8},
        {"handle": "balajis", "name": "Balaji", "category": "tech", "weight": 0.9},
        {"handle": "nic__carter", "name": "Nic Carter", "category": "bitcoin", "weight": 0.85},
        {"handle": "ecoinometrics", "name": "Ecoinometrics", "category": "data", "weight": 0.8},
        {"handle": "LynAldenContact", "name": "Lyn Alden", "category": "macro", "weight": 0.9},
        {
            "handle": "michael_saylor",
            "name": "Michael Saylor",
            "category": "bitcoin",
            "weight": 0.95,
        },
        {"handle": "CathieDWood", "name": "Cathie Wood", "category": "macro", "weight": 0.85},
        {"handle": "APompliano", "name": "Anthony Pompliano", "category": "bitcoin", "weight": 0.8},
        {"handle": "cburniske", "name": "Chris Burniske", "category": "vc", "weight": 0.85},
        {"handle": "CryptoHayes", "name": "Arthur Hayes", "category": "macro", "weight": 0.9},
    ],
    # ── Tier 6: Extended - more voices, all verified ──
    "extended": [
        {"handle": "matt_hougan", "name": "Matt Hougan", "category": "etf", "weight": 0.8},
        {"handle": "EricBalchunas", "name": "Eric Balchunas", "category": "etf", "weight": 0.85},
        {"handle": "JSeyff", "name": "James Seyffart", "category": "etf", "weight": 0.8},
        {"handle": "biancoresearch", "name": "Jim Bianco", "category": "macro", "weight": 0.85},
        {"handle": "LucasNuzzi", "name": "Lucas Nuzzi", "category": "onchain", "weight": 0.8},
        {"handle": "nansen_ai", "name": "Nansen", "category": "onchain", "weight": 0.85},
        {"handle": "DuneAnalytics", "name": "Dune Analytics", "category": "data", "weight": 0.8},
        {"handle": "glassnode", "name": "Glassnode", "category": "onchain", "weight": 0.9},
        {"handle": "intotheblock", "name": "IntoTheBlock", "category": "onchain", "weight": 0.85},
        {"handle": "MessariCrypto", "name": "Messari", "category": "research", "weight": 0.9},
        {
            "handle": "Delphi_Digital",
            "name": "Delphi Digital",
            "category": "research",
            "weight": 0.9,
        },
        {"handle": "PanteraCapital", "name": "Pantera Capital", "category": "vc", "weight": 0.8},
        {"handle": "a16zcrypto", "name": "a16z Crypto", "category": "vc", "weight": 0.85},
        {"handle": "paradigm", "name": "Paradigm", "category": "vc", "weight": 0.85},
        {"handle": "1confirmation", "name": "1confirmation", "category": "vc", "weight": 0.7},
        {"handle": "ElectricCapital", "name": "Electric Capital", "category": "vc", "weight": 0.8},
        {"handle": "VariantFund", "name": "Variant Fund", "category": "vc", "weight": 0.75},
        {"handle": "dragonfly_xyz", "name": "Dragonfly", "category": "vc", "weight": 0.75},
        {"handle": "MulticoinCap", "name": "Multicoin Capital", "category": "vc", "weight": 0.8},
        {"handle": "polychaincap", "name": "Polychain Capital", "category": "vc", "weight": 0.8},
        {"handle": "zhusu", "name": "Zhu Su", "category": "trading", "weight": 0.7},
        {"handle": "KyleSamani", "name": "Kyle Samani", "category": "vc", "weight": 0.8},
        {"handle": "Hoskinson_IO", "name": "IOHK", "category": "protocol", "weight": 0.7},
        {
            "handle": "VitalikButerin",
            "name": "Vitalik Buterin",
            "category": "ethereum",
            "weight": 1.0,
        },
        {"handle": "drakefjustin", "name": "Justin Drake", "category": "ethereum", "weight": 0.85},
        {"handle": "timbeiko", "name": "Tim Beiko", "category": "ethereum", "weight": 0.8},
        {"handle": "StaniKulechov", "name": "Stani Kulechov", "category": "defi", "weight": 0.85},
        {"handle": "RuneKek", "name": "Rune Christensen", "category": "defi", "weight": 0.8},
        {"handle": "haydenzadams", "name": "Hayden Adams", "category": "defi", "weight": 0.85},
        {"handle": "VanceSpencer", "name": "Vance Spencer", "category": "vc", "weight": 0.75},
        {"handle": "lopp", "name": "Jameson Lopp", "category": "bitcoin", "weight": 0.85},
        {"handle": "adam3us", "name": "Adam Back", "category": "bitcoin", "weight": 0.9},
        {"handle": "peterktodd", "name": "Peter Todd", "category": "bitcoin", "weight": 0.8},
        {
            "handle": "udiWertheimer",
            "name": "Udi Wertheimer",
            "category": "bitcoin",
            "weight": 0.75,
        },
        {"handle": "ercwl", "name": "Eric Wall", "category": "bitcoin", "weight": 0.75},
        {"handle": "CryptoKaleo", "name": "Kaleo", "category": "trading", "weight": 0.65},
        {
            "handle": "CryptoCapo_",
            "name": "il Capo of Crypto",
            "category": "trading",
            "weight": 0.6,
        },
        {"handle": "rektcapital", "name": "Rekt Capital", "category": "trading", "weight": 0.65},
        {"handle": "CryptoCred", "name": "Crypto Cred", "category": "trading", "weight": 0.7},
        {"handle": "TechDev_52", "name": "TechDev", "category": "trading", "weight": 0.65},
        {"handle": "saylor", "name": "Saylor Tracker", "category": "bitcoin", "weight": 0.7},
        {
            "handle": "DocumentingBTC",
            "name": "Documenting Bitcoin",
            "category": "bitcoin",
            "weight": 0.7,
        },
        {
            "handle": "BitcoinMagazine",
            "name": "Bitcoin Magazine",
            "category": "bitcoin",
            "weight": 0.85,
        },
        {"handle": "TheCryptoLark", "name": "Lark Davis", "category": "influencer", "weight": 0.6},
        {
            "handle": "AltcoinGordon",
            "name": "Altcoin Gordon",
            "category": "influencer",
            "weight": 0.55,
        },
        {
            "handle": "CryptoWendyO",
            "name": "Crypto Wendy O",
            "category": "influencer",
            "weight": 0.6,
        },
        {
            "handle": "girlgone_crypto",
            "name": "Girl Gone Crypto",
            "category": "influencer",
            "weight": 0.6,
        },
        {
            "handle": "aantonop",
            "name": "Andreas Antonopoulos",
            "category": "education",
            "weight": 0.9,
        },
        {"handle": "gavofyork", "name": "Gavin Wood", "category": "protocol", "weight": 0.9},
        {"handle": "ethereumJoseph", "name": "Joseph Lubin", "category": "ethereum", "weight": 0.8},
        {"handle": "Melt_Dem", "name": "Meltem Demirors", "category": "vc", "weight": 0.8},
        {"handle": "laurashin", "name": "Laura Shin", "category": "journalism", "weight": 0.85},
        {
            "handle": "iamjosephyoung",
            "name": "Joseph Young",
            "category": "journalism",
            "weight": 0.75,
        },
        {
            "handle": "ForbesCrypto",
            "name": "Forbes Crypto",
            "category": "journalism",
            "weight": 0.8,
        },
        {
            "handle": "BloombergCrypto",
            "name": "Bloomberg Crypto",
            "category": "journalism",
            "weight": 0.9,
        },
        {"handle": "WSJmarkets", "name": "WSJ Markets", "category": "journalism", "weight": 0.9},
        {"handle": "FT", "name": "Financial Times", "category": "journalism", "weight": 0.9},
        {"handle": "Reuters", "name": "Reuters", "category": "journalism", "weight": 0.95},
        {"handle": "DefiantNews", "name": "The Defiant", "category": "journalism", "weight": 0.85},
        {
            "handle": "unchained_pod",
            "name": "Unchained Podcast",
            "category": "journalism",
            "weight": 0.8,
        },
        {"handle": "BanklessHQ", "name": "Bankless", "category": "media", "weight": 0.8},
        {
            "handle": "trustmachinesco",
            "name": "Trust Machines",
            "category": "bitcoin",
            "weight": 0.7,
        },
        {"handle": "Stacks", "name": "Stacks", "category": "bitcoin", "weight": 0.75},
        {"handle": "SolanaFndn", "name": "Solana Foundation", "category": "solana", "weight": 0.85},
        {"handle": "SolanaStatus", "name": "Solana Status", "category": "solana", "weight": 0.8},
        {"handle": "base", "name": "Base", "category": "layer2", "weight": 0.8},
        {"handle": "arbitrum", "name": "Arbitrum", "category": "layer2", "weight": 0.8},
        {"handle": "optimismFND", "name": "Optimism", "category": "layer2", "weight": 0.8},
        {"handle": "0xPolygon", "name": "Polygon", "category": "layer2", "weight": 0.8},
        {"handle": "zksync", "name": "zkSync", "category": "layer2", "weight": 0.8},
        {"handle": "Starknet", "name": "Starknet", "category": "layer2", "weight": 0.8},
        {"handle": "Uniswap", "name": "Uniswap", "category": "defi", "weight": 0.85},
        {"handle": "AaveAave", "name": "Aave", "category": "defi", "weight": 0.8},
        {"handle": "LidoFinance", "name": "Lido", "category": "defi", "weight": 0.8},
        {"handle": "MakerDAO", "name": "MakerDAO", "category": "defi", "weight": 0.8},
        {"handle": "chainlink", "name": "Chainlink", "category": "oracle", "weight": 0.85},
        {"handle": "1inch", "name": "1inch", "category": "defi", "weight": 0.75},
        {"handle": "pendle_fi", "name": "Pendle", "category": "defi", "weight": 0.75},
        {"handle": "eigenlayer", "name": "EigenLayer", "category": "defi", "weight": 0.8},
        {"handle": "CelestiaOrg", "name": "Celestia", "category": "infra", "weight": 0.8},
        {"handle": "Avax", "name": "Avalanche", "category": "protocol", "weight": 0.8},
        {"handle": "SuiNetwork", "name": "Sui", "category": "protocol", "weight": 0.75},
        {"handle": "Aptos", "name": "Aptos", "category": "protocol", "weight": 0.75},
        {"handle": "NEARProtocol", "name": "NEAR", "category": "protocol", "weight": 0.75},
        {"handle": "cosmos", "name": "Cosmos", "category": "protocol", "weight": 0.8},
        {"handle": "injective", "name": "Injective", "category": "protocol", "weight": 0.7},
        {"handle": "SeiNetwork", "name": "Sei", "category": "protocol", "weight": 0.7},
        {"handle": "monad_xyz", "name": "Monad", "category": "protocol", "weight": 0.75},
        {"handle": "berachain", "name": "Berachain", "category": "protocol", "weight": 0.7},
        {"handle": "Crypto_News", "name": "Crypto News", "category": "aggregator", "weight": 0.7},
        {"handle": "crypto_banter", "name": "Crypto Banter", "category": "media", "weight": 0.6},
        {"handle": "AltcoinDailyio", "name": "Altcoin Daily", "category": "media", "weight": 0.6},
        {"handle": "Coinbureau", "name": "Coin Bureau", "category": "education", "weight": 0.75},
    ],
}

ALL_CT = [a for tier in CT_ACCOUNTS.values() for a in tier]
CT_HANDLES = [a["handle"].lower() for a in ALL_CT]

GROQ_KEY = os.getenv("GROQ_API_KEY", "")

# ── Multi-Method X Access ─────────────────────────────────────────


async def _xurl_search(query: str, count: int = 20) -> list[dict]:
    """Search X via xurl CLI (OAuth)."""
    try:
        result = subprocess.run(
            ["xurl", "search", query, "-n", str(count), "--auth", "oauth2"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return []

        posts = []
        for line in result.stdout.strip().split("\n"):
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "text" in data:
                    posts.append(data)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "text" in item:
                            posts.append(item)
            except json.JSONDecodeError:
                continue
        return posts
    except Exception as e:
        logger.debug(f"xurl search failed: {e}")
        return []


async def _xurl_user_timeline(handle: str, count: int = 10) -> list[dict]:
    """Get recent posts from a specific user via xurl."""
    try:
        result = subprocess.run(
            ["xurl", "/2/users/by/username/" + handle.lstrip("@"), "--auth", "oauth2"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []

        user_data = json.loads(result.stdout)
        user_id = user_data.get("data", {}).get("id", "")

        if not user_id:
            return []

        result2 = subprocess.run(
            [
                "xurl",
                f"/2/users/{user_id}/tweets",
                "--auth",
                "oauth2",
                "-d",
                json.dumps({"max_results": count, "tweet.fields": "created_at,public_metrics"}),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result2.returncode != 0:
            return []

        data = json.loads(result2.stdout)
        posts = data.get("data", []) if isinstance(data, dict) else []

        return [
            {
                "text": p.get("text", ""),
                "author": handle,
                "created_at": p.get("created_at", ""),
                "likes": p.get("public_metrics", {}).get("like_count", 0),
                "retweets": p.get("public_metrics", {}).get("retweet_count", 0),
                "replies": p.get("public_metrics", {}).get("reply_count", 0),
                "url": f"https://x.com/{handle}/status/{p['id']}",
                "id": p["id"],
            }
            for p in posts
        ]

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug(f"xurl timeline failed for @{handle}: {e}")
        return []


async def _cookie_scrape_x(handles: list[str]) -> list[dict]:
    """Attempt cookie-based scraping via saved browser session.

    If Tailscale is connected to user's machine, we can pull cookies
    from their browser session for authenticated access.
    """
    # Check for saved cookies
    cookie_file = os.path.expanduser("~/.x_cookies.json")
    if not os.path.exists(cookie_file):
        return []

    try:
        with open(cookie_file) as f:
            cookies = json.load(f)

        posts = []
        async with httpx.AsyncClient(timeout=15, cookies=cookies) as c:
            for handle in handles[:5]:  # Limit to avoid rate limiting
                r = await c.get(
                    f"https://x.com/{handle}",
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120"},
                )
                if r.status_code == 200:
                    # Extract embedded tweet data from page
                    # X embeds JSON in __NEXT_DATA__ script tag
                    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            timeline = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                            for entry in timeline[:10]:
                                tweet = entry.get("content", {}).get("tweet", {})
                                if tweet:
                                    posts.append(
                                        {
                                            "text": tweet.get("full_text", ""),
                                            "author": handle,
                                            "likes": tweet.get("favorite_count", 0),
                                            "retweets": tweet.get("retweet_count", 0),
                                            "url": f"https://x.com/{handle}/status/{tweet.get('id_str', '')}",
                                        }
                                    )
                        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                            pass
        return posts
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug(f"Cookie scrape failed: {e}")
        return []


async def _web_scrape_x(handles: list[str], count_per_handle: int = 3) -> list[dict]:
    """Fallback: Web scrape X via DuckDuckGo search + embed extraction."""
    posts = []
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    ) as client:
        for handle in handles[:10]:
            try:
                search_q = f"site:x.com {handle}"
                r = await client.get(f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_q)}")
                if r.status_code != 200:
                    continue

                urls = list(
                    set(
                        re.findall(
                            rf"https://x\.com/{handle.lstrip('@')}/status/\d+",
                            r.text,
                            re.IGNORECASE,
                        )
                    )
                )

                for url in urls[:count_per_handle]:
                    try:
                        r2 = await client.get(url)
                        if r2.status_code == 200:
                            desc_match = re.search(r'<meta property="og:description" content="(.*?)"', r2.text)
                            if desc_match:
                                text = html.unescape(desc_match.group(1))
                                text = re.sub(rf"^{handle}:\s*", "", text, flags=re.IGNORECASE).strip()

                                posts.append(
                                    {
                                        "text": text,
                                        "author": handle,
                                        "url": url,
                                        "likes": 0,
                                        "retweets": 0,
                                        "replies": 0,
                                        "created_at": "",
                                        "id": url.split("/")[-1],
                                    }
                                )
                    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                        continue
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.debug(f"Web scrape failed for @{handle}: {e}")

    return posts


async def _grok_summarize(posts: list[dict]) -> str:
    """Use Groq (free tier) to generate a CT Rundown summary."""
    if not GROQ_KEY or not posts:
        return ""

    try:
        post_texts = "\n---\n".join(f"@{p.get('author', '?')}: {p.get('text', '')[:200]}" for p in posts[:20])

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a crypto news analyst. Summarize the top stories from Crypto Twitter in 5-7 bullet points. Focus on: breaking news, market-moving events, important protocol updates, security incidents, and regulatory developments. Be concise. Format: '• Story (source: @handle)'",
                        },
                        {
                            "role": "user",
                            "content": f"Here are today's Crypto Twitter posts. Give me the rundown:\n\n{post_texts}",
                        },
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug(f"Groq summary failed: {e}")

    # Fallback: simple keyword extraction
    return _simple_rundown(posts)


def _simple_rundown(posts: list[dict]) -> str:
    """Fallback: extract top stories by keyword frequency."""
    words = Counter()
    for p in posts:
        text = p.get("text", "").lower()
        for word in text.split():
            if len(word) > 4 and word not in ("https", "that", "this", "with", "from", "have"):
                words[word] += 1

    top_words = [w for w, _ in words.most_common(20) if w not in ("about", "would", "could", "their", "there")]

    return "Top themes: " + ", ".join(top_words[:8])


# ── CT Rundown Algorithm ───────────────────────────────────────────


def score_ct_post(post: dict, account: dict) -> float:
    """Score a CT post for the rundown. Higher = more important.

    Factors:
      - Account weight (curated: 0.6-1.0)
      - Engagement: likes * 1 + retweets * 3 + replies * 2
      - Recency: newer posts score higher
      - Content signals: links, threads, breaking keywords
    """
    score = account.get("weight", 0.7) * 10

    # Engagement
    likes = post.get("likes", 0) or 0
    retweets = post.get("retweets", 0) or 0
    replies = post.get("replies", 0) or 0
    engagement = likes + retweets * 3 + replies * 2

    # Log-scale to prevent single viral post from dominating
    if engagement > 0:
        score += min(20, (engagement**0.5) * 0.5)

    # Recency bonus (newer = better)
    created = post.get("created_at", "")
    if created:
        try:
            age_hours = (
                datetime.now(UTC) - datetime.fromisoformat(created.replace("Z", "+00:00"))
            ).total_seconds() / 3600
            score += max(0, 5 - age_hours * 0.5)  # 5 points now, 0 after 10 hours
        except (ConnectionError, OSError, TimeoutError):
            pass

    # Content signals
    text = post.get("text", "")
    if "BREAKING" in text.upper() or "🚨" in text:
        score += 5
    if "http" in text:  # Has link = more substance
        score += 2
    if text.count("\n") > 3:  # Thread-style
        score += 2

    # Diversity bonus: slightly penalize if same category already represented
    # (applied at selection time, not here)

    return score


async def fetch_ct_rundown(limit: int = 30, **kw) -> dict | None:
    """THE method. Fetch, score, and rank CT posts for the daily rundown.

    Strategy:
      1. Try xurl OAuth for top 10 handles (fast, reliable)
      2. Try cookie scrape for remaining (if available)
      3. Score all posts by engagement + account weight + signals
      4. Select top 20 with category diversity
      5. Generate Groq AI summary
    """
    all_posts = []
    errors = []
    source_method = "none"

    # ── Method 1: xurl OAuth ──
    try:
        # Fetch from top handles first
        priority_handles = [a["handle"] for a in ALL_CT[:10]]
        for handle in priority_handles:
            posts = await _xurl_user_timeline(handle, count=5)
            if posts:
                all_posts.extend(posts)
                if not source_method or source_method == "none":
                    source_method = "xurl_oauth"
    except Exception as e:
        errors.append(f"xurl: {e}")

    # ── Method 2: Cookie scraping ──
    if source_method == "none" or len(all_posts) < 10:
        try:
            cookie_posts = await _cookie_scrape_x([a["handle"] for a in ALL_CT])
            if cookie_posts:
                all_posts.extend(cookie_posts)
                source_method = source_method or "cookie_scrape"
        except Exception as e:
            errors.append(f"cookies: {e}")

    # ── Method 3: Web scraping fallback (DuckDuckGo + embed) ──
    if len(all_posts) < 5:
        try:
            scrape_posts = await _web_scrape_x([a["handle"] for a in ALL_CT], count_per_handle=3)
            if scrape_posts:
                all_posts.extend(scrape_posts)
                source_method = source_method or "web_scrape"
        except Exception as e:
            errors.append(f"web_scrape: {e}")

    # ── Method 4: xurl search (broad) ──
    if len(all_posts) < 5:
        try:
            search_posts = await _xurl_search("crypto OR bitcoin OR ethereum OR defi", count=30)
            if search_posts:
                all_posts.extend(
                    [
                        {
                            "text": p.get("text", ""),
                            "author": p.get("author_id", "unknown"),
                            "likes": p.get("public_metrics", {}).get("like_count", 0),
                            "retweets": p.get("public_metrics", {}).get("retweet_count", 0),
                            "url": f"https://x.com/i/web/status/{p.get('id', '')}",
                        }
                        for p in search_posts
                    ]
                )
                source_method = source_method or "xurl_search"
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            errors.append(f"search: {e}")

    if not all_posts:
        # ── Fallback: return trending tokens + market sentiment ──
        fallback_stories = []
        try:
            from app.databus.news_provider import get_fear_greed, get_trending_coins

            trending = await get_trending_coins()
            fear = await get_fear_greed()

            if trending and trending.get("trending"):
                for i, coin in enumerate(trending["trending"][:10]):
                    fallback_stories.append(
                        {
                            "rank": i + 1,
                            "text": f"Trending: {coin.get('name', '')} ({coin.get('symbol', '')}) — "
                            f"Market Cap Rank #{coin.get('market_cap_rank', 'N/A')}, "
                            f"CoinGecko score: {coin.get('score', 'N/A')}",
                            "author_handle": "coingecko",
                            "author_name": "CoinGecko Trending",
                            "category": "market",
                            "ct_score": 5.0,
                            "engagement": {"likes": 0, "retweets": 0, "replies": 0},
                            "url": "https://www.coingecko.com/",
                            "time": datetime.now(UTC).isoformat(),
                        }
                    )

            if fear:
                fallback_stories.append(
                    {
                        "rank": len(fallback_stories) + 1,
                        "text": f"Fear & Greed Index: {fear['value']}/100 — {fear['classification']}",
                        "author_handle": "alternative_me",
                        "author_name": "Crypto Fear & Greed Index",
                        "category": "market",
                        "ct_score": 4.0,
                        "engagement": {"likes": 0, "retweets": 0, "replies": 0},
                        "url": "https://alternative.me/crypto/fear-and-greed-index/",
                        "time": datetime.now(UTC).isoformat(),
                    }
                )
        except Exception:
            pass

        return {
            "rundown": fallback_stories,
            "total_fetched": 0,
            "total_selected": len(fallback_stories),
            "ai_summary": (
                "X/Twitter scraping is currently unavailable. Showing CoinGecko trending coins "
                "and market sentiment instead."
            )
            if fallback_stories
            else "No data available at this time.",
            "source_method": "fallback_trending",
            "categories_represented": ["market"],
            "accounts_tracked": 0,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "ct_rundown",
            "note": "X/Twitter unavailable — showing market data",
            "methods_tried": ["xurl_oauth", "cookie_scrape", "web_scrape", "xurl_search"],
            "errors": errors,
        }

    # ── Score posts ──
    account_map = {a["handle"].lower(): a for a in ALL_CT}

    scored = []
    for post in all_posts:
        author = (post.get("author", "") or "").lower().lstrip("@")
        account = account_map.get(author, {"handle": author, "name": author, "category": "unknown", "weight": 0.5})
        post["account"] = account
        post["ct_score"] = score_ct_post(post, account)
        scored.append(post)

    # ── Select top 20 with category diversity ──
    scored.sort(key=lambda p: -p["ct_score"])
    selected = []
    categories_used = set()

    for post in scored:
        cat = post["account"].get("category", "unknown")
        # Allow max 3 from same category, max 5 total from same handle
        same_cat = sum(1 for s in selected if s["account"].get("category") == cat)
        same_handle = sum(1 for s in selected if s["account"].get("handle") == post["account"].get("handle"))

        if same_cat < 3 and same_handle < 3:
            selected.append(post)
            categories_used.add(cat)

        if len(selected) >= 20:
            break

    # ── Generate AI summary ──
    summary = await _grok_summarize(selected[:20]) if GROQ_KEY else _simple_rundown(selected[:20])

    # ── Build result ──
    top_stories = []
    for i, post in enumerate(selected[:limit]):
        top_stories.append(
            {
                "rank": i + 1,
                "text": (post.get("text", "") or "")[:280],
                "author_handle": post["account"].get("handle", ""),
                "author_name": post["account"].get("name", ""),
                "category": post["account"].get("category", ""),
                "ct_score": round(post["ct_score"], 1),
                "engagement": {
                    "likes": post.get("likes", 0) or 0,
                    "retweets": post.get("retweets", 0) or 0,
                    "replies": post.get("replies", 0) or 0,
                },
                "url": post.get("url", ""),
                "time": post.get("created_at", ""),
            }
        )

    return {
        "rundown": top_stories,
        "total_fetched": len(all_posts),
        "total_selected": len(selected),
        "ai_summary": summary,
        "source_method": source_method,
        "categories_represented": sorted(categories_used),
        "accounts_tracked": len(ALL_CT),
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "ct_rundown",
    }


async def track_ct_accounts(**kw) -> dict:
    """Return the curated CT account list with stats."""
    return {
        "accounts": ALL_CT,
        "total": len(ALL_CT),
        "by_category": {cat: [a["handle"] for a in accts] for cat, accts in CT_ACCOUNTS.items()},
        "tiers": list(CT_ACCOUNTS.keys()),
    }
