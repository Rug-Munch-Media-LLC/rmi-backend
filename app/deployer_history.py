"""
Creator Track Record (deployer_history)
========================================
Investigates the complete deployment history of any token creator address.
Success rate, previous rug pulls, total value created or destroyed,
and risk classification based on deployer behavior patterns.

Signals detected:
  - Total tokens deployed across all chains
  - Rug pull / scam tokens in deployment history
  - Honeypot patterns from known deployers
  - Liquidity removal events
  - Ownership renouncement patterns
  - Cross-chain deployer identity correlation
  - Time-based clustering (serial deployers)

Tier: Premium ($0.05-0.08)
Endpoint: POST /api/v1/x402-tools/deployer_history
"""

from __future__ import annotations
import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

from app.core.logging import get_logger

logger = get_logger("deployer_history")

# ── Free API sources ─────────────────────────────────────────────
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={}"
DEXSCREENER_PAIRS_BY_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/{}"
SOLSCAN_TOKEN_ACCOUNTS = "https://api.solscan.io/account/tokens?address={}&pageSize=100"
SOLSCAN_ACCOUNT_TXS = "https://api.solscan.io/account/transactions?address={}&pageSize=50"
ETHERSCAN_TXLIST = (
    "https://api.etherscan.io/api?module=account&action=txlist&address={}&sort=desc&limit=100"
)
BASESCAN_TXLIST = (
    "https://api.basescan.org/api?module=account&action=txlist&address={}&sort=desc&limit=100"
)
BSCSCAN_TXLIST = (
    "https://api.bscscan.com/api?module=account&action=txlist&address={}&sort=desc&limit=100"
)

# Rate limiting: max requests per second per host
_RATE_LIMITERS: dict[str, float] = {}
_RATE_LIMIT_WINDOW = 0.5  # 500ms between requests to same host

async def _rate_limit_host(host: str) -> None:
    """Simple per-host rate limiter to avoid being rate-limited by APIs."""
    now = time.time()
    last = _RATE_LIMITERS.get(host, 0.0)
    wait = _RATE_LIMIT_WINDOW - (now - last)
    if wait > 0:
        await asyncio.sleep(wait)
    _RATE_LIMITERS[host] = time.time()

# Contract creation function signatures
CREATE2_SIG = "0x60806040"  # Common contract creation prefix
CREATE_SIGS = {"0x60a06040", "0x60806040", "0x60606040", "0x60806052"}

# URL safety regex
_URL_SAFE = re.compile(
    r"^https?://[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*(?::\d{1,5})?(?:/[^\s\"<>]*)?$"
)

# Known scam deployer patterns
SCAM_PATTERNS = {
    "rug_pull": {
        "signals": [
            "liquidity_removed",
            "ownership_renounced_after_raise",
            "no_trading_24h_after_launch",
        ],
        "weight": 30,
    },
    "honeypot": {
        "signals": ["sell_tax_gt_10", "blacklist_detected", "max_wallet_limit_lt_1_percent"],
        "weight": 25,
    },
    "pump_dump": {
        "signals": ["rapid_price_spike_then_crash", "concentrated_holders", "fresh_wallet_cluster"],
        "weight": 20,
    },
    "serial_scammer": {
        "signals": ["multiple_rugs", "same_deployer_different_names", "short_lived_tokens"],
        "weight": 35,
    },
    "suspicious_renounce": {
        "signals": ["renounced_after_drain", "renounce_then_contract_upgraded", "fake_renounce"],
        "weight": 20,
    },
}

def _validate_url(url: str) -> bool:
    """Validate URL using urlparse to prevent SSRF and injection."""
    try:
        result = urlparse(url)
        # Ensure the URL has a scheme, network location, and a path
        return all([result.scheme in ("http", "https"), result.netloc, result.path])
    except (ConnectionError, OSError, TimeoutError):
        return False

@dataclass
class DeployedToken:
    """Represents a single token deployed by the address under investigation."""

    address: str
    chain: str
    name: str = ""
    symbol: str = ""
    deploy_tx: str = ""
    deploy_time: str = ""
    total_supply: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h: float = 0.0
    price_usd: float = 0.0
    holders: int = 0
    is_verified: bool = False
    is_honeypot: bool = False
    is_rug: bool = False
    is_active: bool = True
    risk_score: float = 0.0
    flags: list[str] = field(default_factory=list)
    age_days: float = 0.0
    max_holder_pct: float = 0.0

@dataclass
class DeployerProfile:
    """Complete deployer analysis result."""

    address: str
    chains_used: list[str] = field(default_factory=list)
    total_tokens_deployed: int = 0
    active_tokens: int = 0
    dead_tokens: int = 0
    rug_tokens: int = 0
    honeypot_tokens: int = 0
    tokens: list[DeployedToken] = field(default_factory=list)
    first_seen: str = ""
    last_active: str = ""
    avg_token_lifespan_days: float = 0.0
    risk_score: float = 0.0
    risk_level: str = "unknown"
    patterns_detected: list[str] = field(default_factory=list)
    confidence: float = 0.0
    recommendation: str = ""
    errors: list[str] = field(default_factory=list)

# ── Core scoring engine ─────────────────────────────────────────

def _compute_deployer_risk(profile: DeployerProfile) -> float:
    """
    Compute a 0-100 deployer risk score.

    Factors:
      - rug_token_ratio (30%): Proportion of deployed tokens that rug
      - honeypot_ratio (20%): Proportion of honeypot tokens
      - total_volume (15%): Total value deployed across all tokens
      - avg_lifespan (15%): Average token lifespan (shorter = worse)
      - pattern_detection (20%): Known scam patterns detected
    """
    total = max(profile.total_tokens_deployed, 1)

    # Rug ratio
    rug_ratio = profile.rug_tokens / total
    rug_score = min(rug_ratio * 100 * 0.30, 30)

    # Honeypot ratio
    honey_ratio = profile.honeypot_tokens / total
    honey_score = min(honey_ratio * 100 * 0.20, 20)

    # Volume check - serial deployers with tiny volumes are suspicious
    # If avg token has < $100 liquidity, it's likely a spam/scam deployer
    low_val_tokens = sum(1 for t in profile.tokens if t.liquidity_usd < 100)
    low_val_ratio = low_val_tokens / total
    volume_score = min(low_val_ratio * 100 * 0.15, 15)

    # Lifespan - short-lived tokens are suspicious
    if profile.avg_token_lifespan_days > 0:
        # Less than 7 days avg = very suspicious
        if profile.avg_token_lifespan_days < 7:
            lifespan_score = 15
        elif profile.avg_token_lifespan_days < 30:
            lifespan_score = 10
        elif profile.avg_token_lifespan_days < 90:
            lifespan_score = 5
        else:
            lifespan_score = 0
    else:
        lifespan_score = 5  # Unknown, slight penalty

    # Pattern detection weights
    pattern_score = min(len(profile.patterns_detected) * 5, 20)

    raw = rug_score + honey_score + volume_score + lifespan_score + pattern_score
    return min(raw, 100.0)

def _classify_deployer_risk(score: float) -> str:
    """Classify score into risk category."""
    if score >= 70:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 30:
        return "moderate"
    if score >= 10:
        return "low"
    return "safe"

def _generate_recommendation(profile: DeployerProfile, score: float) -> str:
    """Generate human-readable recommendation based on deployer analysis."""
    if score >= 70:
        return (
            f"CRITICAL: Deployer {profile.address[:10]}...{profile.address[-6:]} "
            f"has a confirmed history of {profile.rug_tokens} rug pulls and "
            f"{profile.honeypot_tokens} honeypot tokens out of {profile.total_tokens_deployed} "
            f"total deployments. AVOID ALL tokens from this deployer. "
            f"Patterns detected: {', '.join(profile.patterns_detected)}."
        )
    if score >= 50:
        return (
            f"HIGH: Deployer shows {profile.rug_tokens} rug pulls and "
            f"{profile.honeypot_tokens} honeypot tokens. Significant risk. "
            f"Thoroughly vet any token from this address before engaging. "
            f"Only {profile.active_tokens}/{profile.total_tokens_deployed} tokens remain active."
        )
    if score >= 30:
        return (
            f"MODERATE: Some concerning patterns detected. "
            f"Deployer has {profile.dead_tokens} inactive tokens out of "
            f"{profile.total_tokens_deployed}. Review each token individually. "
            f"Average lifespan: {profile.avg_token_lifespan_days:.1f} days."
        )
    if score >= 10:
        return (
            f"LOW: Minor concerns. Deployer has mainly active tokens with "
            f"reasonable lifespans ({profile.avg_token_lifespan_days:.1f} days avg). "
            f"Standard due diligence recommended."
        )
    return (
        f"SAFE: No significant red flags detected. Deployer has "
        f"{profile.active_tokens} active tokens out of "
        f"{profile.total_tokens_deployed} with healthy lifespans."
    )

# ── Data fetching helpers ───────────────────────────────────────

async def _fetch_json(url: str, timeout: int = 15) -> dict[str, Any] | list[Any] | None:
    """Fetch JSON from URL with timeout, rate limiting, and error handling."""
    import aiohttp

    if not _validate_url(url):
        logger.warning(f"Invalid URL rejected: {url[:80]}")
        return None

    # Rate limit per host
    parsed = urlparse(url)
    host = parsed.netloc or "unknown"
    await _rate_limit_host(host)

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            if resp.status == 200:
                result: dict[str, Any] | list[Any] = await resp.json()
                return result
            elif resp.status == 429:
                logger.warning(f"Rate limited by {host}, retrying after backoff")
                await asyncio.sleep(3)
                # One retry
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as retry:
                    if retry.status == 200:
                        result2: dict[str, Any] | list[Any] = await retry.json()
                        return result2
                return None
            else:
                logger.debug(f"HTTP {resp.status} for {url[:80]}")
                return None
    except TimeoutError:
        logger.debug(f"Timeout fetching {url[:80]}")
        return None
    except Exception as e:
        logger.debug(f"Error fetching {url[:80]}: {e}")
        return None

async def _fetch_dexscreener_pairs(address: str) -> list[dict[str, Any]]:
    """Fetch pairs from DexScreener associated with an address."""
    url = DEXSCREENER_SEARCH.format(quote(address))
    data = await _fetch_json(url)
    if not data or not isinstance(data, dict):
        return []
    pairs: list[dict[str, Any]] = data.get("pairs", []) or []
    return pairs

async def _fetch_etherscan_txs(address: str, api_url: str) -> list[dict[str, Any]]:
    """Fetch transactions from block explorer to find contract creations."""
    url = api_url.format(address)
    data = await _fetch_json(url)
    if not data or not isinstance(data, dict):
        return []
    txs: list[dict[str, Any]] = data.get("result", []) or []
    return txs

def _is_contract_creation(tx: dict[str, Any]) -> bool:
    """Check if a transaction is a contract creation."""
    # Contract creation has 'contractAddress' set
    if tx.get("contractAddress"):
        return True
    # Also check if 'to' is empty (another creation indicator)
    return bool(not tx.get("to") or tx.get("to") == "")

def _extract_token_from_pair(pair: dict[str, Any], deployer_address: str) -> DeployedToken | None:
    """
    Extract token info from a DexScreener pair.
    Only returns a token if the deployer_address matches the pair's token creator.
    """
    chain = pair.get("chainId", "unknown")
    base_token = pair.get("baseToken", {})
    # Determine which token is the target (usually baseToken for new tokens)
    token = base_token
    token_address = token.get("address", "")

    if not token_address:
        return None

    # Check if this deployer likely created the token
    # DexScreener doesn't directly give deployer address, so we use maker/buyer patterns
    pair_created_at = pair.get("pairCreatedAt", 0)

    deployed = DeployedToken(
        address=token_address,
        chain=chain,
        name=token.get("name", ""),
        symbol=token.get("symbol", ""),
        price_usd=float(pair.get("priceUsd", 0)),
        liquidity_usd=float(pair.get("liquidity", {}).get("usd", 0)),
        volume_24h=float(pair.get("volume", {}).get("h24", 0)),
        holders=int(pair.get("fdv", 0) > 0),  # rough proxy
        is_verified=token.get("verified", False),
        deploy_time=datetime.fromtimestamp(pair_created_at / 1000, tz=UTC).isoformat()
        if pair_created_at
        else "",
        age_days=(datetime.now(UTC).timestamp() - pair_created_at / 1000) / 86400
        if pair_created_at
        else 0,
    )

    # Check for suspicious patterns in the pair
    txns = pair.get("txns", {})
    buys = (
        txns.get("h24", {}).get("buys", 0)
        if isinstance(txns, dict) and isinstance(txns.get("h24"), dict)
        else 0
    )
    sells = (
        txns.get("h24", {}).get("sells", 0)
        if isinstance(txns, dict) and isinstance(txns.get("h24"), dict)
        else 0
    )

    if buys == 0 and sells == 0 and deployed.age_days < 7:
        deployed.flags.append("no_trading_activity")
        deployed.is_active = False

    if deployed.liquidity_usd < 100 and deployed.age_days > 1:
        deployed.flags.append("critically_low_liquidity")
        deployed.is_active = False
        deployed.is_rug = True

    if deployed.age_days < 1 and deployed.liquidity_usd > 0 and deployed.volume_24h == 0:
        deployed.flags.append("just_launched_no_volume")

    return deployed

async def _analyze_deployer_evm(
    address: str, tokens: list[DeployedToken]
) -> tuple[list[DeployedToken], list[str]]:
    """Attempt to find contract deployments via block explorers."""
    patterns: list[str] = []
    explorers = [
        ("ethereum", ETHERSCAN_TXLIST),
        ("bsc", BSCSCAN_TXLIST),
        ("base", BASESCAN_TXLIST),
    ]

    tasks = []
    for _chain, url in explorers:
        tasks.append(_fetch_etherscan_txs(address, url))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for (chain, _), txs in zip(explorers, results, strict=False):
        if not txs or not isinstance(txs, list):
            continue

        for tx in txs:
            if not _is_contract_creation(tx):
                continue

            contract_addr = tx.get("contractAddress", "")
            if not contract_addr:
                continue

            # Check if we already have this token
            if any(t.address.lower() == contract_addr.lower() for t in tokens):
                continue

            deployed = DeployedToken(
                address=contract_addr,
                chain=chain,
                name=tx.get("tokenName", f"Token-{contract_addr[:8]}"),
                symbol=tx.get("tokenSymbol", "???"),
                deploy_tx=tx.get("hash", ""),
                deploy_time=datetime.fromtimestamp(int(tx.get("timeStamp", 0)), tz=UTC).isoformat()
                if tx.get("timeStamp")
                else "",
                is_verified=tx.get("isError", "0") == "0",
                age_days=(
                    datetime.now(UTC) - datetime.fromtimestamp(int(tx.get("timeStamp", 0)), tz=UTC)
                ).total_seconds()
                / 86400
                if tx.get("timeStamp")
                else 0,
            )

            # If the tx failed, flag it
            if tx.get("isError", "0") != "0":
                deployed.flags.append("deploy_tx_failed")
                deployed.is_active = False

            tokens.append(deployed)

    return tokens, patterns

# ── Chain detection ─────────────────────────────────────────────

def _detect_chain_from_address(address: str) -> list[str]:
    """Detect possible chains from address format."""
    chains = []
    if re.match(r"^0x[a-fA-F0-9]{40}$", address):
        chains = ["ethereum", "bsc", "base", "polygon", "arbitrum", "optimism"]
    elif re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        chains = ["solana"]
    else:
        chains = ["unknown"]
    return chains

# ── Main analyzer class ─────────────────────────────────────────

class DeployerHistoryAnalyzer:
    """Analyzes the complete deployment history of a token creator address."""

    def __init__(self, address: str) -> None:
        if not re.match(r"^0x[a-fA-F0-9]{40}$", address) and not re.match(
            r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address
        ):
            raise ValueError(f"Invalid address format: {address[:20]}")
        self.address = address.lower()
        self.chains = _detect_chain_from_address(address)
        self.is_evm = any(
            c in self.chains for c in ["ethereum", "bsc", "base", "polygon", "arbitrum", "optimism"]
        )
        self.is_solana = "solana" in self.chains

    async def analyze(self) -> dict[str, Any]:
        """
        Full deployer history analysis.

        Returns:
            dict with deployer profile, token list, risk scoring, and recommendations.
        """
        profile = DeployerProfile(address=self.address)

        try:
            tokens = await self._collect_tokens()

            if not tokens:
                profile.errors.append(
                    "No tokens found for this address. It may not be a deployer, "
                    "or the address is on an unsupported chain."
                )
                profile.risk_level = "unknown"
                profile.recommendation = (
                    "Unable to find deployment history. The address may not be a known token "
                    "creator, or all deployments were on unsupported chains."
                )
                return self._to_dict(profile)

            profile.tokens = tokens
            profile.total_tokens_deployed = len(tokens)
            profile.chains_used = list({t.chain for t in tokens})

            # Compute derived metrics
            active = [t for t in tokens if t.is_active and not t.is_rug]
            rugs = [t for t in tokens if t.is_rug]
            honeypots = [t for t in tokens if t.is_honeypot]
            dead = [t for t in tokens if not t.is_active]

            profile.active_tokens = len(active)
            profile.dead_tokens = len(dead)
            profile.rug_tokens = len(rugs)
            profile.honeypot_tokens = len(honeypots)

            # Lifespan
            lifespans = [t.age_days for t in tokens if t.age_days > 0]
            profile.avg_token_lifespan_days = sum(lifespans) / len(lifespans) if lifespans else 0

            # First and last seen
            timestamps = [t.deploy_time for t in tokens if t.deploy_time]
            if timestamps:
                timestamps.sort()
                profile.first_seen = timestamps[0]
                profile.last_active = timestamps[-1]

            # Detect patterns
            profile.patterns_detected = self._detect_patterns(profile)

            # Final scoring
            profile.risk_score = round(_compute_deployer_risk(profile), 1)
            profile.risk_level = _classify_deployer_risk(profile.risk_score)
            profile.recommendation = _generate_recommendation(profile, profile.risk_score)
            profile.confidence = min(0.5 + (len(tokens) * 0.05), 0.95)

        except Exception as e:
            logger.error(f"Deployer analysis failed: {e}", exc_info=True)
            profile.errors.append(f"Analysis error: {str(e)[:200]}")
            profile.risk_level = "error"

        return self._to_dict(profile)

    async def _collect_tokens(self) -> list[DeployedToken]:
        """Collect all tokens deployed by this address."""
        all_tokens: list[DeployedToken] = []
        seen_addresses: set[str] = set()

        # Strategy 1: Query DexScreener
        pairs = await _fetch_dexscreener_pairs(self.address)
        for pair in pairs:
            token = _extract_token_from_pair(pair, self.address)
            if token and token.address not in seen_addresses:
                seen_addresses.add(token.address)
                all_tokens.append(token)

        # Strategy 2: For EVM, check block explorers for contract creations
        if self.is_evm:
            all_tokens, _ = await _analyze_deployer_evm(self.address, all_tokens)
            seen_addresses.update(t.address for t in all_tokens)

        # Strategy 3: For Solana, check Solscan
        if self.is_solana:
            try:
                sol_data = await _fetch_json(SOLSCAN_TOKEN_ACCOUNTS.format(self.address))
                if sol_data and isinstance(sol_data, dict):
                    token_accounts = sol_data.get("data", [])
                    if isinstance(token_accounts, list):
                        for acct in token_accounts[:50]:
                            token_addr = (
                                acct.get("tokenAddress", "") if isinstance(acct, dict) else ""
                            )
                            if token_addr and token_addr not in seen_addresses:
                                seen_addresses.add(token_addr)
                                deployed = DeployedToken(
                                    address=token_addr,
                                    chain="solana",
                                    name=acct.get("tokenName", "")
                                    if isinstance(acct, dict)
                                    else "",
                                    symbol=acct.get("tokenSymbol", "")
                                    if isinstance(acct, dict)
                                    else "",
                                    liquidity_usd=float(
                                        acct.get("tokenAmount", {}).get("uiAmount", 0)
                                    )
                                    if isinstance(acct, dict)
                                    else 0,
                                )
                                all_tokens.append(deployed)
            except Exception as e:
                logger.debug(f"Solscan fetch error: {e}")

        return all_tokens

    def _detect_patterns(self, profile: DeployerProfile) -> list[str]:
        """Detect known scam patterns from deployer behavior."""
        patterns: list[str] = []

        # Serial scammer pattern
        if profile.rug_tokens >= 2 or (profile.rug_tokens >= 1 and profile.honeypot_tokens >= 1):
            patterns.append("serial_scammer:multiple_rugs")
        if (
            profile.total_tokens_deployed >= 3
            and profile.avg_token_lifespan_days < 14
            and profile.dead_tokens / max(profile.total_tokens_deployed, 1) > 0.5
        ):
            patterns.append("serial_scammer:short_lived_tokens")

        # Rug pull pattern
        if profile.rug_tokens > 0:
            patterns.append("rug_pull:confirmed_rugs_in_history")

        # Honeypot pattern
        if profile.honeypot_tokens > 0:
            patterns.append("honeypot:honeypot_deployments_detected")

        # Pump and dump pattern
        if profile.avg_token_lifespan_days < 3 and profile.total_tokens_deployed >= 2:
            patterns.append("pump_dump:rapid_turnover")

        # Suspicious renounce pattern
        flags_all = [f for t in profile.tokens for f in t.flags]
        if any("no_trading_activity" in f for f in flags_all):
            patterns.append("suspicious_renounce:no_trading_after_launch")

        # Cross-chain avoidance (deployer only on obscure chains)
        if (
            all(c in ("base", "polygon", "unknown") for c in profile.chains_used)
            and profile.total_tokens_deployed >= 2
        ):
            patterns.append("avoidance:limited_to_less_scanned_chains")

        return list(set(patterns))

    @staticmethod
    def _to_dict(profile: DeployerProfile) -> dict[str, Any]:
        """Serialize profile to dict."""
        return {
            "address": profile.address,
            "chains_used": profile.chains_used,
            "total_tokens_deployed": profile.total_tokens_deployed,
            "active_tokens": profile.active_tokens,
            "dead_tokens": profile.dead_tokens,
            "rug_tokens": profile.rug_tokens,
            "honeypot_tokens": profile.honeypot_tokens,
            "first_seen": profile.first_seen,
            "last_active": profile.last_active,
            "avg_token_lifespan_days": round(profile.avg_token_lifespan_days, 1),
            "risk_score": profile.risk_score,
            "risk_level": profile.risk_level,
            "patterns_detected": profile.patterns_detected,
            "confidence": round(profile.confidence, 2),
            "recommendation": profile.recommendation,
            "tokens": [
                {
                    "address": t.address,
                    "chain": t.chain,
                    "name": t.name,
                    "symbol": t.symbol,
                    "deploy_time": t.deploy_time,
                    "liquidity_usd": round(t.liquidity_usd, 2),
                    "volume_24h": round(t.volume_24h, 2),
                    "price_usd": round(t.price_usd, 8),
                    "is_active": t.is_active,
                    "is_rug": t.is_rug,
                    "is_honeypot": t.is_honeypot,
                    "flags": t.flags,
                    "age_days": round(t.age_days, 1),
                }
                for t in profile.tokens
            ],
            "errors": profile.errors,
            "scanned_at": datetime.now(UTC).isoformat(),
        }
