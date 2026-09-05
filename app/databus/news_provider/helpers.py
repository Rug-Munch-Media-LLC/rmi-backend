"""Shared constants and helpers for the news provider package."""

from __future__ import annotations

from app.databus.vault import resolve_key

# ── Free API endpoints (no keys needed) ────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
POLYMARKET_URL = "https://gamma-api.polymarket.com/events"
COINGECKO_KEY = resolve_key("COINGECKO_API_KEY")
CRYPTOPANIC_API_KEY = resolve_key("CRYPTOPANIC_API_KEY").strip()

NEWS_CACHE_KEY = "rmi:news:full_feed"
NEWS_CACHE_TTL = 300  # 5 minutes
MARKET_BRIEF_KEY = "rmi:news:market_brief"
MARKET_BRIEF_TTL = 60  # 1 minute


def hashlib_quick(text: str) -> str:
    """Quick hash for article deduplication."""
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ── Top RSS feeds (the "TOP_50_FEEDS" list; 75+ after 2026-08-16 expansion) ──
# Curated from the 170+ feed list — fast, reliable, high-signal sources.
# All entries added 2026-08-16 were live-probed (HTTP 200 + feedparser parse
# + fresh items within 30 days) before inclusion; a feed that later dies is
# skipped at fetch time by the fail-open per-feed handler (never blocks the
# batch), and the registry-level probe (scripts/test_news_sources.py +
# app/databus/news_source_health.py) tracks VERIFIED/STALE/DEAD for pruning.
TOP_50_FEEDS = [
    # ---- Tier 1: Major News Outlets (fastest, always up) ----
    ("https://cointelegraph.com/rss", "CoinTelegraph"),
    ("https://decrypt.co/feed", "Decrypt"),
    ("https://blockworks.co/feed", "Blockworks"),
    ("https://www.theblock.co/rss.xml", "The Block"),
    ("https://thedefiant.io/feed", "The Defiant"),
    ("https://www.bankless.com/feed", "Bankless"),
    ("https://bitcoinmagazine.com/feed", "Bitcoin Magazine"),
    ("https://protos.com/feed/", "Protos"),
    ("https://beincrypto.com/feed/", "BeInCrypto"),
    ("https://unchainedcrypto.com/feed/", "Unchained"),
    ("https://coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
    ("https://cryptoslate.com/feed/", "CryptoSlate"),
    ("https://cryptobriefing.com/feed/", "CryptoBriefing"),
    ("https://cryptopotato.com/feed/", "CryptoPotato"),
    ("https://dailyhodl.com/feed/", "Daily Hodl"),
    ("https://www.newsbtc.com/feed/", "NewsBTC"),
    ("https://bitcoinist.com/feed/", "Bitcoinist"),
    # ---- Tier 2: Security Firms ----
    ("https://peckshield.medium.com/feed", "PeckShield"),
    ("https://slowmist.medium.com/feed", "SlowMist"),
    ("https://certik.medium.com/feed", "CertiK"),
    ("https://immunefi.medium.com/feed", "Immunefi"),
    ("https://blog.trailofbits.com/feed/", "Trail of Bits"),
    ("https://www.chainalysis.com/blog/feed/", "Chainalysis"),
    ("https://blog.openzeppelin.com/feed/", "OpenZeppelin"),
    # ---- Tier 3: Research & Analytics ----
    ("https://insights.glassnode.com/feed/", "Glassnode"),
    ("https://dune.com/blog/feed", "Dune Analytics"),
    ("https://messari.io/feed", "Messari"),
    ("https://defillama.com/feed", "DeFi Llama"),
    # ---- Tier 4: Protocol Blogs ----
    ("https://blog.ethereum.org/feed.xml", "Ethereum Foundation"),
    ("https://solana.com/news/feed", "Solana"),
    ("https://blog.arbitrum.io/feed", "Arbitrum"),
    ("https://blog.optimism.io/feed", "Optimism"),
    ("https://polygon.technology/blog/feed", "Polygon"),
    ("https://blog.sui.io/feed", "Sui"),
    ("https://blog.cosmos.network/feed", "Cosmos"),
    # ---- Tier 5: DeFi Protocols ----
    ("https://uniswap.org/blog/feed.xml", "Uniswap"),
    ("https://aave.com/blog/rss.xml", "Aave"),
    ("https://blog.chain.link/feed", "Chainlink"),
    ("https://blog.makerdao.com/feed/", "MakerDAO"),
    ("https://blog.lido.fi/feed", "Lido"),
    # ---- Tier 6: Exchange Blogs ----
    ("https://blog.coinbase.com/feed", "Coinbase"),
    ("https://blog.kraken.com/feed/", "Kraken"),
    ("https://www.binance.com/en/feed/news", "Binance"),
    ("https://blog.bitfinex.com/feed/", "Bitfinex"),
    ("https://blog.ledger.com/feed/", "Ledger"),
    # ---- Tier 7: VC & Investment ----
    ("https://a16zcrypto.com/feed/", "a16z Crypto"),
    ("https://www.paradigm.xyz/feed", "Paradigm"),
    ("https://panteracapital.com/feed/", "Pantera Capital"),
    ("https://multicolin.capital/feed/", "Multicoin Capital"),
    ("https://variant.fund/feed/", "Variant Fund"),
    # ---- Tier 8: Market Data ----
    ("https://www.tradingview.com/blog/feed/", "TradingView"),
    ("https://blog.santiment.net/feed/", "Santiment"),
    ("https://blog.coinmarketcap.com/feed/", "CoinMarketCap"),
    ("https://www.coingecko.com/en/blog/feed", "CoinGecko Blog"),
    # ---- Tier 9: Security/Threat Intel ----
    ("https://www.bleepingcomputer.com/feed/", "BleepingComputer"),
    ("https://www.web3isgoinggreat.com/feed", "W3IGG"),
    ("https://krebsonsecurity.com/feed/", "Krebs on Security"),
    # ---- Tier 10: News outlets (expansion 2026-08-16, live-probed) ----
    ("https://www.coingape.com/feed/", "CoinGape"),
    ("https://zycrypto.com/feed/", "ZyCrypto"),
    ("https://ambcrypto.com/feed/", "AMBCrypto"),
    ("https://coinpedia.org/feed/", "Coinpedia"),
    ("https://crypto.news/feed/", "Crypto.news"),
    ("https://blockonomi.com/feed/", "Blockonomi"),
    ("https://cryptodaily.co.uk/feed/", "CryptoDaily"),
    ("https://news.bitcoin.com/feed/", "Bitcoin.com News"),
    ("https://watcher.guru/feed/", "Watcher Guru"),
    ("https://cryptonews.com/feed/", "Cryptonews.com"),
    ("https://www.trustnodes.com/feed", "Trustnodes"),
    ("https://www.cryptopolitan.com/feed/", "Cryptopolitan"),
    ("https://u.today/rss", "U.Today"),
    ("https://nulltx.com/feed/", "NullTX"),
    ("https://www.crypto-news-flash.com/feed/", "Crypto News Flash"),
    ("https://www.thecoinrepublic.com/feed/", "Coin Republic"),
    ("https://coinedition.com/feed/", "Coin Edition"),
    ("https://www.crypto-reporter.com/feed/", "Crypto Reporter"),
    ("https://www.cointribune.com/en/feed/", "CoinTribune"),
    ("https://themerkle.com/feed/", "The Merkle"),
    ("https://www.coinspeaker.com/feed/", "CoinSpeaker"),
    ("https://dailycoin.com/feed/", "DailyCoin"),
    ("https://www.bitcoinsistemi.com/en/feed/", "Bitcoin Sistemi (EN)"),
    # ---- Tier 11: Exchange & market blogs (expansion 2026-08-16) ----
    ("https://www.financemagnates.com/cryptocurrency/feed/", "Finance Magnates Crypto"),
    ("https://www.investing.com/rss/news_25.rss", "Investing.com Crypto"),
    ("https://www.bitget.com/news/feed", "Bitget"),
    # ---- Tier 12: Security firms (expansion 2026-08-16, live-probed) ----
    ("https://blog.verichains.io/feed", "Verichains"),
]
