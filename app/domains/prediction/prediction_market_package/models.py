"""
Prediction Market — Data Models
Unified dataclasses across all prediction market sources.
"""
from dataclasses import dataclass, field
from typing import Any

SECURITY_KEYWORDS = [
    "hack","exploit","rug","scam","fraud","breach","leak","drain","phish",
    "backdoor","vulnerability","zero-day","sanction","indict","arrest",
    "freeze","seize","clampdown","depeg","insolvent","bankrupt","collapse",
    "default","theft","heist","compromise","ransomware","malware",
    "SEC","CFTC","DOJ","FBI","regulatory","enforcement",
]

CRYPTO_KEYWORDS = [
    "bitcoin","ethereum","solana","crypto","defi","token","blockchain",
    "web3","nft","stablecoin","usdt","usdc","dai","exchange","binance",
    "coinbase","uniswap","aave","tether","circle","layer","L1","L2",
    "rollup","bridge","polygon","arbitrum","optimism","avalanche","fantom",
    "chainlink","makerdao","lido","eigenlayer",
]


@dataclass
class PredictionMarket:
    """Unified prediction market result across all sources."""
    source: str
    source_id: str
    question: str
    slug: str
    probability_yes: float
    probability_no: float
    volume_usd: float
    liquidity_usd: float = 0.0
    category: str = ""
    tags: list[str] = field(default_factory=list)
    tokens_mentioned: list[str] = field(default_factory=list)
    is_security_relevant: bool = False
    is_crypto_relevant: bool = False
    url: str = ""
    ends_at: str | None = None
    updated_at: str = ""

    def __post_init__(self) -> None:
        q_lower = self.question.lower()
        self.is_security_relevant = any(kw in q_lower for kw in SECURITY_KEYWORDS)
        self.is_crypto_relevant = any(kw in q_lower for kw in CRYPTO_KEYWORDS)


@dataclass
class PredictionDigest:
    """Daily intelligence digest of security-relevant prediction markets."""
    generated_at: str
    total_markets_searched: int
    security_relevant_count: int
    crypto_relevant_count: int
    top_threats: list[PredictionMarket] = field(default_factory=list)
    token_specific_markets: list[PredictionMarket] = field(default_factory=list)
    ecosystem_risk_markets: list[PredictionMarket] = field(default_factory=list)
    regulatory_markets: list[PredictionMarket] = field(default_factory=list)


# Token extraction helpers
_COMMON_TOKENS = {
    "BTC","ETH","SOL","USDT","USDC","DAI","BNB","XRP","ADA","DOGE",
    "MATIC","POL","DOT","AVAX","LINK","UNI","AAVE","ARB","OP","SUI",
    "APT","TIA","SEI","STRK","WLD","PEPE","SHIB","BONK","WIF","JUP",
    "PYTH","RNDR","FET","AGIX","OCEAN","IMX","INJ","EIGEN","ENA","ETHFI",
}

_COMMON_PROJECTS = {
    "polymarket","kalshi","manifold","uniswap","sushiswap","aave","compound",
    "makerdao","maker","lido","eigenlayer","chainlink","arbitrum","optimism",
    "polygon","avalanche","fantom","near","celestia","worldcoin","tether",
    "circle","coinbase","binance","kraken","ftx","celcius","blockfi",
    "three arrows","alameda","jump crypto","wintermute","curve","balancer",
    "thorchain","osmosis","dydx","gmx","hyperliquid","jupiter","raydium",
    "orca","wormhole","layerzero","zksync","starknet","scroll","linea",
    "base","mantle","mode","blast",
}


def extract_token_symbols(text: str) -> list[str]:
    """Extract known token symbols and project names from text."""
    found: list[str] = []
    text_upper = text.upper()
    text_lower = text.lower()
    for token in _COMMON_TOKENS:
        if (
            f" {token} " in f" {text_upper} "
            or f"${token}" in text_upper
            or text_upper.startswith(f"{token} ")
            or text_upper.endswith(f" {token}")
        ) and token not in found:
            found.append(token)
    for project in _COMMON_PROJECTS:
        if project in text_lower and project.upper() not in found:
            found.append(project)
    return found
