"""
from __future__ import annotations

News Service — Static Config & Data Sources
==============================================
RSS feeds (170+), Reddit subreddits, sentiment keywords, category keywords.
"""
import os
import random

# ─── PROXY ROTATION ───────────────────────────────────────────────
PROXY_LIST = [
    "http://43.153.99.140:13001", "http://43.153.99.140:13002",
    "http://43.153.99.140:13003", "http://43.153.99.140:13004",
    "http://43.153.99.140:13005", "http://43.153.99.140:13006",
    "http://43.153.99.140:13007", "http://43.153.99.140:13008",
    "http://43.153.99.140:13009", "http://43.153.99.140:13010",
]

PROXY_REQUIRED_FEEDS = {
    "https://cryptosecurity.substack.com/feed",
    "https://weekinethereum.substack.com/feed",
    "https://banklessdao.substack.com/feed",
    "https://defieducation.substack.com/feed",
    "https://rekt.substack.com/feed",
    "https://cryptohayes.substack.com/feed",
    "https://doseofdefi.substack.com/feed",
    "https://thedefiant.substack.com/feed",
    "https://tokeninsight.substack.com/feed",
    "https://blockanalytica.substack.com/feed",
}


def _get_proxy() -> str | None:
    return random.choice(PROXY_LIST) if PROXY_LIST else None


# ─── CONFIG ───────────────────────────────────────────────────────
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "").strip()
GHOST_URL = os.getenv("GHOST_URL", "http://172.19.0.3:2368")
GHOST_CONTENT_KEY = os.getenv("GHOST_CONTENT_API_KEY", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
REDDIT_USER_AGENT = "RMI-News-Aggregator/3.0"


# ─── RSS FEEDS ────────────────────────────────────────────────────
RSS_FEEDS = [
    # Tier 1: Security & Audit Firms
    ("https://peckshield.medium.com/feed", "PeckShield"),
    ("https://slowmist.medium.com/feed", "SlowMist"),
    ("https://certik.medium.com/feed", "CertiK"),
    ("https://immunefi.medium.com/feed", "Immunefi"),
    ("https://blog.trailofbits.com/feed/", "Trail of Bits"),
    ("https://www.chainalysis.com/blog/feed/", "Chainalysis"),
    ("https://blog.openzeppelin.com/feed/", "OpenZeppelin"),
    ("https://consensys.io/diligence/blog/feed/", "Consensys Diligence"),
    ("https://halborn.com/feed/", "Halborn"),
    ("https://quantstamp.com/blog/feed/", "Quantstamp"),
    ("https://blog.solidified.io/feed/", "Solidified"),
    ("https://osec.io/blog/feed", "OSEC"),
    ("https://blog.hexens.io/feed", "Hexens"),
    ("https://dedaub.com/blog/feed", "Dedaub"),
    # Tier 2: Major News Outlets
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
    ("https://cryptodaily.co.uk/feed", "CryptoDaily"),
    ("https://u.today/rss", "U.Today"),
    ("https://cryptonews.com/news/feed/", "CryptoNews"),
    ("https://insidebitcoins.com/feed/", "InsideBitcoins"),
    ("https://nulltx.com/feed/", "NullTX"),
    ("https://coinpedia.org/feed/", "Coinpedia"),
    ("https://www.coinspeaker.com/feed/", "CoinSpeaker"),
    ("https://crypto.news/feed/", "Crypto.News"),
    ("https://cryptopolitan.com/feed/", "Cryptopolitan"),
    ("https://finbold.com/feed/", "Finbold"),
    ("https://coinjournal.net/feed/", "CoinJournal"),
    ("https://blockonomi.com/feed/", "Blockonomi"),
    ("https://dailycoin.com/feed/", "DailyCoin"),
    ("https://cryptoslate.com/feed/", "CryptoSlate"),
    ("https://coincodex.com/blog/feed/", "CoinCodex"),
    ("https://crypto.com/blog/feed/", "Crypto.com"),
    # Tier 3: Research & Analytics
    ("https://insights.glassnode.com/feed/", "Glassnode"),
    ("https://dune.com/blog/feed", "Dune Analytics"),
    ("https://messari.io/feed", "Messari"),
    ("https://www.nansen.ai/research/feed/", "Nansen"),
    ("https://blog.chainalysis.com/reports/feed/", "Chainalysis Reports"),
    ("https://kaiko.com/feed", "Kaiko"),
    ("https://www.thetie.is/feed", "The Tie"),
    ("https://coinmetrics.io/blog/feed/", "CoinMetrics"),
    ("https://blog.intotheblock.com/feed/", "IntoTheBlock"),
    ("https://defillama.com/feed", "DeFi Llama"),
    ("https://tokenterminal.com/blog/feed", "Token Terminal"),
    ("https://blog.bitquery.io/feed", "Bitquery"),
    # Tier 4: Protocol & Chain Blogs
    ("https://blog.ethereum.org/feed.xml", "Ethereum Foundation"),
    ("https://solana.com/news/feed", "Solana"),
    ("https://blog.arbitrum.io/feed", "Arbitrum"),
    ("https://blog.optimism.io/feed", "Optimism"),
    ("https://polygon.technology/blog/feed", "Polygon"),
    ("https://www.avax.network/blog/feed", "Avalanche"),
    ("https://near.org/blog/feed/", "NEAR Protocol"),
    ("https://blog.sui.io/feed", "Sui"),
    ("https://aptoslabs.medium.com/feed", "Aptos"),
    ("https://blog.cosmos.network/feed", "Cosmos"),
    ("https://polkadot.network/blog/feed/", "Polkadot"),
    ("https://blog.celestia.org/feed", "Celestia"),
    ("https://blog.eigenlayer.xyz/feed", "EigenLayer"),
    ("https://blog.base.org/feed", "Base"),
    ("https://starkware.medium.com/feed", "StarkWare"),
    # Tier 5: DeFi Protocols
    ("https://uniswap.org/blog/feed.xml", "Uniswap"),
    ("https://aave.com/blog/feed", "Aave"),
    ("https://blog.compound.finance/feed/", "Compound"),
    ("https://blog.makerdao.com/feed/", "MakerDAO"),
    ("https://blog.lido.fi/feed", "Lido"),
    ("https://blog.curve.fi/rss/", "Curve"),
    ("https://blog.balancer.fi/feed/", "Balancer"),
    ("https://blog.1inch.io/feed", "1inch"),
    ("https://blog.chain.link/feed", "Chainlink"),
    ("https://blog.synthetix.io/feed", "Synthetix"),
    ("https://blog.injective.com/feed", "Injective"),
    ("https://pancakeswap.medium.com/feed", "PancakeSwap"),
    ("https://blog.dydx.exchange/feed", "dYdX"),
    ("https://blog.gmx.io/feed", "GMX"),
    ("https://blog.pendle.finance/feed", "Pendle"),
    ("https://blog.morpho.org/feed", "Morpho"),
    ("https://blog.venus.io/feed", "Venus"),
    ("https://blog.yearn.fi/feed/", "Yearn"),
    # Tier 6: Exchange Blogs
    ("https://blog.trezor.io/feed", "Trezor"),
    ("https://blog.kraken.com/feed/", "Kraken"),
    ("https://blog.bitfinex.com/feed/", "Bitfinex"),
    ("https://blog.mexc.com/feed", "MEXC"),
    ("https://www.binance.com/en/feed/news", "Binance"),
    ("https://blog.coinbase.com/feed", "Coinbase"),
    ("https://www.okx.com/feed/blog", "OKX"),
    ("https://blog.bybit.com/feed/", "Bybit"),
    ("https://www.gemini.com/blog/feed", "Gemini"),
    ("https://blog.kucoin.com/feed/", "KuCoin"),
    ("https://www.gate.io/learn/feed", "Gate.io"),
    ("https://blog.ledger.com/feed/", "Ledger"),
    # Tier 7: VC & Investment
    ("https://a16zcrypto.com/feed/", "a16z Crypto"),
    ("https://www.paradigm.xyz/feed", "Paradigm"),
    ("https://panteracapital.com/feed/", "Pantera Capital"),
    ("https://multicoin.capital/feed/", "Multicoin Capital"),
    ("https://www.dragonfly.xyz/feed", "Dragonfly"),
    ("https://variant.fund/feed/", "Variant Fund"),
    ("https://1confirmation.com/feed", "1confirmation"),
    ("https://www.electriccapital.com/feed/", "Electric Capital"),
    ("https://www.coinbase.com/blog/feed", "Coinbase Ventures"),
    ("https://www.cmsholdings.co/feed/", "CMS Holdings"),
    ("https://blog.spartangroup.io/feed", "Spartan Group"),
    ("https://placeholder.vc/feed/", "Placeholder"),
    # Tier 8: Academic
    ("https://cyber.stanford.edu/blog/feed", "Stanford CBR"),
    ("https://dci.mit.edu/feed", "MIT DCI"),
    ("https://www.initc3.org/feed", "IC3 (Cornell)"),
    ("https://blockchain.berkeley.edu/feed/", "Berkeley Blockchain"),
    ("https://www.src.ac.uk/feed/", "SRC Research (UK)"),
    ("https://cbr.stanford.edu/feed/", "Stanford CBR"),
    # Tier 9: Government & Regulatory
    ("https://www.sec.gov/news/pressreleases.rss", "SEC"),
    ("https://www.cftc.gov/PressRoom/PressReleases/rss.xml", "CFTC"),
    ("https://www.fatf-gafi.org/en/rss.html", "FATF"),
    ("https://www.fincen.gov/news/rss", "FinCEN"),
    ("https://www.esma.europa.eu/press-news/rss", "ESMA"),
    # Tier 10: Newsletters
    ("https://weekinethereum.substack.com/feed", "Week in Ethereum"),
    ("https://banklessdao.substack.com/feed", "Bankless DAO"),
    ("https://thedefiant.substack.com/feed", "The Defiant Substack"),
    ("https://rekt.substack.com/feed", "REKT Newsletter"),
    ("https://cryptohayes.substack.com/feed", "Arthur Hayes"),
    ("https://doseofdefi.substack.com/feed", "Dose of DeFi"),
    ("https://cryptosecurity.substack.com/feed", "CryptoSecurity"),
    ("https://tokeninsight.substack.com/feed", "TokenInsight"),
    ("https://defieducation.substack.com/feed", "DeFi Education"),
    ("https://blockanalytica.substack.com/feed", "BlockAnalytica"),
    # Tier 11: Security & Research
    ("https://feeds.feedburner.com/TheHackersNews", "The Hacker News"),
    ("https://www.bleepingcomputer.com/feed/", "BleepingComputer"),
    ("https://krebsonsecurity.com/feed/", "Krebs on Security"),
    ("https://www.darkreading.com/rss.xml", "Dark Reading"),
    ("https://www.web3isgoinggreat.com/feed", "W3IGG"),
    ("https://forum.openzeppelin.com/posts.rss", "OpenZeppelin Forum"),
    ("https://ethereum-magicians.org/posts.rss", "ETH Magicians"),
    ("https://ethresear.ch/posts.rss", "ETH Research"),
    ("https://gov.uniswap.org/posts.rss", "Uniswap Governance"),
    ("https://forum.makerdao.com/posts.rss", "MakerDAO Forum"),
    # Tier 12: Market & Trading
    ("https://www.tradingview.com/blog/feed/", "TradingView"),
    ("https://blog.santiment.net/feed/", "Santiment"),
    ("https://blog.coinmarketcap.com/feed/", "CoinMarketCap"),
    ("https://www.coingecko.com/en/blog/feed", "CoinGecko Blog"),
    # Tier 13: NFT & Gaming
    ("https://nftevening.com/feed/", "NFTEvening"),
    ("https://nftplazas.com/feed/", "NFTPlazas"),
    ("https://playtoearn.net/feed", "PlayToEarn"),
    ("https://dappradar.com/blog/feed", "DappRadar"),
    # Tier 14: L2 & Scaling
    ("https://blog.matter-labs.io/feed", "zkSync"),
    ("https://starknet.io/blog/feed/", "StarkNet"),
    ("https://blog.scroll.io/feed", "Scroll"),
    ("https://blog.linea.build/feed", "Linea"),
    # Tier 15: MEV & Infra
    ("https://collective.flashbots.net/latest.rss", "Flashbots"),
    ("https://blog.chainstack.com/feed/", "Chainstack"),
    ("https://blog.alchemy.com/feed/", "Alchemy"),
    ("https://blog.infura.io/feed", "Infura"),
    ("https://blog.tenderly.co/feed", "Tenderly"),
]


# ─── REDDIT ───────────────────────────────────────────────────────
REDDIT_SUBREDDITS = [
    "CryptoCurrency", "CryptoMarkets", "ethdev", "CryptoTechnology",
    "defi", "ethfinance", "solana", "CryptoScams", "Buttcoin",
    "Bitcoin", "ethereum", "CryptoMoonShots", "altcoin", "CryptoTrading",
    "NFT", "web3", "layer2", "CryptoGaming", "CryptoCurrencyTrading",
    "SatoshiStreetBets", "Crypto_General",
]


# ─── SENTIMENT KEYWORDS ──────────────────────────────────────────
BULLISH_WORDS = [
    "surge", "soar", "rally", "bull", "breakout", "pump", "moon", "ath",
    "all-time high", "record", "adoption", "launch", "partnership", "upgrade",
    "breakthrough", "milestone", "billions", "growth", "accumulation", "green",
    "profit", "gain", "positive", "optimistic", "innovation", "mainstream",
    "institutional", "approval", "etf",
]

BEARISH_WORDS = [
    "crash", "dump", "bear", "hack", "exploit", "scam", "rug", "drain",
    "loss", "decline", "sell-off", "liquidation", "collapse", "default",
    "bankrupt", "freeze", "halt", "suspend", "delist", "sec", "lawsuit",
    "fine", "penalty", "ban", "restrict", "phishing", "vulnerability",
    "attack", "stolen", "breach", "warning", "alert", "risk", "volatile",
    "uncertainty", "fear",
]


CATEGORY_KEYWORDS = {
    "security": ["hack", "exploit", "vulnerability", "audit", "scam", "rug", "drain",
                  "phishing", "breach", "attack", "theft", "stolen", "bug", "patch"],
    "defi": ["defi", "dex", "lending", "yield", "liquidity", "swap", "amm", "pool",
             "staking", "farm", "borrow", "lend"],
    "regulation": ["sec", "cftc", "regulation", "compliance", "law", "legal", "ban",
                    "approve", "etf", "license", "registered"],
    "market": ["price", "market", "trading", "volume", "btc", "eth", "bull", "bear",
               "chart", "analysis", "prediction", "target"],
    "protocol": ["upgrade", "fork", "eip", "governance", "proposal", "vote", "dao",
                 "mainnet", "testnet", "launch", "migration"],
    "nft_gaming": ["nft", "gaming", "metaverse", "collectible", "mint", "p2e", "play"],
    "institutional": ["institution", "bank", "fund", "venture", "acquisition",
                       "investment", "treasury", "corporate"],
    "layer2": ["l2", "layer 2", "rollup", "zk", "optimistic", "scaling", "gas"],
    "privacy": ["privacy", "zkp", "zero-knowledge", "anonymous", "mixer", "tornado"],
    "academic": ["research", "paper", "study", "academic", "university", "cryptography"],
}
