"""
RMI Social Feed - X/Twitter + Reddit crypto news with aggressive caching.

Top 50 crypto X accounts monitored for breaking news.
Falls back to Nitter when rate limited.
Reddit top posts from r/CryptoCurrency, r/ethfinance, r/solana.
"""

import asyncio
import hashlib
from app.core.logging import get_logger
import time
from datetime import UTC, datetime

import httpx

logger = get_logger("social_feed")

# ═══════════════════════════════════════════════════════════════════════════
# TOP 50 CRYPTO X ACCOUNTS - by influence/relevance
# ═══════════════════════════════════════════════════════════════════════════

TOP_CRYPTO_ACCOUNTS = [
    {"handle": "cz_binance", "name": "CZ Binance", "followers": "9.5M", "pfp": "cz_binance"},
    {"handle": "VitalikButerin", "name": "Vitalik Buterin", "followers": "5.5M", "pfp": "vitalik"},
    {"handle": "SBF_FTX", "name": "SBF", "followers": "1.1M", "pfp": "sbf"},
    {"handle": "aantonop", "name": "Andreas Antonopoulos", "followers": "750K", "pfp": "aantonop"},
    {"handle": "balajis", "name": "Balaji Srinivasan", "followers": "1M", "pfp": "balajis"},
    {"handle": "cdixon", "name": "Chris Dixon", "followers": "950K", "pfp": "cdixon"},
    {"handle": "brian_armstrong", "name": "Brian Armstrong", "followers": "1.3M", "pfp": "brian"},
    {"handle": "aeyakovenko", "name": "Anatoly Yakovenko", "followers": "400K", "pfp": "toly"},
    {"handle": "saylor", "name": "Michael Saylor", "followers": "3.6M", "pfp": "saylor"},
    {"handle": "mcuban", "name": "Mark Cuban", "followers": "8.8M", "pfp": "cuban"},
    {"handle": "david_sacks", "name": "David Sacks", "followers": "900K", "pfp": "sacks"},
    {"handle": "twobitidiot", "name": "Ryan Selkis", "followers": "400K", "pfp": "selkis"},
    {"handle": "RamaswamyNik", "name": "Vivek Ramaswamy", "followers": "3.5M", "pfp": "vivek"},
    {"handle": "elonmusk", "name": "Elon Musk", "followers": "210M", "pfp": "elon"},
    {"handle": "novogratz", "name": "Mike Novogratz", "followers": "540K", "pfp": "novo"},
    {"handle": "CryptoHayes", "name": "Arthur Hayes", "followers": "600K", "pfp": "hayes"},
    {"handle": "zhusu", "name": "Zhu Su", "followers": "650K", "pfp": "zhu"},
    {"handle": "cobie", "name": "Cobie", "followers": "800K", "pfp": "cobie"},
    {"handle": "loomdart", "name": "Loomdart", "followers": "350K", "pfp": "loom"},
    {"handle": "0xngmi", "name": "0xngmi", "followers": "250K", "pfp": "ngmi"},
    {"handle": "gaut", "name": "Gaut", "followers": "200K", "pfp": "gaut"},
    {"handle": "ThinkingUSD", "name": "ThinkingUSD", "followers": "180K", "pfp": "thinking"},
    {"handle": "blknoiz06", "name": "Blknoiz06", "followers": "300K", "pfp": "blknoiz"},
    {"handle": "Dynamo_Patrick", "name": "Patrick Dynamo", "followers": "350K", "pfp": "dynamo"},
    {"handle": "TheCryptoLark", "name": "Lark Davis", "followers": "1.2M", "pfp": "lark"},
    {"handle": "APompliano", "name": "Anthony Pompliano", "followers": "1.7M", "pfp": "pomp"},
    {"handle": "RaoulGMI", "name": "Raoul Pal", "followers": "1.1M", "pfp": "raoul"},
    {"handle": "Melt_Dem", "name": "Meltem Demirors", "followers": "300K", "pfp": "meltem"},
    {"handle": "laurashin", "name": "Laura Shin", "followers": "350K", "pfp": "laura"},
    {"handle": "nic__carter", "name": "Nic Carter", "followers": "400K", "pfp": "nic"},
    {"handle": "ercwl", "name": "Eric Wall", "followers": "200K", "pfp": "ercwl"},
    {"handle": "udiWertheimer", "name": "Udi Wertheimer", "followers": "250K", "pfp": "udi"},
    {"handle": "PeterLBrandt", "name": "Peter Brandt", "followers": "700K", "pfp": "brandt"},
    {"handle": "100trillionUSD", "name": "PlanB", "followers": "1.9M", "pfp": "planb"},
    {"handle": "woonomic", "name": "Willy Woo", "followers": "1.1M", "pfp": "woo"},
    {"handle": "CryptoCapo_", "name": "Crypto Capo", "followers": "900K", "pfp": "capo"},
    {"handle": "CryptoCred", "name": "CryptoCred", "followers": "600K", "pfp": "cred"},
    {"handle": "Pentosh1", "name": "Pentoshi", "followers": "850K", "pfp": "pentoshi"},
    {"handle": "intocryptoverse", "name": "Benjamin Cowen", "followers": "900K", "pfp": "cowen"},
    {"handle": "CryptoKaleo", "name": "Kaleo", "followers": "650K", "pfp": "kaleo"},
    {"handle": "rektcapital", "name": "Rekt Capital", "followers": "500K", "pfp": "rektcap"},
    {"handle": "CryptoMichNL", "name": "Micha�l van de Poppe", "followers": "750K", "pfp": "mich"},
    {"handle": "CryptoJack", "name": "CryptoJack", "followers": "400K", "pfp": "cryptojack"},
    {"handle": "AltcoinGordon", "name": "Altcoin Gordon", "followers": "550K", "pfp": "gordon"},
    {"handle": "Ashcryptoreal", "name": "Ash Crypto", "followers": "1.1M", "pfp": "ash"},
    {"handle": "Trader_XO", "name": "TraderXO", "followers": "380K", "pfp": "xo"},
    {"handle": "CryptoWizardd", "name": "CryptoWizard", "followers": "350K", "pfp": "wizard"},
    {"handle": "CryptosBatman", "name": "Batman", "followers": "300K", "pfp": "batman"},
    {"handle": "ColdBloodShill", "name": "Cold Blooded Shiller", "followers": "280K", "pfp": "cbs"},
    {"handle": "MacroCRG", "name": "MacroCRG", "followers": "250K", "pfp": "crg"},
    {"handle": "WatcherGuru", "name": "Watcher.Guru", "followers": "2.1M", "pfp": "watcher"},
    {"handle": "unusual_whales", "name": "Unusual Whales", "followers": "1.5M", "pfp": "whales"},
    {"handle": "dbnewstweets", "name": "DB News", "followers": "750K", "pfp": "db"},
    {"handle": "ZeroHedge", "name": "ZeroHedge", "followers": "1.8M", "pfp": "zh"},
    {
        "handle": "SpectrumMarkets",
        "name": "Spectrum Markets",
        "followers": "120K",
        "pfp": "spectrum",
    },
    {"handle": "TreeNews5", "name": "Tree News", "followers": "500K", "pfp": "tree"},
    {
        "handle": "Crypto_Twitter",
        "name": "Crypto Twitter News",
        "followers": "450K",
        "pfp": "ctnews",
    },
    {"handle": "DegenerateNews", "name": "Degenerate News", "followers": "380K", "pfp": "degen"},
    {"handle": "NFT_GOD", "name": "NFT God", "followers": "280K", "pfp": "nftgod"},
    {"handle": "CryptoKoryo", "name": "Koryo", "followers": "180K", "pfp": "koryo"},
    {"handle": "OnchainData", "name": "Onchain Data Nerd", "followers": "120K", "pfp": "onchain"},
    {"handle": "spl_brah", "name": "SPL Brah", "followers": "90K", "pfp": "spl"},
    {"handle": "solana_daily", "name": "Solana Daily", "followers": "250K", "pfp": "soldaily"},
    {"handle": "SolanaLegend", "name": "Solana Legend", "followers": "180K", "pfp": "sollegend"},
    {"handle": "SolanaConf", "name": "Solana News", "followers": "150K", "pfp": "solconf"},
    {"handle": "0xMert_", "name": "Mert", "followers": "350K", "pfp": "mert"},
    {"handle": "0xTanishq", "name": "Tanishq", "followers": "130K", "pfp": "tanishq"},
    {"handle": "Deebs_DeFi", "name": "Deebs DeFi", "followers": "160K", "pfp": "deebs"},
    {"handle": "DeFi_Dad", "name": "DeFi Dad", "followers": "200K", "pfp": "defidad"},
    {"handle": "DeFi_Ignas", "name": "Ignas DeFi", "followers": "220K", "pfp": "ignas"},
    {"handle": "Crypto_GodJohn", "name": "Crypto God John", "followers": "280K", "pfp": "godjohn"},
    {"handle": "CryptoNTez", "name": "Crypto NTez", "followers": "90K", "pfp": "ntez"},
    {"handle": "0xSisyphus", "name": "Sisyphus", "followers": "110K", "pfp": "sisyphus"},
    {"handle": "vydamo_", "name": "Vydamo", "followers": "85K", "pfp": "vydamo"},
    {"handle": "0xKillWolf", "name": "KillWolf", "followers": "75K", "pfp": "killwolf"},
    {"handle": "MoonOverlord", "name": "Moon Overlord", "followers": "95K", "pfp": "moon"},
    {"handle": "CryptoAmb", "name": "Crypto Amber", "followers": "70K", "pfp": "amber"},
    {"handle": "satsdart", "name": "SatsDart", "followers": "65K", "pfp": "sats"},
    {"handle": "DeFi_Kamikaze", "name": "Kamikaze DeFi", "followers": "55K", "pfp": "kami"},
    {"handle": "Crypto_Ninja", "name": "Crypto Ninja", "followers": "80K", "pfp": "ninja"},
    {"handle": "0xWave_", "name": "0xWave", "followers": "60K", "pfp": "wave"},
    {"handle": "Crypto_Chase", "name": "Chase Crypto", "followers": "45K", "pfp": "chase"},
    {"handle": "AltCryptoGems", "name": "Altcoin Gems", "followers": "140K", "pfp": "gems"},
    {"handle": "DeFi_Warhol", "name": "DeFi Warhol", "followers": "50K", "pfp": "warhol"},
    {"handle": "Crypto_Link", "name": "Crypto Link", "followers": "70K", "pfp": "link"},
    {"handle": "SolanaAlpha_", "name": "Solana Alpha", "followers": "55K", "pfp": "solalpha"},
    {"handle": "DeFi_Prime", "name": "DeFi Prime", "followers": "40K", "pfp": "prime"},
    {"handle": "Crypto_Oracle", "name": "Crypto Oracle", "followers": "65K", "pfp": "oracle"},
    {"handle": "chain_news", "name": "Chain News", "followers": "85K", "pfp": "chain"},
    {"handle": "Crypto_Banter", "name": "Crypto Banter", "followers": "220K", "pfp": "banter"},
    {"handle": "LunarCRUSH", "name": "LunarCrush", "followers": "250K", "pfp": "lunar"},
    {"handle": "SantimentFeed", "name": "Santiment", "followers": "140K", "pfp": "santi"},
    {"handle": "CoinMarketCap", "name": "CoinMarketCap", "followers": "3.5M", "pfp": "cmc"},
    {"handle": "CoinGecko", "name": "CoinGecko", "followers": "1.8M", "pfp": "gecko"},
    {"handle": "MessariCrypto", "name": "Messari", "followers": "450K", "pfp": "messari"},
    {"handle": "ArkhamIntel", "name": "Arkham", "followers": "700K", "pfp": "arkham"},
    {"handle": "DuneAnalytics", "name": "Dune", "followers": "350K", "pfp": "dune"},
    {"handle": "Nansen_ai", "name": "Nansen", "followers": "280K", "pfp": "nansen"},
    {"handle": "Glassnode", "name": "Glassnode", "followers": "320K", "pfp": "glass"},
]

# Reddit crypto subreddits
REDDIT_SUBS = [
    "CryptoCurrency",
    "ethfinance",
    "solana",
    "bitcoin",
    "defi",
    "CryptoTrading",
    "CryptoMarkets",
    "ethereum",
    "CryptoTechnology",
]

# Cache TTLs
TTL_TWITTER = 300  # 5 min
TTL_REDDIT = 600  # 10 min
TTL_NITTER = 900  # 15 min (fallback, longer cache)

_cache: dict[str, tuple] = {}


def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry:
        expiry, data = entry
        if time.monotonic() < expiry:
            return data
        del _cache[key]
    return None


def _cache_set(key: str, data: dict, ttl: int):
    _cache[key] = (time.monotonic() + ttl, data)


async def get_twitter_feed(limit: int = 30) -> dict:
    """Get top crypto tweets via Nitter (free, no auth, cached)."""
    cache_key = f"twitter_feed_{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    tweets = []
    async with httpx.AsyncClient(timeout=15) as c:
        for account in TOP_CRYPTO_ACCOUNTS[:30]:  # Top 15 to minimize calls
            try:
                # Try Nitter (free, no auth)
                nitter_url = f"https://nitter.net/{account['handle']}/rss"
                r = await c.get(nitter_url)

                if r.status_code == 200:
                    import xml.etree.ElementTree as ET

                    root = ET.fromstring(r.text)
                    for item in root.findall(".//item")[:3]:
                        title = item.find("title").text if item.find("title") is not None else ""
                        link = item.find("link").text if item.find("link") is not None else ""
                        item.find("description").text if item.find("description") is not None else ""
                        pubdate = item.find("pubDate").text if item.find("pubDate") is not None else ""

                        if title and "RT @" not in title:
                            tweets.append(
                                {
                                    "id": hashlib.sha256(link.encode()).hexdigest()[:12],
                                    "text": title.replace(f"{account['name']}: ", ""),
                                    "author": account["name"],
                                    "handle": account["handle"],
                                    "followers": account["followers"],
                                    "pfp": f"https://unavatar.io/twitter/{account['handle']}",
                                    "url": link,
                                    "published": pubdate,
                                    "source": "x",
                                    "type": "tweet",
                                }
                            )
            except Exception:
                continue

    result = {
        "tweets": sorted(tweets, key=lambda t: t.get("published", ""), reverse=True)[:limit],
        "accounts_monitored": len(TOP_CRYPTO_ACCOUNTS),
        "new_accounts": "50 top news breakers + 49 underrated alpha accounts",
        "updated": datetime.now(UTC).isoformat(),
    }

    _cache_set(cache_key, result, TTL_NITTER if tweets else 60)
    return result


async def get_reddit_feed(limit: int = 20) -> dict:
    """Get top crypto Reddit posts (free, no auth, cached)."""
    cache_key = f"reddit_feed_{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    posts = []
    async with httpx.AsyncClient(timeout=15) as c:
        for sub in REDDIT_SUBS[:4]:  # Top 4 to minimize calls
            try:
                r = await c.get(
                    f"https://www.reddit.com/r/{sub}/hot.json?limit=5",
                    headers={"User-Agent": "RMI/1.0"},
                )
                if r.status_code == 200:
                    data = r.json()
                    for child in data.get("data", {}).get("children", []):
                        d = child["data"]
                        posts.append(
                            {
                                "id": d["id"],
                                "title": d["title"],
                                "author": d["author"],
                                "subreddit": d["subreddit"],
                                "score": d["score"],
                                "num_comments": d["num_comments"],
                                "url": f"https://reddit.com{d['permalink']}",
                                "published": datetime.fromtimestamp(d["created_utc"], UTC).isoformat(),
                                "source": "reddit",
                                "type": "post",
                            }
                        )
            except Exception:
                continue

    result = {
        "posts": sorted(posts, key=lambda p: p.get("score", 0), reverse=True)[:limit],
        "subreddits": REDDIT_SUBS,
        "updated": datetime.now(UTC).isoformat(),
    }

    _cache_set(cache_key, result, TTL_REDDIT)
    return result


async def get_social_feed(limit_twitter: int = 30, limit_reddit: int = 20) -> dict:
    """Get combined social feed - X + Reddit, sorted by recency."""
    twitter, reddit = await asyncio.gather(
        get_twitter_feed(limit_twitter),
        get_reddit_feed(limit_reddit),
        return_exceptions=True,
    )

    twitter_data = twitter if not isinstance(twitter, Exception) else {"tweets": []}
    reddit_data = reddit if not isinstance(reddit, Exception) else {"posts": []}

    return {
        "x": twitter_data,
        "reddit": reddit_data,
        "total_social_sources": len(TOP_CRYPTO_ACCOUNTS) + len(REDDIT_SUBS),
        "updated": datetime.now(UTC).isoformat(),
    }


def get_top_accounts() -> list:
    """Get the list of monitored X accounts with profile pics."""
    return [
        {
            "handle": a["handle"],
            "name": a["name"],
            "followers": a["followers"],
            "pfp": f"https://unavatar.io/twitter/{a['handle']}",
        }
        for a in TOP_CRYPTO_ACCOUNTS
    ]
