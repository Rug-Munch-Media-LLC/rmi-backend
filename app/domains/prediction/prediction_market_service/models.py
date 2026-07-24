"""Data models for prediction market intelligence."""
from __future__ import annotations

from dataclasses import dataclass, field

SECURITY_KEYWORDS = ["hack","exploit","rug","scam","fraud","breach","leak","drain","phish","backdoor","vulnerability","zero-day","sanction","indict","arrest","freeze","seize","clampdown","depeg","insolvent","bankrupt","collapse","default","theft","heist","compromise","ransomware","malware","SEC","CFTC","DOJ","FBI","regulatory","enforcement"]
CRYPTO_KEYWORDS = ["bitcoin","ethereum","solana","crypto","defi","token","blockchain","web3","nft","stablecoin","usdt","usdc","dai","exchange","binance","coinbase","uniswap","aave","tether","circle","layer","L1","L2","rollup","bridge","polygon","arbitrum","optimism","avalanche","fantom","chainlink","makerdao","lido","eigenlayer"]

@dataclass
class PredictionMarket:
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
    generated_at: str
    total_markets_searched: int
    security_relevant_count: int
    crypto_relevant_count: int
    top_threats: list[PredictionMarket] = field(default_factory=list)
    token_specific_markets: list[PredictionMarket] = field(default_factory=list)
    ecosystem_risk_markets: list[PredictionMarket] = field(default_factory=list)
    regulatory_markets: list[PredictionMarket] = field(default_factory=list)
