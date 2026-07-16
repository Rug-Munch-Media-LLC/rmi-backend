"""
Insider Web Mapper
==================
Map the complete network of insider-connected wallets across token projects.
Reveals shared funding sources, coordinated trading rings, and team-to-team
relationships - all using free-tier data sources.

TOOL  : insider_network
TIER  : premium / intelligence
PRICE : $0.10 (100000 atoms)
TRIAL : 1 free check

Data Sources (all free):
- DexScreener - token pairs and holders
- Solscan (public) - token holder lists
- Etherscan/BscScan (public free tier) - EVM token holders
- Birdeye public - holder data
- Public RPCs - on-chain queries
"""

import asyncio
import json
import re
import time
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────

FREE_APIS = {
    "dexscreener": "https://api.dexscreener.com/latest/dex",
    "birdeye_public": "https://public-api.birdeye.so",
    "jupiter": "https://api.jup.ag",
}

CHAIN_NAMES: dict[str, str] = {
    "solana": "Solana",
    "ethereum": "Ethereum",
    "bsc": "BSC",
    "polygon": "Polygon",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "base": "Base",
}

ETHERSCAN_BASES: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "base": "https://api.basescan.org/api",
}

SOLSCAN_BASE = "https://api.solscan.io"

ADDRESS_PATTERN_EVM = re.compile(r"^0x[a-fA-F0-9]{40}$")
ADDRESS_PATTERN_SOLANA = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Cache for HTTP responses
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 120  # 2 minutes
_MAX_STALE_TTL = 600  # 10 minutes - refuse stale cache older than this


# ── Data Models ───────────────────────────────────────────────────


@dataclass
class InsiderLink:
    """A connection between two wallets/entities indicating insider relationship."""

    source_wallet: str
    target_wallet: str
    relationship_type: str  # "shared_funding", "co_trading", "co_deploy", "value_transfer"
    strength: float = 0.0  # 0.0 - 1.0 confidence
    evidence: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""


@dataclass
class InsiderCluster:
    """A cluster of wallets that form an insider network."""

    cluster_id: str
    wallets: list[str] = field(default_factory=list)
    projects_involved: list[str] = field(default_factory=list)
    total_value_usd: float = 0.0
    risk_score: float = 0.0  # 0-100
    risk_factors: list[str] = field(default_factory=list)
    member_count: int = 0
    links: list[InsiderLink] = field(default_factory=list)
    top_relationships: list[dict] = field(default_factory=list)


@dataclass
class InsiderNetworkReport:
    """Complete insider network report."""

    target_wallet: str
    target_label: str = ""
    clusters: list[InsiderCluster] = field(default_factory=list)
    total_connected_wallets: int = 0
    total_clusters: int = 0
    highest_risk_score: float = 0.0
    summary: str = ""


# ── Address Validation ───────────────────────────────────────────


def _validate_eip55_checksum(address: str) -> bool:
    """
    Validate EIP-55 mixed-case checksum for Ethereum addresses.
    Implements the exact EIP-55 algorithm using keccak256 hashing.
    """
    if not address.startswith("0x") or len(address) != 42:
        return False

    try:
        # EIP-55: lowercase address, hash with keccak256
        addr_lower = address[2:].lower().encode("ascii")

        # Try to use eth_hash if available (pysha3)
        try:
            from eth_hash.auto import keccak

            hashed = keccak(addr_lower).hex()
        except ImportError:
            # Fallback: accept mixed-case without strict EIP-55
            return True

        # EIP-55 checksum validation
        for i, ch in enumerate(address[2:]):
            if ch.isalpha():
                should_be_upper = int(hashed[i], 16) >= 8
                if should_be_upper and ch.islower():
                    return False
                if not should_be_upper and ch.isupper():
                    return False
        return True
    except Exception:
        # If keccak unavailable, accept basic hex format
        return bool(ADDRESS_PATTERN_EVM.match(address))


def is_valid_address(address: str) -> bool:
    """Validate Solana (base58) or EVM (0x-hex) address with checksum validation."""
    if not address or not isinstance(address, str):
        return False
    addr = address.strip()

    # EVM address validation
    if ADDRESS_PATTERN_EVM.match(addr):
        # EIP-55 checksum validation
        if addr == addr.lower() or addr == addr.upper():
            return True  # All-lower or all-upper passes basic validation
        return _validate_eip55_checksum(addr)

    # Solana address validation (base58)
    return bool(ADDRESS_PATTERN_SOLANA.match(addr))


def _truncate_address(addr: str) -> str:
    """Truncate address for display."""
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


# ── HTTP Helpers with Caching ────────────────────────────────────


async def _cached_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cache_ttl: int = _CACHE_TTL,
) -> dict[str, Any] | None:
    """GET with simple in-memory caching and bounded stale-return."""
    cache_key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
    now = time.time()

    if cache_key in _cache:
        cached_at, cached_data = _cache[cache_key]
        if now - cached_at < cache_ttl:
            return cached_data

    try:
        resp = await client.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            _cache[cache_key] = (now, data)
            return data
        elif resp.status_code == 429:
            # Rate limited - return stale cache if fresh enough
            if cache_key in _cache:
                cached_at, cached_data = _cache[cache_key]
                if now - cached_at < _MAX_STALE_TTL:
                    return cached_data
            return None
        return None
    except (httpx.TimeoutException, httpx.RequestError):
        if cache_key in _cache:
            cached_at, cached_data = _cache[cache_key]
            if now - cached_at < _MAX_STALE_TTL:
                return cached_data
        return None


# ── API Data Fetching ────────────────────────────────────────────


async def _fetch_dexscreener_pairs(client: httpx.AsyncClient, token_address: str) -> list[dict]:
    """Fetch trading pairs for a token from DexScreener."""
    url = f"{FREE_APIS['dexscreener']}/tokens/{token_address}"
    data = await _cached_get(client, url)
    if data and "pairs" in data:
        return data["pairs"]
    return []


async def _fetch_etherscan_holders(
    client: httpx.AsyncClient,
    token_address: str,
    chain: str,
    api_key: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Fetch top token holders from Etherscan-like explorer."""
    base = ETHERSCAN_BASES.get(chain)
    if not base:
        return []

    params: dict[str, Any] = {
        "module": "token",
        "action": "tokenholderlist",
        "contractaddress": token_address,
        "limit": min(limit, 500),
    }
    if api_key:
        params["apikey"] = api_key

    data = await _cached_get(client, base, params=params)
    if data and data.get("status") == "1" and "result" in data:
        result = data["result"]
        if isinstance(result, list):
            return result
    return []


async def _fetch_solscan_holders(
    client: httpx.AsyncClient, token_address: str, limit: int = 100
) -> list[dict]:
    """Fetch top token holders from Solscan (free public API)."""
    params = {
        "tokenAddress": token_address,
        "limit": str(min(limit, 100)),
        "offset": "0",
    }
    headers = {"Accept": "application/json"}
    url = f"{SOLSCAN_BASE}/token/holders"
    data = await _cached_get(client, url, params=params, headers=headers)
    if data and "data" in data:
        result = data["data"]
        if isinstance(result, list):
            return result
    return []


async def _fetch_birdeye_holders(
    client: httpx.AsyncClient, token_address: str, chain: str = "solana"
) -> list[dict]:
    """Fetch holder data from Birdeye public API."""
    chain_map: dict[str, str] = {
        "solana": "solana",
        "ethereum": "ethereum",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "base": "base",
    }
    c = chain_map.get(chain, "solana")
    url = f"{FREE_APIS['birdeye_public']}/public/defi/holderlist"
    params = {"address": token_address, "chain": c, "limit": 100}
    headers = {"Accept": "application/json"}
    data = await _cached_get(client, url, params=params, headers=headers)
    if data and "data" in data and "items" in data["data"]:
        return data["data"]["items"]
    return []


async def _fetch_token_transfers(
    client: httpx.AsyncClient,
    token_address: str,
    chain: str,
    limit: int = 100,
) -> list[dict]:
    """Fetch recent token transfers from explorer APIs."""
    base = ETHERSCAN_BASES.get(chain)
    if not base:
        return []

    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": token_address,
        "sort": "desc",
        "limit": str(min(limit, 100)),
    }
    data = await _cached_get(client, base, params=params)
    if data and data.get("status") == "1" and "result" in data:
        result = data["result"]
        if isinstance(result, list):
            return result
    return []


async def _fetch_wallet_transactions(
    client: httpx.AsyncClient, wallet_address: str, chain: str, limit: int = 50
) -> list[dict]:
    """Fetch normal transactions for a wallet address."""
    base = ETHERSCAN_BASES.get(chain)
    if not base:
        return []

    params = {
        "module": "account",
        "action": "txlist",
        "address": wallet_address,
        "sort": "desc",
        "limit": str(min(limit, 50)),
    }
    data = await _cached_get(client, base, params=params)
    if data and data.get("status") == "1" and "result" in data:
        result = data["result"]
        if isinstance(result, list):
            return result
    return []


# ── Core Analysis Logic ──────────────────────────────────────────


def _detect_shared_funding(
    cluster_wallets: list[str],
    wallet_transactions: dict[str, list[dict]],
) -> list[InsiderLink]:
    """Detect wallets that share common funding sources."""
    links: list[InsiderLink] = []
    funders: dict[str, set[str]] = defaultdict(set)

    for wallet in cluster_wallets:
        txs = wallet_transactions.get(wallet, [])
        for tx in txs[:10]:
            sender = tx.get("from", "").lower()
            if sender and sender != wallet.lower():
                funders[sender].add(wallet)

    for funder, wallets_set in funders.items():
        wallet_list = list(wallets_set)
        if len(wallet_list) >= 2:
            for i in range(len(wallet_list)):
                for j in range(i + 1, len(wallet_list)):
                    strength = min(1.0, 0.3 + 0.1 * len(wallet_list))
                    link = InsiderLink(
                        source_wallet=wallet_list[i],
                        target_wallet=wallet_list[j],
                        relationship_type="shared_funding",
                        strength=strength,
                        evidence=[f"Both funded by {_truncate_address(funder)}"],
                    )
                    links.append(link)
    return links


def _detect_co_trading(
    cluster_wallets: list[str],
    token_transfers: dict[str, list[dict]],
) -> list[InsiderLink]:
    """Detect wallets that trade the same tokens at similar times."""
    links: list[InsiderLink] = []
    token_wallets: dict[str, set[str]] = defaultdict(set)

    for wallet in cluster_wallets:
        transfers = token_transfers.get(wallet, [])
        for tx in transfers:
            token = tx.get("contractAddress", "").lower()
            if token:
                token_wallets[token].add(wallet.lower())

    for token, wallets_set in token_wallets.items():
        wallet_list = list(wallets_set)
        if len(wallet_list) >= 2:
            for i in range(len(wallet_list)):
                for j in range(i + 1, len(wallet_list)):
                    link = InsiderLink(
                        source_wallet=wallet_list[i],
                        target_wallet=wallet_list[j],
                        relationship_type="co_trading",
                        strength=0.4,
                        evidence=[f"Both traded token {_truncate_address(token)}"],
                    )
                    existing = [
                        lnk
                        for lnk in links
                        if lnk.source_wallet == link.source_wallet
                        and lnk.target_wallet == link.target_wallet
                    ]
                    if existing:
                        existing[0].strength = min(1.0, existing[0].strength + 0.1)
                        existing[0].evidence.append(f"Also traded {_truncate_address(token)}")
                    else:
                        links.append(link)
    return links


def _detect_value_transfers(
    cluster_wallets: list[str],
    wallet_transactions: dict[str, list[dict]],
) -> list[InsiderLink]:
    """Detect direct value transfers between wallets in the cluster."""
    links: list[InsiderLink] = []
    wallet_set = {w.lower() for w in cluster_wallets}

    for wallet in cluster_wallets:
        txs = wallet_transactions.get(wallet, [])
        for tx in txs[:30]:
            from_addr = tx.get("from", "").lower()
            to_addr = tx.get("to", "").lower()
            value = float(tx.get("value", 0))

            if from_addr in wallet_set and to_addr in wallet_set and from_addr != to_addr:
                link = InsiderLink(
                    source_wallet=from_addr,
                    target_wallet=to_addr,
                    relationship_type="value_transfer",
                    strength=min(1.0, 0.5 + 0.3 * min(value / 10**18, 1.0)),
                    evidence=[f"Direct transfer of {value / 10**18:.4f} native token"],
                )
                links.append(link)
    return links


def _detect_co_deployer(
    cluster_wallets: list[str],
    wallet_transactions: dict[str, list[dict]],
) -> list[InsiderLink]:
    """Detect wallets that were funded by the same deployer."""
    links: list[InsiderLink] = []
    deployers: dict[str, set[str]] = defaultdict(set)

    for wallet in cluster_wallets:
        txs = wallet_transactions.get(wallet, [])
        if txs:
            first_tx = txs[-1] if len(txs) > 5 else txs[0]
            deployer = first_tx.get("from", "").lower()
            if deployer and deployer != wallet.lower():
                deployers[deployer].add(wallet)

    for deployer, wallets_set in deployers.items():
        wallet_list = list(wallets_set)
        if len(wallet_list) >= 2:
            for i in range(len(wallet_list)):
                for j in range(i + 1, len(wallet_list)):
                    link = InsiderLink(
                        source_wallet=wallet_list[i],
                        target_wallet=wallet_list[j],
                        relationship_type="co_deploy",
                        strength=0.6,
                        evidence=[f"Both originated from deployer {_truncate_address(deployer)}"],
                    )
                    links.append(link)
    return links


def _compute_cluster_risk(cluster: InsiderCluster, links: list[InsiderLink]) -> float:
    """Compute risk score (0-100) for a cluster."""
    if not links or len(cluster.wallets) < 2:
        return 0.0

    risk = 0.0
    n_wallets = len(cluster.wallets)

    # Density: more links = higher coordination risk
    max_possible = n_wallets * (n_wallets - 1) / 2
    density = len(links) / max_possible if max_possible > 0 else 0
    risk += density * 30

    # Relationship type weighting
    type_counts = defaultdict(int)
    for link in links:
        type_counts[link.relationship_type] += 1

    if type_counts.get("co_deploy", 0) >= 2:
        risk += 20
    if type_counts.get("value_transfer", 0) >= 3:
        risk += 15
    if type_counts.get("co_trading", 0) >= 5:
        risk += 15
    if n_wallets >= 10:
        risk += 10
    elif n_wallets >= 5:
        risk += 5

    strong_links = sum(1 for link in links if link.strength >= 0.7)
    if strong_links >= 3:
        risk += 10

    return min(100.0, risk)


def _build_summary(report: InsiderNetworkReport) -> str:
    """Generate a human-readable summary."""
    if not report.clusters:
        return (
            f"No insider networks detected for wallet "
            f"{_truncate_address(report.target_wallet)}. "
            f"This wallet appears to operate independently."
        )

    top_cluster = max(report.clusters, key=lambda c: c.risk_score, default=None)
    parts = [
        f"\U0001f575\ufe0f **Insider Network Report for {_truncate_address(report.target_wallet)}**\n",
        f"\u2022 **Clusters found:** {report.total_clusters}",
        f"\u2022 **Connected wallets:** {report.total_connected_wallets}",
        f"\u2022 **Highest risk score:** {report.highest_risk_score:.0f}/100",
    ]

    if top_cluster and top_cluster.risk_score >= 50:
        parts.append(
            f"\n\u26a0\ufe0f **HIGH RISK** \u2014 Insider network detected! "
            f"{top_cluster.member_count} wallets across "
            f"{len(top_cluster.projects_involved)} projects."
        )

    for i, cluster in enumerate(report.clusters[:3]):
        parts.append(f"\n**Cluster {i + 1}** (Risk: {cluster.risk_score:.0f}/100):")
        parts.append(f"\u2022 {cluster.member_count} wallets")
        if cluster.projects_involved:
            parts.append(f"\u2022 Projects: {', '.join(cluster.projects_involved[:5])}")
        if cluster.risk_factors:
            parts.append(f"\u2022 Risks: {', '.join(cluster.risk_factors[:3])}")

    return "\n".join(parts)


# ── Public API ────────────────────────────────────────────────────


async def analyze_insider_network(
    wallet_address: str,
    chains: list[str] | None = None,
    max_wallets_per_chain: int = 50,
    deep_scan: bool = False,
) -> InsiderNetworkReport:
    """
    Analyze a wallet's insider connections across token projects.

    Args:
        wallet_address: The wallet address to investigate.
        chains: Chains to search (default: all supported).
        max_wallets_per_chain: Max wallet addresses to fetch per chain.
        deep_scan: If True, do deeper analysis (more API calls, slower).

    Returns:
        InsiderNetworkReport with clusters, risk scores, and summary.
    """
    addr = wallet_address.strip()
    if not is_valid_address(addr):
        raise ValueError(f"Invalid wallet address: {addr}")

    if chains is None:
        chains = ["solana", "ethereum", "bsc", "base"]

    report = InsiderNetworkReport(target_wallet=addr)

    async with httpx.AsyncClient(timeout=20) as client:
        wallet_transactions: dict[str, list[dict]] = {}
        token_transfers: dict[str, list[dict]] = {}
        defaultdict(set)

        for chain in chains:
            txs = await _fetch_wallet_transactions(client, addr, chain, limit=50)
            if txs:
                wallet_transactions[addr] = txs

            base = ETHERSCAN_BASES.get(chain)
            if base:
                transfer_params = {
                    "module": "account",
                    "action": "tokentx",
                    "address": addr,
                    "sort": "desc",
                    "limit": 100,
                }
                token_data = await _cached_get(client, base, params=transfer_params)
                if token_data and token_data.get("status") == "1":
                    result = token_data.get("result", [])
                    if isinstance(result, list):
                        token_transfers[addr] = result[:100]

                int_tx_params = {
                    "module": "account",
                    "action": "txlistinternal",
                    "address": addr,
                    "sort": "desc",
                    "limit": 50,
                }
                with suppress(Exception):
                    int_data = await _cached_get(client, base, params=int_tx_params)
                    if int_data and int_data.get("status") == "1":
                        result = int_data.get("result", [])
                        if isinstance(result, list):
                            if addr not in wallet_transactions:
                                wallet_transactions[addr] = []
                            wallet_transactions[addr].extend(result[:50])

        # Step 2: Discover related wallets from transactions
        related_wallets: set[str] = set()
        txs = wallet_transactions.get(addr, [])
        for tx in txs:
            from_addr = tx.get("from", "").lower().strip()
            to_addr = tx.get("to", "").lower().strip()
            if from_addr and from_addr != addr.lower():
                related_wallets.add(from_addr)
            if to_addr and to_addr != addr.lower():
                related_wallets.add(to_addr)

        # Step 3: Also discover wallets from token transfers
        transfers = token_transfers.get(addr, [])
        for tx in transfers:
            fr = tx.get("from", "").lower().strip()
            to = tx.get("to", "").lower().strip()
            if fr and fr != addr.lower() and is_valid_address(fr):
                related_wallets.add(fr)
            if to and to != addr.lower() and is_valid_address(to):
                related_wallets.add(to)

        # Step 4: For each related wallet, fetch their transactions too (deep scan)
        if deep_scan:
            related_list = list(related_wallets)[:max_wallets_per_chain]
            batch_size = 5
            for i in range(0, len(related_list), batch_size):
                batch = related_list[i : i + batch_size]
                tasks = []
                for rw in batch:
                    for chain in chains:
                        tasks.append(_fetch_wallet_transactions(client, rw, chain, limit=20))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                idx = 0
                for rw in batch:
                    for _ in chains:
                        res = results[idx] if idx < len(results) else None
                        if isinstance(res, list) and res:
                            wallet_transactions[rw] = res
                        idx += 1
                    await asyncio.sleep(0.1)

        # Step 5: Detect relationships
        all_wallets = {addr.lower()} | {w.lower() for w in related_wallets}
        wallet_list = list(all_wallets)

        links: list[InsiderLink] = []
        links.extend(_detect_shared_funding(wallet_list, wallet_transactions))
        links.extend(_detect_co_trading(wallet_list, token_transfers))
        links.extend(_detect_value_transfers(wallet_list, wallet_transactions))
        links.extend(_detect_co_deployer(wallet_list, wallet_transactions))

        # Step 6: Cluster wallets based on links (connected components)
        if links:
            adj: dict[str, set[str]] = defaultdict(set)
            for link in links:
                adj[link.source_wallet].add(link.target_wallet)
                adj[link.target_wallet].add(link.source_wallet)

            visited: set[str] = set()
            clusters_raw: list[set[str]] = []

            for w in adj:
                if w in visited:
                    continue
                cluster_set: set[str] = set()
                queue = [w]
                while queue:
                    node = queue.pop(0)
                    if node in visited:
                        continue
                    visited.add(node)
                    cluster_set.add(node)
                    for neighbor in adj.get(node, set()):
                        if neighbor not in visited:
                            queue.append(neighbor)
                if len(cluster_set) >= 2:
                    clusters_raw.append(cluster_set)

            for i, cluster_set in enumerate(clusters_raw):
                cluster_wallets = list(cluster_set)
                cluster_links = [
                    lnk
                    for lnk in links
                    if lnk.source_wallet in cluster_set or lnk.target_wallet in cluster_set
                ]

                projects: set[str] = set()
                for cw in cluster_wallets:
                    cw_transfers = token_transfers.get(cw, [])
                    for tx in cw_transfers[:20]:
                        token = tx.get("contractAddress", "")
                        name = tx.get("tokenName", "") or tx.get("tokenSymbol", "")
                        if name:
                            projects.add(name)
                        elif token:
                            projects.add(_truncate_address(token))

                cluster = InsiderCluster(
                    cluster_id=f"INSIDER-{i + 1:04d}",
                    wallets=cluster_wallets,
                    member_count=len(cluster_wallets),
                    links=cluster_links,
                    projects_involved=list(projects)[:10],
                )

                cluster.risk_score = _compute_cluster_risk(cluster, cluster_links)
                if cluster.risk_score >= 50:
                    cluster.risk_factors.append("High coordination density")
                if any(lnk.relationship_type == "co_deploy" for lnk in cluster_links):
                    cluster.risk_factors.append("Shared deployer origin")
                if any(lnk.relationship_type == "value_transfer" for lnk in cluster_links):
                    cluster.risk_factors.append("Direct value transfers between members")
                if len(cluster_links) > 10:
                    cluster.risk_factors.append("Extensive relationship network")

                sorted_links = sorted(cluster_links, key=lambda x: x.strength, reverse=True)[:5]
                cluster.top_relationships = [
                    {
                        "from": _truncate_address(lnk.source_wallet),
                        "to": _truncate_address(lnk.target_wallet),
                        "type": lnk.relationship_type,
                        "strength": lnk.strength,
                    }
                    for lnk in sorted_links
                ]

                report.clusters.append(cluster)

        # Step 7: Compile report
        report.total_connected_wallets = len(related_wallets)
        report.total_clusters = len(report.clusters)
        if report.clusters:
            report.highest_risk_score = max(c.risk_score for c in report.clusters)

        report.summary = _build_summary(report)

    return report


# ── Formatting ────────────────────────────────────────────────────


def format_insider_network_report(report: InsiderNetworkReport) -> str:
    """Format insider network report as a human-readable string."""
    lines = [
        "=" * 60,
        "INSIDER NETWORK ANALYSIS REPORT",
        "=" * 60,
        f"Target Wallet : {report.target_wallet}",
        f"Label         : {report.target_label or 'Unknown'}",
        f"Connected Wallets : {report.total_connected_wallets}",
        f"Clusters Found    : {report.total_clusters}",
        f"Highest Risk      : {report.highest_risk_score:.0f}/100",
        "",
    ]

    if not report.clusters:
        lines.append("\u2705 No insider networks detected. Wallet appears independent.")
        return "\n".join(lines)

    for r_idx, cluster in enumerate(report.clusters):
        lines.extend(
            [
                f"{'─' * 60}",
                f"Cluster {r_idx + 1}: {cluster.cluster_id}",
                f"{'─' * 60}",
                f"  Members          : {cluster.member_count}",
                f"  Risk Score        : {cluster.risk_score:.0f}/100",
                f"  Link Count        : {len(cluster.links)}",
            ]
        )

        if cluster.projects_involved:
            lines.append(f"  Projects Involved : {', '.join(cluster.projects_involved[:5])}")

        if cluster.risk_factors:
            lines.append("  Risk Factors      :")
            for rf in cluster.risk_factors[:3]:
                lines.append(f"    \u26a0\ufe0f {rf}")

        if cluster.top_relationships:
            lines.append("  Top Relationships :")
            for rel in cluster.top_relationships[:3]:
                lines.append(
                    f"    {rel['from']} \u2194 {rel['to']}  [{rel['type']}, strength={rel['strength']:.1f}]"
                )

        lines.append("")

    lines.append(report.summary)
    return "\n".join(lines)


# ── Singleton Holder ──────────────────────────────────────────────

_insider_network_instance = None


def get_insider_network_analyzer():
    """Get or create the singleton InsiderNetworkAnalyzer."""
    global _insider_network_instance
    if _insider_network_instance is None:
        _insider_network_instance = InsiderNetworkAnalyzer()
    return _insider_network_instance


class InsiderNetworkAnalyzer:
    """Wrapper class for InsiderNetwork analysis (backward-compat)."""

    async def analyze(
        self,
        wallet_address: str,
        chains: list[str] | None = None,
        max_wallets_per_chain: int = 50,
        deep_scan: bool = False,
    ) -> InsiderNetworkReport:
        return await analyze_insider_network(
            wallet_address=wallet_address,
            chains=chains,
            max_wallets_per_chain=max_wallets_per_chain,
            deep_scan=deep_scan,
        )

    @staticmethod
    def format_report(report: InsiderNetworkReport) -> str:
        return format_insider_network_report(report)
