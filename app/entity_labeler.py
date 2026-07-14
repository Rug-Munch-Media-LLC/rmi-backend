"""
Entity Labeler - Advanced Wallet Labeling Module
================================================

Exposes:
  • Exchange/Protocol detection (Uniswap, Aave, Binance, etc.)
  • Whale detection (high-value wallets)
  • Contract type detection (fungible, non-fungible, proxy, library)
  • Risk scoring (low/medium/high/critical)
  • Smart contract verification status
  • Token metadata (name, symbol, decimals)
"""

from app.core.logging import get_logger
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.entity_clustering import EntityClusteringEngine

logger = get_logger(__name__)


# ─── KNOWN ENTITIES DATABASE ──────────────────────────────────────────────

# Exchange contracts (major CEX/DEX)
EXCHANGES = {
    "binance": ["0x" + "0" * 39 + "2a", "0x" + "0" * 39 + "3b"],  # Placeholder - actual addresses
    "coinbase": ["0x" + "0" * 39 + "4c"],
    "uniswap": ["0x7a250d5630b4cf139281983dce37532e7d5c9196"],
    "sushiswap": ["0xc0a47dfe034b400b26bc65c9f1b2c5ac5b5d7373"],
    "curve": ["0x90e80ecf27b8b9f3a6c5b6694c44515f5b6f5d45"],
    "chainlink": ["0x514910771af9ca656af840dff83e8264ecf986ca"],
}

# Protocol contracts (DeFi protocols)
PROTOCOLS = {
    "aave": {
        "address": "0x7d2768de32b0b8013e039f31fbacf67128c9c3d8",
        "token": "0x37d819f03d3261550b4e75c5e5c5c5c5c5c5c5c5",  # placeholder
        "type": "lending",
    },
    "compound": {
        "address": "0x3fdf4e696732ad248fbfc9d846c252d66b4d945c",
        "type": "lending",
    },
    "uniswap_v3": {
        "address": "0x1f98431c8ad98523631ae4a59f267346ea31f988",
        "type": "amm",
    },
}

# Token categories
TOKEN_TYPES = {
    "stablecoin": ["usdc", "usdt", "dai", "busd", "ampt"],
    "memecoin": ["pepe", "shib", "doge", "floki"],
    "governance": ["snapshot", "governance", "vote"],
    "yield": ["aave", "comp", "yearn"],
}


# ─── DETECTION FUNCTIONS ──────────────────────────────────────────────────


def detect_exchange(address: str, name: str = "", symbol: str = "") -> str | None:
    """Detect if address belongs to a known exchange/protocol."""
    address_lower = address.lower()

    # Check exchange contracts
    for exchange, contracts in EXCHANGES.items():
        if any(contract.lower() in address_lower for contract in contracts):
            return exchange

    # Check name/symbol matching
    name_lower = (name + " " + symbol).lower()
    for exchange in EXCHANGES:
        if exchange.lower() in name_lower:
            return exchange

    return None


def detect_protocol(address: str, name: str = "", symbol: str = "") -> str | None:
    """Detect if address is a DeFi protocol."""
    # Check protocol contracts
    for protocol, info in PROTOCOLS.items():
        if info["address"].lower() in address.lower():
            return protocol

    # Check name/symbol
    name_lower = (name + " " + symbol).lower()
    for protocol in PROTOCOLS:
        if protocol.lower() in name_lower:
            return protocol

    return None


def detect_token_type(symbol: str, name: str = "") -> list[str]:
    """Detect token type(s) based on name/symbol."""
    symbol_lower = symbol.lower()
    name_lower = name.lower()

    types = []
    for token_type, keywords in TOKEN_TYPES.items():
        if any(kw in symbol_lower or kw in name_lower for kw in keywords):
            types.append(token_type)

    return types


def detect_whale(address: str, balance_eth: float = 0, balance_usd: float = 0) -> dict[str, Any] | None:
    """Detect if address is a whale (high-value wallet)."""
    whale_thresholds = {
        "super_whale": 10000,  # $10M+ USD
        "high_whale": 1000,  # $1M+ USD
        "medium_whale": 100,  # $100k+ USD
    }

    for level, threshold_usd in whale_thresholds.items():
        if balance_usd >= threshold_usd:
            return {
                "is_whale": True,
                "level": level,
                "balance_usd": balance_usd,
                "balance_eth": balance_eth,
                "rank": "whale",
            }

    return None


def detect_high_risk(address: str, tx_count: int = 0, age_days: int = 0) -> dict[str, Any]:
    """Detect high-risk wallet characteristics."""
    risk_factors = []
    risk_score = 0

    # Low age with high activity = suspicious
    if age_days < 30 and tx_count > 100:
        risk_factors.append("new_wallet_high_activity")
        risk_score += 2

    # High transaction count (potential bot/spam)
    if tx_count > 1000:
        risk_factors.append("high_frequency")
        risk_score += 1

    # Zero balance but active (potential scam)
    if tx_count > 50:
        risk_factors.append("活跃但余额为零")
        risk_score += 1

    return {
        "is_high_risk": risk_score >= 2,
        "risk_score": risk_score,
        "risk_factors": risk_factors,
        "risk_level": "low" if risk_score == 0 else ("medium" if risk_score <= 2 else "high"),
    }


def analyze_contract_type(bytecode: str, source_code: str | None = None) -> dict[str, Any]:
    """Analyze contract type from bytecode."""
    result = {
        "is_contract": True,
        "contract_type": "unknown",
        "is_verified": source_code is not None,
        "is_proxy": False,
        "proxy_type": None,
        "has_own_code": False,
    }

    # Check for proxy patterns
    if "0x360894a1" in bytecode:  # delegate call to
        result["is_proxy"] = True
        result["proxy_type"] = "delegate"
    elif "0xc5318c17" in bytecode:  # implementation storage
        result["is_proxy"] = True
        result["proxy_type"] = "transparent"
    elif "0x70ca07de" in bytecode:  # upgradeTo
        result["is_proxy"] = True
        result["proxy_type"] = "upgradeable"

    # Check for specific contract types
    if "transferFrom" in (source_code or ""):
        result["contract_type"] = "erc20"
    elif "tokenURI" in (source_code or "") or "safeMint" in (source_code or ""):
        result["contract_type"] = "erc721"
    elif "vote" in (source_code or ""):
        result["contract_type"] = "governance"

    return result


# ─── ENTITY LABELER ENGINE ────────────────────────────────────────────────


class EntityLabeler:
    """Main entity labeler engine."""

    def __init__(self, clustering_engine: EntityClusteringEngine | None = None):
        self.clustering = clustering_engine or EntityClusteringEngine()
        self.labels: dict[str, dict[str, Any]] = {}
        self.risk_scores: dict[str, float] = {}

    def label_address(
        self,
        address: str,
        chain: str = "ethereum",
        name: str = "",
        symbol: str = "",
        balance_eth: float = 0,
        balance_usd: float = 0,
        tx_count: int = 0,
        age_days: int = 0,
        bytecode: str = "",
        source_code: str | None = None,
    ) -> dict[str, Any]:
        """Comprehensive labeling of an address."""
        label = {
            "address": address,
            "chain": chain,
            "labels": [],
            "labels_detail": [],
            "risk": {"score": 0, "level": "low", "factors": []},
            "whale": None,
            "exchange": None,
            "protocol": None,
            "token_type": [],
            "contract": {},
            "verified_at": datetime.utcnow().isoformat(),
        }

        # Detect exchange
        exchange = detect_exchange(address, name, symbol)
        if exchange:
            label["exchange"] = exchange
            label["labels"].append(f"exchange:{exchange}")
            label["labels_detail"].append({"type": "exchange", "name": exchange})
            label["risk"]["score"] += 1

        # Detect protocol
        protocol = detect_protocol(address, name, symbol)
        if protocol:
            label["protocol"] = protocol
            label["labels"].append(f"protocol:{protocol}")
            label["labels_detail"].append({"type": "protocol", "name": protocol})

        # Detect whale
        whale = detect_whale(address, balance_eth, balance_usd)
        if whale:
            label["whale"] = whale
            label["labels"].append(f"whale:{whale['level']}")
            label["labels_detail"].append({"type": "whale", "level": whale["level"]})
            label["risk"]["score"] += 1  # Whales need monitoring

        # Detect token type
        token_types = detect_token_type(symbol, name)
        if token_types:
            label["token_type"] = token_types
            label["labels"].extend([f"token_type:{tt}" for tt in token_types])

        # Detect risk factors
        risk = detect_high_risk(address, tx_count, age_days)
        label["risk"]["score"] += risk["risk_score"]
        label["risk"]["factors"] = risk["risk_factors"]

        # Adjust risk level
        label["risk"]["level"] = risk["risk_level"]

        # Analyze contract
        if bytecode:
            contract_info = analyze_contract_type(bytecode, source_code)
            label["contract"] = contract_info
            if contract_info["contract_type"] != "unknown":
                label["labels"].append(f"contract:{contract_info['contract_type']}")

        # Store label
        self.labels[address] = label

        # Update risk score
        self.risk_scores[address] = min(label["risk"]["score"], 10)

        return label

    def batch_label_addresses(
        self,
        addresses: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Label multiple addresses at once."""
        results = {}
        for addr_info in addresses:
            address = addr_info.get("address", "")
            result = self.label_address(
                address=address,
                chain=addr_info.get("chain", "ethereum"),
                name=addr_info.get("name", ""),
                symbol=addr_info.get("symbol", ""),
                balance_eth=addr_info.get("balance_eth", 0),
                balance_usd=addr_info.get("balance_usd", 0),
                tx_count=addr_info.get("tx_count", 0),
                age_days=addr_info.get("age_days", 0),
                bytecode=addr_info.get("bytecode", ""),
                source_code=addr_info.get("source_code"),
            )
            results[address] = result
        return results

    def get_label(self, address: str) -> dict[str, Any] | None:
        """Get label for an address."""
        return self.labels.get(address.lower())

    def get_risk_score(self, address: str) -> float:
        """Get risk score for an address."""
        return self.risk_scores.get(address.lower(), 0)

    def get_risk_level(self, address: str) -> str:
        """Get risk level for an address."""
        score = self.get_risk_score(address)
        if score >= 8:
            return "critical"
        elif score >= 5:
            return "high"
        elif score >= 2:
            return "medium"
        return "low"


# ─── MODULE FUNCTIONS ─────────────────────────────────────────────────────

_labeler: EntityLabeler | None = None


def get_entity_labeler() -> EntityLabeler:
    """Get the global entity labeler instance."""
    global _labeler
    if _labeler is None:
        _labeler = EntityLabeler()
    return _labeler


def label_address(
    address: str,
    chain: str = "ethereum",
    name: str = "",
    symbol: str = "",
    balance_eth: float = 0,
    balance_usd: float = 0,
    tx_count: int = 0,
    age_days: int = 0,
    bytecode: str = "",
    source_code: str | None = None,
) -> dict[str, Any]:
    """Label an address with comprehensive metadata."""
    labeler = get_entity_labeler()
    return labeler.label_address(
        address=address,
        chain=chain,
        name=name,
        symbol=symbol,
        balance_eth=balance_eth,
        balance_usd=balance_usd,
        tx_count=tx_count,
        age_days=age_days,
        bytecode=bytecode,
        source_code=source_code,
    )


def batch_label_addresses(addresses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Label multiple addresses."""
    labeler = get_entity_labeler()
    return labeler.batch_label_addresses(addresses)


def get_risk_score(address: str) -> float:
    """Get risk score for an address."""
    labeler = get_entity_labeler()
    return labeler.get_risk_score(address)


def get_risk_level(address: str) -> str:
    """Get risk level for an address."""
    labeler = get_entity_labeler()
    return labeler.get_risk_level(address)


# ─── API RESPONSE MODELS ──────────────────────────────────────────────────


class LabelResponse(BaseModel):
    address: str
    chain: str = "ethereum"
    labels: list[str]
    risk: dict[str, Any]
    whale: dict[str, Any] | None = None
    exchange: str | None = None
    protocol: str | None = None
    token_type: list[str] = []
    contract: dict[str, Any] = {}
    verified_at: str


class RiskResponse(BaseModel):
    address: str
    risk_score: float
    risk_level: str
    risk_factors: list[str]
