#!/usr/bin/env python3
"""
Public Wallet Label Downloader & Ingester
==========================================
Downloads publicly available wallet labels from multiple free sources,
normalizes them to the internal schema, and inserts into ClickHouse (SSOT)
and Neo4j (entity graph).

Sources:
  1. Etherscan Label API   — contract source code labels (EVM chains)
  2. DefiLlama Protocols   — all protocol contract addresses across chains
  3. CryptoScamDB          — known scam addresses (GitHub blacklist)
  4. Chainabuse            — scam/fraud addresses reported by community
  5. Ethereum-lists        — token/contract lists (GitHub)
  6. Solana token list     — official SPL token registry

Schema (wallet_memory.wallet_labels):
  address, chain_id, label_name, label_category, label_subtype,
  source, is_sanctioned, loaded_at

Neo4j:
  (Wallet {address, chain})-[:LABELED_AS {source, confidence}]->(Label {name, category, source})

Usage:
  python scripts/download_public_labels.py              # full run
  python scripts/download_public_labels.py --dry-run     # no writes
  python scripts/download_public_labels.py --source 1,3  # only sources 1 and 3

Schedule:
  Daily at 06:00 via systemd timer (download-labels.timer)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

try:
    import yaml
except ImportError:  # pragma: no cover — pyyaml ships in the rmi-backend venv
    yaml = None

# ── Add parent dir to path for app imports ────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.logging import get_logger

logger = get_logger("download_public_labels")

# ── Dataclasses ─────────────────────────────────────────────────────────


@dataclass
class LabelEntry:
    """Normalized wallet label entry for insertion."""

    address: str
    chain_id: str
    label_name: str
    label_category: str = ""
    label_subtype: str = ""
    source: str = ""
    is_sanctioned: bool = False
    confidence: float = 1.0  # for Neo4j only; not stored in CH
    loaded_at: str = ""

    def __post_init__(self) -> None:
        self.address = self.address.lower().strip()
        if not self.loaded_at:
            self.loaded_at = datetime.now(UTC).isoformat()

    def ch_tuple(self) -> tuple:
        """Return tuple matching ClickHouse wallet_labels column order."""
        return (
            self.address,
            self.chain_id,
            self.label_name,
            self.label_category,
            self.label_subtype,
            self.source,
            1 if self.is_sanctioned else 0,
            datetime.now(UTC),
        )


# ── Configuration ──────────────────────────────────────────────────────

BATCH_SIZE = 5_000
MAX_CONCURRENT = 10
REQUEST_TIMEOUT = 30

# Normalized chain map
CHAIN_ALIASES = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "bnb_chain": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "base": "base",
    "solana": "solana",
    "sol": "solana",
    "fantom": "fantom",
    "ftm": "fantom",
    "tron": "tron",
}

SOURCE_WEIGHTS = {
    "etherscan_api": 0.85,
    "defillama": 0.90,
    "cryptoscamdb": 0.95,
    "chainabuse": 0.70,
    "ethereum_lists": 0.80,
    "solana_token_list": 0.85,
}


def normalize_chain(raw: str) -> str:
    """Normalize chain name to canonical form."""
    return CHAIN_ALIASES.get(raw.lower(), raw.lower())


# ── HTTP client ────────────────────────────────────────────────────────

_http: httpx.AsyncClient | None = None


async def get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            headers={"User-Agent": "RMI-LabelDownloader/1.0"},
        )
    return _http


async def close_http() -> None:
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()


# ── ClickHouse connection ─────────────────────────────────────────────

_ch_client: Any = None


def get_clickhouse() -> Any | None:
    """Lazy-init ClickHouse client (same pattern as wallet_memory/storage.py)."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client
    try:
        import clickhouse_driver

        ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
        ch_port = int(os.getenv("CLICKHOUSE_PORT", "9000"))
        ch_user = os.getenv("CLICKHOUSE_USER", "default")
        ch_pass = os.getenv("CLICKHOUSE_PASSWORD", "")
        ch_db = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")
        _ch_client = clickhouse_driver.Client(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=10,
            send_receive_timeout=60,
        )
        _ch_client.execute("SELECT 1")
        logger.info("ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except Exception as e:
        logger.warning("ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ── Neo4j connection ──────────────────────────────────────────────────

_neo4j_driver: Any = None


def get_neo4j() -> Any | None:
    """Lazy-init Neo4j Bolt driver (same pattern as neo4j_client.py)."""
    global _neo4j_driver
    if _neo4j_driver is not None:
        return _neo4j_driver
    try:
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=10)
        with driver.session() as s:
            result = s.run("RETURN 1 AS ok").single()
            if result and result["ok"] == 1:
                logger.info("Neo4j connected: %s", uri)
                _neo4j_driver = driver
                return _neo4j_driver
    except ImportError:
        logger.warning("neo4j library not installed — Neo4j writes disabled")
    except Exception as e:
        logger.warning("Neo4j connection failed: %s", e)
    _neo4j_driver = None
    return None


def ensure_neo4j_schema() -> None:
    """Create constraints and indexes for label schema in Neo4j."""
    driver = get_neo4j()
    if not driver:
        return
    queries = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (l:Label) REQUIRE l.label_id IS UNIQUE",
        "CREATE INDEX IF NOT EXISTS FOR (w:Wallet) ON (w.address)",
        "CREATE INDEX IF NOT EXISTS FOR (w:Wallet) ON (w.address, w.chain)",
    ]
    try:

        def _run() -> None:
            with driver.session() as s:
                for q in queries:
                    s.run(q).consume()

        asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as e:
        logger.debug("Neo4j schema ensure failed: %s", e)


# ── SOURCE 1: Etherscan Label API ────────────────────────────────────


async def download_etherscan_labels() -> list[LabelEntry]:
    """
    Fetch labeled addresses from Etherscan API.

    Uses:
      - getsourcecode: To get contract names from verified contracts
      - Token holdings for large-volume addresses

    NOTE: Etherscan free tier is 5 req/s. We respect that with a throttle.
    We iterate through known token contract addresses to get verified labels.
    """
    logger.info("─" * 50)
    logger.info("SOURCE 1: Etherscan Label API")
    logger.info("─" * 50)

    api_key = os.getenv("ETHERSCAN_API_KEY", "")
    if not api_key:
        logger.warning("ETHERSCAN_API_KEY not set — skipping Etherscan API source")
        return []

    labels: list[LabelEntry] = []
    http = await get_http()
    sem = asyncio.Semaphore(4)  # stay under 5 req/s

    # We fetch token contract addresses from the Ethereum-lists source (source 5)
    # and then look up their source code to get verified names.
    # For bulk labels, we query Etherscan's getaddressinfo for known addresses.

    # Approach: Use Etherscan's "accounts" module to get labels for tagged addresses
    # Since there's no bulk label dump API, we query the biggest token holders
    # from the token list we already have.

    # Best approach: fetch known contract addresses from source 5 first,
    # then verify them via Etherscan API.

    # For the initial run, we fetch a curated set of known exchange/defi addresses
    # by querying Etherscan's address info for well-known addresses.

    # These are well-known addresses we want labels for
    known_addresses = [
        # Ethereum 2.0 deposit contract
        ("0x00000000219ab540356cbb839cbe05303d7705fa", "ethereum", "Eth2 Deposit Contract", "infrastructure"),
        # WETH
        ("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "ethereum", "Wrapped Ether (WETH)", "defi"),
        # Tether
        ("0xdac17f958d2ee523a2206206994597c13d831ec7", "ethereum", "Tether USD (USDT)", "token"),
        # USDC
        ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "ethereum", "USD Coin (USDC)", "token"),
        # Uniswap
        ("0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "ethereum", "Uniswap (UNI)", "defi"),
        # Chainlink
        ("0x514910771af9ca656af840dff83e8264ecf986ca", "ethereum", "Chainlink (LINK)", "defi"),
        # Aave
        ("0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9", "ethereum", "Aave (AAVE)", "defi"),
        # Maker
        ("0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2", "ethereum", "Maker (MKR)", "defi"),
        # Curve
        ("0xd533a949740bb3306d119cc777fa900ba034cd52", "ethereum", "Curve DAO (CRV)", "defi"),
        # Lido
        ("0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "ethereum", "Lido Staked ETH (stETH)", "defi"),
    ]

    async def fetch_address_info(addr: str, chain: str, name_hint: str, cat_hint: str) -> list[LabelEntry]:
        """Fetch address info from Etherscan API."""
        results = []
        async with sem:
            try:
                # Use the getaddressinfo endpoint
                resp = await http.get(
                    "https://api.etherscan.io/api",
                    params={
                        "module": "account",
                        "action": "getaddressinfo",
                        "address": addr,
                        "apikey": api_key,
                    },
                )
                if resp.status_code != 200:
                    return results
                data = resp.json()
                if data.get("status") != "1":
                    return results
                result = data.get("result", {})
                tag = result.get("tag", "") or name_hint
                name = result.get("name", "") or name_hint
                if name:
                    results.append(
                        LabelEntry(
                            address=addr,
                            chain_id=chain,
                            label_name=name,
                            label_category=_infer_category(tag or name),
                            source="etherscan_api",
                            confidence=0.85,
                        )
                    )
            except Exception as e:
                logger.debug("Etherscan API error for %s: %s", addr[:12], e)

        # Rate limit: 5 req/s on free tier
        await asyncio.sleep(0.25)  # max 4 req/s to be safe
        return results

    tasks = [fetch_address_info(addr, chain, name, cat) for addr, chain, name, cat in known_addresses]
    task_results = await asyncio.gather(*tasks)
    for result_list in task_results:
        labels.extend(result_list)

    logger.info("Etherscan API: %d labels fetched", len(labels))
    return labels


# ── SOURCE 2: DefiLlama Protocol Addresses ────────────────────────────


async def download_defillama_protocols() -> list[LabelEntry]:
    """
    Fetch all protocol contract addresses from DefiLlama.

    First fetches the protocol list, then fetches per-protocol details
    to get contract addresses across all chains.

    API docs: https://defillama.com/docs/api
    - GET /protocols → list of all protocols
    - GET /protocol/{slug} → protocol details with addresses
    """
    logger.info("─" * 50)
    logger.info("SOURCE 2: DefiLlama Protocol Addresses")
    logger.info("─" * 50)

    labels: list[LabelEntry] = []
    http = await get_http()

    # Step 1: Get all protocols
    try:
        resp = await http.get("https://api.llama.fi/protocols", timeout=60)
        if resp.status_code != 200:
            logger.warning("DefiLlama protocols API returned %s", resp.status_code)
            return labels
        protocols = resp.json()
        logger.info("DefiLlama: %d protocols found", len(protocols))
    except Exception as e:
        logger.warning("DefiLlama protocol list fetch failed: %s", e)
        return labels

    if not isinstance(protocols, list):
        logger.warning("DefiLlama returned non-list response")
        return labels

    # Step 2: Fetch per-protocol details for contract addresses
    # We limit to top 200 by TVL to avoid excessive API calls
    protocols_sorted = sorted(protocols, key=lambda p: p.get("tvl", 0) or 0, reverse=True)
    top_protocols = protocols_sorted[:200]

    sem = asyncio.Semaphore(5)  # stay polite

    async def fetch_protocol(slug: str, name: str) -> list[LabelEntry]:
        """Fetch single protocol details."""
        results = []
        async with sem:
            try:
                resp = await http.get(f"https://api.llama.fi/protocol/{slug}", timeout=30)
                if resp.status_code != 200:
                    return results
                data = resp.json()
                # Extract chain addresses from currentChainTvls and other fields
                addresses = data.get("addresses", data.get("tokens", {}))
                chain_tvls = data.get("currentChainTvls", {})

                # If addresses is a dict keyed by chain
                if isinstance(addresses, dict):
                    for chain, addrs in addresses.items():
                        if isinstance(addrs, list):
                            for addr in addrs:
                                if addr and len(addr) >= 10:
                                    results.append(
                                        LabelEntry(
                                            address=addr,
                                            chain_id=normalize_chain(chain),
                                            label_name=name,
                                            label_category="defi",
                                            label_subtype=data.get("category", "protocol"),
                                            source="defillama",
                                            confidence=0.90,
                                        )
                                    )
                elif isinstance(addresses, list):
                    for addr in addresses:
                        if addr and len(addr) >= 10:
                            results.append(
                                LabelEntry(
                                    address=addr,
                                    chain_id="ethereum",  # default if not specified
                                    label_name=name,
                                    label_category="defi",
                                    label_subtype=data.get("category", "protocol"),
                                    source="defillama",
                                    confidence=0.90,
                                )
                            )

                # Also extract from chainTvls if it has address info
                if isinstance(chain_tvls, dict):
                    for chain in chain_tvls:
                        chain_addrs = data.get("addresses", {}).get(chain, [])
                        if isinstance(chain_addrs, list):
                            for addr in chain_addrs:
                                if addr and len(addr) >= 10 and not any(r.address == addr.lower() for r in results):
                                    results.append(
                                        LabelEntry(
                                            address=addr,
                                            chain_id=normalize_chain(chain),
                                            label_name=name,
                                            label_category="defi",
                                            source="defillama",
                                            confidence=0.90,
                                        )
                                    )

            except Exception as e:
                logger.debug("DefiLlama protocol %s fetch error: %s", slug, e)

        await asyncio.sleep(0.1)  # be nice to the API
        return results

    tasks = [fetch_protocol(p.get("slug", ""), p.get("name", "")) for p in top_protocols if p.get("slug")]

    # Run in batches to avoid overwhelming the API
    batch_size = 20
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i : i + batch_size]
        batch_results = await asyncio.gather(*batch)
        for result_list in batch_results:
            labels.extend(result_list)
        logger.info(
            "DefiLlama progress: %d/%d protocols → %d labels so far",
            min(i + batch_size, len(tasks)),
            len(tasks),
            len(labels),
        )
        await asyncio.sleep(0.5)  # between batches

    logger.info("DefiLlama: %d total labels from %d protocols", len(labels), len(top_protocols))
    return labels


# ── SOURCE 3: CryptoScamDB ───────────────────────────────────────────


async def download_cryptoscamdb() -> list[LabelEntry]:
    """
    Fetch known scam addresses from CryptoScamDB GitHub blacklist.

    Primary source: GitHub raw YAML:
      https://raw.githubusercontent.com/CryptoScamDB/blacklist/master/data/urls.yaml
      (data/blacklist.json never existed upstream — old URL 404'd)

    Fallback: CryptoScamDB API (often 502)
    """
    logger.info("─" * 50)
    logger.info("SOURCE 3: CryptoScamDB")
    logger.info("─" * 50)

    labels: list[LabelEntry] = []
    http = await get_http()

    # Primary: GitHub blacklist YAML (data/urls.yaml on master). The old
    # data/blacklist.json path never existed upstream (404) and the public API
    # is intermittently 502 — this YAML is the maintained canonical dataset.
    github_url = "https://raw.githubusercontent.com/CryptoScamDB/blacklist/master/data/urls.yaml"

    try:
        resp = await http.get(github_url, timeout=60)
        if resp.status_code == 200:
            if yaml is None:
                logger.error("pyyaml not installed — cannot parse CryptoScamDB YAML")
            else:
                data = yaml.safe_load(resp.text)
                if isinstance(data, list):
                    logger.info("CryptoScamDB: %d entries from GitHub urls.yaml", len(data))
                    for entry in data:
                        if not isinstance(entry, dict):
                            continue
                        name = entry.get("name", "CryptoScamDB Flagged")
                        category = entry.get("category", "scam")
                        description = entry.get("description", "")
                        # urls.yaml shape: {chain: [addr, ...]}
                        addresses = entry.get("addresses", {})
                        if not isinstance(addresses, dict):
                            continue
                        for chain, chain_addrs in addresses.items():
                            if not isinstance(chain_addrs, list):
                                continue
                            for address in chain_addrs:
                                if not address or len(address) < 10:
                                    continue
                                label_name = name
                                if description:
                                    label_name = f"{name} — {description[:100]}"
                                labels.append(
                                    LabelEntry(
                                        address=address,
                                        chain_id=normalize_chain(chain),
                                        label_name=label_name,
                                        label_category="scam",
                                        label_subtype=category,
                                        source="cryptoscamdb",
                                        is_sanctioned=False,
                                        confidence=0.95,
                                    )
                                )
                else:
                    logger.warning("CryptoScamDB GitHub: expected list, got %s", type(data).__name__)
        else:
            logger.warning("CryptoScamDB GitHub returned %s", resp.status_code)
    except Exception as e:
        logger.warning("CryptoScamDB GitHub fetch failed: %s", e)

    # Fallback: Try API
    if not labels:
        api_urls = [
            "https://api.cryptoscamdb.org/v1/all",
            "https://api.cryptoscamdb.org/v1/scams",
        ]
        for url in api_urls:
            try:
                resp = await http.get(url, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    scams = data.get("result", data.get("scams", data.get("data", [])))
                    if isinstance(scams, list):
                        logger.info("CryptoScamDB API: %d entries", len(scams))
                        for scam in scams:
                            if not isinstance(scam, dict):
                                continue
                            addresses = scam.get("addresses", [])
                            name = scam.get("name", "Scam")
                            category = scam.get("category", "scam")
                            for addr in addresses:
                                if addr and len(addr) >= 10:
                                    labels.append(
                                        LabelEntry(
                                            address=addr,
                                            chain_id="ethereum",
                                            label_name=name,
                                            label_category="scam",
                                            label_subtype=category,
                                            source="cryptoscamdb",
                                            confidence=0.95,
                                        )
                                    )
                        break
            except Exception as e:
                logger.debug("CryptoScamDB API %s failed: %s", url, e)

    logger.info("CryptoScamDB: %d labels fetched", len(labels))
    return labels


# ── SOURCE 4: Chainabuse ─────────────────────────────────────────────


async def download_chainabuse() -> list[LabelEntry]:
    """
    Fetch scam/fraud addresses from Chainabuse public reports.

    API: https://api.chainabuse.com/v1/reports (public, no auth needed for basic access)
    """
    logger.info("─" * 50)
    logger.info("SOURCE 4: Chainabuse")
    logger.info("─" * 50)

    labels: list[LabelEntry] = []
    http = await get_http()

    urls = [
        "https://api.chainabuse.com/v1/reports?limit=200",
        "https://api.chainabuse.com/v0/reports?limit=200",
        "https://chainabuse.com/api/reports?limit=200",
    ]

    for url in urls:
        try:
            resp = await http.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                reports = data if isinstance(data, list) else data.get("data", data.get("reports", []))
                if isinstance(reports, list) and len(reports) > 0:
                    logger.info("Chainabuse: %d reports from %s", len(reports), url)
                    for report in reports:
                        if not isinstance(report, dict):
                            continue
                        address = report.get("address", report.get("targetAddress", ""))
                        category = report.get("category", report.get("type", "scam"))
                        description = report.get("description", report.get("report", ""))
                        if not address or len(address) < 10:
                            continue

                        label_name = f"Chainabuse Reported ({category})"
                        if description:
                            label_name = f"Chainabuse: {description[:80]}"

                        labels.append(
                            LabelEntry(
                                address=address,
                                chain_id="ethereum",
                                label_name=label_name,
                                label_category="scam",
                                label_subtype=category,
                                source="chainabuse",
                                confidence=0.70,
                            )
                        )
                    break  # stop after first successful source
        except Exception as e:
            logger.debug("Chainabuse %s failed: %s", url, e)

    if not labels:
        logger.warning("Chainabuse: No accessible API endpoint found")

    logger.info("Chainabuse: %d labels fetched", len(labels))
    return labels


# ── SOURCE 5: Ethereum-lists ─────────────────────────────────────────


async def download_ethereum_lists() -> list[LabelEntry]:
    """
    Fetch token/contract lists from ethereum-lists GitHub.

    Sources:
      - https://raw.githubusercontent.com/ethereum-lists/tokens/master/tokens/eth/tokens.json
      - https://raw.githubusercontent.com/ethereum-lists/contracts/master/contracts/eth.json
    """
    logger.info("─" * 50)
    logger.info("SOURCE 5: Ethereum-lists (GitHub)")
    logger.info("─" * 50)

    labels: list[LabelEntry] = []
    http = await get_http()

    sources = [
        {
            "url": "https://raw.githubusercontent.com/ethereum-lists/tokens/master/tokens/eth/tokens.json",
            "category": "token",
            "chain": "ethereum",
        },
        {
            "url": "https://raw.githubusercontent.com/ethereum-lists/contracts/master/contracts/eth.json",
            "category": "contract",
            "chain": "ethereum",
        },
    ]

    for src in sources:
        try:
            resp = await http.get(src["url"], timeout=60)
            if resp.status_code != 200:
                logger.warning("Ethereum-lists %s returned %s", src["url"], resp.status_code)
                continue

            data = resp.json()
            if isinstance(data, list):
                logger.info("Ethereum-lists: %d entries from %s", len(data), src["url"].split("/")[-1])
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    address = entry.get("address", "")
                    name = entry.get("name", entry.get("label", ""))
                    symbol = entry.get("symbol", "")
                    decimals = entry.get("decimals")

                    if not address or len(address) < 10:
                        continue

                    label_name = name or f"{symbol} Token" if symbol else "Verified Contract"
                    if symbol and name:
                        label_name = f"{name} ({symbol})"
                    elif symbol:
                        label_name = symbol

                    labels.append(
                        LabelEntry(
                            address=address,
                            chain_id=src["chain"],
                            label_name=label_name,
                            label_category=src["category"],
                            label_subtype=f"decimals={decimals}" if decimals else "",
                            source="ethereum_lists",
                            confidence=0.80,
                        )
                    )
            elif isinstance(data, dict):
                # Some ethereum-lists files are maps of address → info
                logger.info("Ethereum-lists: %d entries (dict) from %s", len(data), src["url"].split("/")[-1])
                for address, entry in data.items():
                    if not address or len(address) < 10:
                        continue
                    if isinstance(entry, dict):
                        name = entry.get("name", entry.get("label", ""))
                        label_name = name or "Verified Contract"
                        labels.append(
                            LabelEntry(
                                address=address,
                                chain_id=src["chain"],
                                label_name=label_name,
                                label_category=src["category"],
                                source="ethereum_lists",
                                confidence=0.80,
                            )
                        )
        except Exception as e:
            logger.warning("Ethereum-lists %s failed: %s", src["url"], e)

    logger.info("Ethereum-lists: %d labels fetched", len(labels))
    return labels


# ── SOURCE 6: Solana token list ──────────────────────────────────────


async def download_solana_token_list() -> list[LabelEntry]:
    """
    Fetch the official Solana SPL token registry.

    Source: https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json
    """
    logger.info("─" * 50)
    logger.info("SOURCE 6: Solana Token List")
    logger.info("─" * 50)

    labels: list[LabelEntry] = []
    http = await get_http()

    url = "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"

    try:
        resp = await http.get(url, timeout=60)
        if resp.status_code != 200:
            logger.warning("Solana token list returned %s", resp.status_code)
            return labels

        data = resp.json()
        tokens = data.get("tokens", [])
        logger.info("Solana token list: %d tokens", len(tokens))

        for token in tokens:
            if not isinstance(token, dict):
                continue
            address = token.get("address", "")
            name = token.get("name", "")
            symbol = token.get("symbol", "")
            chain_id = token.get("chainId", 101)

            if not address or len(address) < 10:
                continue

            # Map chain ID to chain name
            chain = "solana"
            if chain_id == 101:
                chain = "solana"
            elif chain_id == 102:
                chain = "solana_testnet"
            elif chain_id == 103:
                chain = "solana_devnet"
            elif chain_id == 1:
                chain = "ethereum"
            else:
                chain = f"chain_{chain_id}"

            label_name = f"{name} ({symbol})" if symbol else name
            if not label_name:
                continue

            labels.append(
                LabelEntry(
                    address=address,
                    chain_id=chain,
                    label_name=label_name,
                    label_category="token",
                    label_subtype="spl",
                    source="solana_token_list",
                    confidence=0.85,
                )
            )

    except Exception as e:
        logger.warning("Solana token list fetch failed: %s", e)

    logger.info("Solana token list: %d labels fetched", len(labels))
    return labels


# ── Deduplication ─────────────────────────────────────────────────────


def deduplicate_labels(labels: list[LabelEntry]) -> list[LabelEntry]:
    """
    Deduplicate labels: same address+chain_id+label_category → keep highest confidence.

    The ClickHouse ReplacingMergeTree handles physical dedup on ORDER BY
    (address, chain_id, source), but we do logical dedup before insert to
    reduce write volume and pick the best label per category.
    """
    # Group by (address, chain_id, label_category)
    groups: dict[tuple[str, str, str], list[LabelEntry]] = defaultdict(list)
    for lbl in labels:
        key = (lbl.address, lbl.chain_id, lbl.label_category)
        groups[key].append(lbl)

    deduped: list[LabelEntry] = []
    for key, group in groups.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            # Keep the one with highest confidence, then longest label_name
            best = max(group, key=lambda x: (x.confidence, len(x.label_name)))
            deduped.append(best)

    logger.info(
        "Dedup: %d → %d labels (%.1f%% reduction)",
        len(labels),
        len(deduped),
        (1 - len(deduped) / len(labels)) * 100 if labels else 0,
    )
    return deduped


# ── ClickHouse insertion ─────────────────────────────────────────────


def insert_clickhouse(labels: list[LabelEntry]) -> int:
    """Batch insert labels into ClickHouse wallet_memory.wallet_labels."""
    ch = get_clickhouse()
    if not ch:
        logger.info("labels_sync: ClickHouse unavailable - falling back to stdout")
        # Print first 5 as JSON for visibility
        for lbl in labels[:5]:
            logger.info("  WOULD INSERT: %s/%s = %s (%s)", lbl.chain_id, lbl.address[:16], lbl.label_name, lbl.source)
        return 0

    # Ensure the wallet_labels table exists
    try:
        ch.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_memory.wallet_labels (
                address         String,
                chain_id        LowCardinality(String),
                label_name      String DEFAULT '',
                label_category  LowCardinality(String) DEFAULT '',
                label_subtype   String DEFAULT '',
                source          LowCardinality(String) DEFAULT '',
                is_sanctioned   UInt8 DEFAULT 0,
                loaded_at       DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(loaded_at)
            ORDER BY (address, chain_id, source)
            """
        )
    except Exception as e:
        logger.warning("Failed to ensure wallet_labels table: %s", e)
        return 0

    # Get existing sources to skip already-imported data
    existing_sources: set[str] = set()
    try:
        rows = ch.execute("SELECT DISTINCT source FROM wallet_memory.wallet_labels")
        existing_sources = {row[0] for row in rows}
        logger.info("Existing sources in ClickHouse: %s", existing_sources)
    except Exception as e:
        logger.debug("Could not query existing sources: %s", e)

    # Filter out labels from already-processed sources
    new_labels = [lbl for lbl in labels if lbl.source not in existing_sources]
    skipped = len(labels) - len(new_labels)
    if skipped:
        logger.info("Skipping %d labels from already-imported sources", skipped)

    if not new_labels:
        logger.info("No new labels to insert")
        return 0

    # Insert in batches
    total = 0
    for i in range(0, len(new_labels), BATCH_SIZE):
        batch = new_labels[i : i + BATCH_SIZE]
        ch_rows = [lbl.ch_tuple() for lbl in batch]
        try:
            ch.execute(
                """INSERT INTO wallet_memory.wallet_labels
                (address, chain_id, label_name, label_category, label_subtype,
                 source, is_sanctioned, loaded_at) VALUES""",
                ch_rows,
            )
            total += len(ch_rows)
        except Exception as e:
            logger.error("ClickHouse batch insert failed at row %d: %s", i, e)

        if (i + BATCH_SIZE) % (BATCH_SIZE * 5) == 0 or (i + BATCH_SIZE) >= len(new_labels):
            logger.info("ClickHouse insert: %d/%d labels", total, len(new_labels))

    logger.info("ClickHouse insert complete: %d labels inserted", total)
    return total


# ── Neo4j insertion ──────────────────────────────────────────────────


async def insert_neo4j(labels: list[LabelEntry]) -> int:
    """Insert labels into Neo4j graph database (async wrapper around sync driver)."""
    driver = get_neo4j()
    if not driver:
        logger.info("Neo4j unavailable — skipping graph insert")
        return 0

    ensure_neo4j_schema()
    inserted = 0
    loop = asyncio.get_running_loop()

    def _batch_insert(batch: list[LabelEntry]) -> int:
        """Insert a batch of labels into Neo4j (runs in executor)."""
        count = 0
        with driver.session() as s:
            for lbl in batch:
                try:
                    # Merge Wallet node
                    s.run(
                        """
                        MERGE (w:Wallet {address: $address})
                        ON CREATE SET w.chain = $chain, w.first_seen = datetime()
                        ON MATCH SET w.last_seen = datetime()
                        SET w.chains = CASE
                            WHEN NOT $chain IN COALESCE(w.chains, []) THEN
                                COALESCE(w.chains, []) + [$chain]
                            ELSE w.chains
                        END
                        """,
                        address=lbl.address,
                        chain=lbl.chain_id,
                    )

                    # Generate a stable label_id (truncate to avoid key length issues)
                    label_id = f"{lbl.source}:{lbl.label_category}:{lbl.label_name[:60]}"

                    # Merge Label node
                    s.run(
                        """
                        MERGE (l:Label {label_id: $label_id})
                        ON CREATE SET l.name = $name,
                                      l.category = $category,
                                      l.source = $source
                        ON MATCH SET l.name = CASE WHEN l.name = '' THEN $name ELSE l.name END
                        """,
                        label_id=label_id,
                        name=lbl.label_name,
                        category=lbl.label_category,
                        source=lbl.source,
                    )

                    # Create LABELED_AS relationship
                    s.run(
                        """
                        MATCH (w:Wallet {address: $address})
                        MATCH (l:Label {label_id: $label_id})
                        MERGE (w)-[r:LABELED_AS]->(l)
                        SET r.source = $source,
                            r.confidence = $confidence,
                            r.is_sanctioned = $sanctioned,
                            r.updated_at = datetime()
                        """,
                        address=lbl.address,
                        label_id=label_id,
                        source=lbl.source,
                        confidence=lbl.confidence,
                        sanctioned=lbl.is_sanctioned,
                    )
                    count += 1
                except Exception as e:
                    logger.debug("Neo4j label insert failed for %s: %s", lbl.address[:12], e)
        return count

    try:
        for i in range(0, len(labels), BATCH_SIZE):
            batch = labels[i : i + BATCH_SIZE]
            _inserted = await loop.run_in_executor(None, _batch_insert, batch)
            inserted += _inserted

            if (i + BATCH_SIZE) % (BATCH_SIZE * 10) == 0 or (i + BATCH_SIZE) >= len(labels):
                logger.info("Neo4j insert: %d/%d labels", inserted, len(labels))

    except Exception as e:
        logger.warning("Neo4j batch insert error: %s", e)

    logger.info("Neo4j insert complete: %d labels", inserted)
    return inserted


# ── Source registry ──────────────────────────────────────────────────

SOURCES = {
    1: ("Etherscan API", download_etherscan_labels),
    2: ("DefiLlama Protocols", download_defillama_protocols),
    3: ("CryptoScamDB", download_cryptoscamdb),
    4: ("Chainabuse", download_chainabuse),
    5: ("Ethereum-lists", download_ethereum_lists),
    6: ("Solana Token List", download_solana_token_list),
}


# ── Main ─────────────────────────────────────────────────────────────


async def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Download and ingest public wallet labels")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and normalize but do NOT write to databases",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="Comma-separated source IDs to run (e.g., '1,3,6'). Default: all",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Skip deduplication step (faster, but more writes)",
    )
    args = parser.parse_args()

    # Determine which sources to run
    if args.source:
        source_ids = [int(s.strip()) for s in args.source.split(",") if s.strip()]
        active_sources = {sid: SOURCES[sid] for sid in source_ids if sid in SOURCES}
    else:
        active_sources = SOURCES

    logger.info("=" * 60)
    logger.info("PUBLIC WALLET LABEL DOWNLOADER")
    logger.info("=" * 60)
    logger.info("Dry run: %s", args.dry_run)
    logger.info("Sources: %s", ", ".join(f"{sid}: {name}" for sid, (name, _) in active_sources.items()))
    logger.info("=" * 60)

    t0 = time.time()
    all_labels: list[LabelEntry] = []

    # Step 1: Download from all selected sources in parallel
    download_tasks = {}
    for sid, (name, fn) in active_sources.items():
        logger.info("\nStarting source %d: %s...", sid, name)
        download_tasks[sid] = fn()

    download_results = await asyncio.gather(*download_tasks.values())
    for sid, labels in zip(active_sources.keys(), download_results, strict=False):
        logger.info("Source %d (%s): %d labels", sid, active_sources[sid][0], len(labels))
        all_labels.extend(labels)

    # Step 2: Deduplicate
    if not args.no_dedup and all_labels:
        all_labels = deduplicate_labels(all_labels)

    logger.info("\n" + "=" * 60)
    logger.info("TOTAL: %d labels across %d sources", len(all_labels), len(active_sources))
    logger.info("=" * 60)

    # Show breakdown by source
    source_counts: dict[str, int] = defaultdict(int)
    category_counts: dict[str, int] = defaultdict(int)
    chain_counts: dict[str, int] = defaultdict(int)
    for lbl in all_labels:
        source_counts[lbl.source] += 1
        category_counts[lbl.label_category] += 1
        chain_counts[lbl.chain_id] += 1

    for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d labels", src, count)

    logger.info("  Categories: %s", dict(sorted(category_counts.items(), key=lambda x: -x[1])))
    logger.info("  Chains: %s", dict(sorted(chain_counts.items(), key=lambda x: -x[1])))

    if args.dry_run:
        logger.info("\n DRY RUN — no data written. Pass --dry-run to skip writes.")
        elapsed = time.time() - t0
        logger.info("Total time: %.1fs", elapsed)
        return {
            "dry_run": True,
            "total_labels": len(all_labels),
            "sources": dict(active_sources),
            "source_counts": dict(source_counts),
            "elapsed_seconds": round(elapsed, 1),
        }

    # Step 3: Insert into ClickHouse
    logger.info("\n" + "=" * 60)
    logger.info("INSERTING INTO CLICKHOUSE (SSOT)")
    logger.info("=" * 60)
    ch_count = insert_clickhouse(all_labels)

    # Step 4: Insert into Neo4j
    logger.info("\n" + "=" * 60)
    logger.info("INSERTING INTO NEO4J (ENTITY GRAPH)")
    logger.info("=" * 60)
    n4j_count = await insert_neo4j(all_labels)

    elapsed = time.time() - t0
    logger.info("\n" + "=" * 60)
    logger.info("COMPLETE")
    logger.info("=" * 60)
    logger.info("  Elapsed: %.1fs", elapsed)
    logger.info("  Sources: %d", len(active_sources))
    logger.info("  Labels downloaded: %d", len(all_labels))
    logger.info("  Inserted into ClickHouse: %d", ch_count)
    logger.info("  Inserted into Neo4j: %d", n4j_count)
    logger.info("=" * 60)

    return {
        "dry_run": False,
        "total_downloaded": len(all_labels),
        "clickhouse_inserted": ch_count,
        "neo4j_inserted": n4j_count,
        "source_counts": dict(source_counts),
        "category_counts": dict(category_counts),
        "chain_counts": dict(chain_counts),
        "elapsed_seconds": round(elapsed, 1),
    }


# ── Category inference ───────────────────────────────────────────────


def _infer_category(text: str) -> str:
    """Infer label category from text (same as wallet_memory/labels.py)."""
    if not text:
        return "unknown"
    t = text.lower()
    if any(w in t for w in ("exchange", "binance", "coinbase", "kraken", "okx", "bitfinex")):
        return "exchange"
    if any(w in t for w in ("mev", "bot", "flashbot")):
        return "mev"
    if any(w in t for w in ("phish", "scam", "hack", "exploit", "rug", "drainer", "fraud")):
        return "scam"
    if any(w in t for w in ("defi", "aave", "uniswap", "curve", "compound", "lido")):
        return "defi"
    if any(w in t for w in ("bridge", "hop", "across", "synapse", "stargate")):
        return "bridge"
    if any(w in t for w in ("sanction", "ofac")):
        return "sanctioned"
    if any(w in t for w in ("token", "coin")):
        return "token"
    return "wallet"


if __name__ == "__main__":
    result = asyncio.run(main())
    # Print JSON summary to stdout for systemd journal
    # Convert non-serializable items
    serializable = {}
    for k, v in result.items():
        if isinstance(v, dict):
            serializable[k] = {str(ks): vs for ks, vs in v.items()}
        else:
            serializable[k] = v
    print(json.dumps(serializable, indent=2, default=str))
