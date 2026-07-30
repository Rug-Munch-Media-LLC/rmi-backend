"""
Cross-Chain Trace — Fund Flow Tracer via Chain-Indexer ClickHouse
==================================================================
Wires the chain-indexer's 73M EVM transfer records into the scanner
pipeline for wallet-level fund flow tracing across chains.

Architecture:
  1. Accept a wallet address + source chain
  2. Query evm_transfers for ALL transfers involving that address
  3. Detect bridge contract interactions → follow funds across chains
  4. Flag known entities (exchanges, mixers, scams) from wallet_labels
  5. Return a directed fund flow graph with nodes + edges + entity annotations

All data is LOCAL — we OWN the ClickHouse tables. Zero external API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger("cross_chain_trace")


# ═══════════════════════════════════════════════════════════════════
# KNOWN BRIDGE CONTRACTS — cross-chain fund flow detection
# ═══════════════════════════════════════════════════════════════════
# These are well-known bridge/router contracts. When a traced address
# interacts with one, we flag it as a cross-chain hop.
# Format: { chain: [list_of_contract_addresses] }

_BRIDGE_CONTRACTS: dict[str, set[str]] = {
    "ethereum": {
        # LayerZero
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: ETH Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        # Stargate
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router (V2)
        # Wormhole
        "0x3ee18b2214aff97000d974cf647e7c347e8fa585",  # Wormhole: Bridge
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        # Across
        "0x5c7bcd1f0b4e5c2bc6b3c0fc2cd31332c21e9afc",  # Across: Spoke Pool
        # Hop
        "0x3666f603a164d1e1b1f6b8e8e9e6b3f6a7d8e9f6",  # Hop: L1 Bridge
        # Synapse
        "0x2796317b0ff8538f253012862c06787adfb8ceb6",  # Synapse: Bridge
        # Celer cBridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
        # Multichain (anySwap)
        "0x6b7a87899490ece95443e979ca9485cbe7e71522",  # Multichain: Router V6
        # Connext / Amarok
        "0xee9dec2712cce65174b561151701bf54b99c24c8",  # Connext: bridge
        # Rainbow Bridge (NEAR)
        "0x23ddd3e3692d1861ed57ede224608875809e127f",  # Rainbow Bridge
    },
    "bsc": {
        "0x3a6d8ca21d1cf76f653a67577fa0d27453350dd8",  # Binance Bridge (BSC)
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router V2
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        "0x2796317b0ff8538f253012862c06787adfb8ceb6",  # Synapse: Bridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
    },
    "polygon": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router V2
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
        # Polygon native bridge (PoS)
        "0x40ec5b33f54e0e8a33a975908c5ba1c14e5bbbdf",  # Polygon: ERC20 Predicate
        "0x7ffb3d3a245dc8f1552b2a5b0c1b7fe62f6af7b6",  # Polygon: Plasma Bridge
    },
    "arbitrum": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router V2
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
        # Arbitrum native bridge
        "0x8315177ab297ba92a06054ce80a1b2f14d5b0a46",  # Arbitrum: L1 Gateway Router
        "0x4dbd4fc535ac27206064b68ffcf827b0a60bab3f",  # Arbitrum: L2 Gateway Router
    },
    "base": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router V2
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
        # Base native bridge
        "0x49048044d57e1c92a77f79988d21fa8faf74e97e",  # Base: L1 CrossDomainMessenger
    },
    "optimism": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router V2
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
        # Optimism native bridge
        "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1",  # Optimism: L1 Gateway
    },
    "avalanche": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",  # LayerZero: Endpoint
        "0x1a44076050125825900e736c501f859c50fe728c",  # LayerZero: UltraLightNodeV2
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",  # Stargate: Router
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",  # Stargate: Router V2
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",  # Wormhole: Token Bridge
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",  # cBridge
    },
}


# ═══════════════════════════════════════════════════════════════════
# ENTITY LABEL PRIORITY
# ═══════════════════════════════════════════════════════════════════
# When multiple labels exist for an address, pick the highest priority.
_LABEL_PRIORITY: dict[str, int] = {
    "scam": 100,
    "exploiter": 100,
    "malicious": 100,
    "ponzi": 100,
    "phishing_scam": 100,
    "ransomware": 100,
    "mixer": 90,
    "sanctioned": 90,
    "exchange": 80,
    "bridge": 75,
    "defi": 70,
    "dex": 70,
    "money_laundering": 100,
    "darknet_market": 100,
    "high_risk": 85,
    "mev_bot": 60,
    "lending": 60,
    "gambling": 50,
    "p2p_service": 50,
}

# Chains that share EVM address space (same private key works)
_EVM_CHAINS: set[str] = {
    "ethereum",
    "eth",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "base",
    "avalanche",
    "fantom",
    "gnosis",
}

_NATIVE_TOKEN_SYMBOLS: dict[str, str] = {
    "ethereum": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "arbitrum": "ETH",
    "optimism": "ETH",
    "base": "ETH",
    "avalanche": "AVAX",
    "fantom": "FTM",
    "gnosis": "xDAI",
}

# Chain ID mapping for wallet_labels (uses chain_id column)
_CHAIN_ID_MAP: dict[str, str] = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "gnosis": "gnosis",
}


# ═══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════


@dataclass
class CrossChainNode:
    """A wallet address discovered in the fund flow trace."""

    address: str
    chain: str
    label: str | None  # entity label from wallet_labels
    label_category: str | None  # exchange, mixer, scam, etc.
    role: str  # origin, intermediate, bridge, exchange, mixer, scam, destination
    total_received: float = 0.0
    total_sent: float = 0.0
    tx_count: int = 0
    is_sanctioned: bool = False


@dataclass
class CrossChainEdge:
    """A token transfer between two addresses."""

    from_address: str
    to_address: str
    chain: str
    token_address: str
    token_symbol: str | None
    value: str  # raw string value from ClickHouse
    tx_hash: str
    block_number: int
    block_timestamp: str
    is_bridge: bool = False
    bridge_destination_chain: str | None = None


@dataclass
class CrossChainTraceReport:
    """
    Complete cross-chain fund flow trace for a wallet address.

    Used as the module output — compatible with SentinelReport field storage.
    """

    seed_address: str
    seed_chain: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    bridge_hops: list[dict[str, Any]] = field(default_factory=list)
    entity_flags: list[str] = field(default_factory=list)
    known_entities: dict[str, str] = field(default_factory=dict)  # address -> label
    total_volume_received: float = 0.0
    total_volume_sent: float = 0.0
    chains_involved: list[str] = field(default_factory=list)
    unique_counterparties: int = 0
    risk_score: float = 0.0
    risk_level: str = "low"
    error: str | None = None


# ═══════════════════════════════════════════════════════════════════
# CLICKHOUSE HELPERS (shares ClickHouse client from chain_indexer)
# ═══════════════════════════════════════════════════════════════════


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client — shares config with chain_indexer.

    Uses CH_HOST/CH_PORT/CH_USER/CH_PASSWORD env vars with sensible defaults
    for the local Docker environment. Note: CLICKHOUSE_HOST/CLICKHOUSE_PORT
    are typically HTTP-oriented (port 8123) so we prefer the CH_* namespace.
    """
    import os

    # Prefer CH_* namespace for native protocol; fall back to CLICKHOUSE_*
    ch_host = os.getenv("CH_HOST") or os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
    ch_port = int(os.getenv("CH_PORT") or os.getenv("CLICKHOUSE_PORT", "9000"))
    ch_user = os.getenv("CH_USER") or os.getenv("CLICKHOUSE_USER", "rmi")
    ch_pass = os.getenv("CH_PASSWORD") or os.getenv("CLICKHOUSE_PASSWORD", "clickhouse_rmi_prod_2026")
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass or None,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        client.execute("SELECT 1")
        return client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError, AttributeError) as e:
        logger.warning("ClickHouse unavailable for cross_chain_trace: %s", e)
        return None


async def _run_query(fn):
    """Run a sync ClickHouse query in a thread pool."""
    import asyncio

    return await asyncio.to_thread(fn)


# ═══════════════════════════════════════════════════════════════════
# QUERY: EVM transfers for an address
# ═══════════════════════════════════════════════════════════════════


def _build_transfer_query(
    address: str,
    chain_filter: str | None = None,
    limit: int = 500,
) -> str:
    """Build SQL to find ALL transfers TO or FROM a given address.

    Chain filter is optional — when omitted, queries ALL chains.
    This is the core cross-chain capability.
    """
    params: dict[str, Any] = {
        "addr": address.lower(),
        "limit": limit,
    }

    where_parts = [
        "(from_address = %(addr)s OR to_address = %(addr)s)",
    ]

    if chain_filter:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain_filter

    where_clause = " AND ".join(where_parts)

    return (
        f"""
        SELECT chain, block_number, tx_hash, from_address, to_address,
               token_address, value, block_timestamp, log_index,
               token_symbol, token_name
        FROM wallet_memory.evm_transfers
        WHERE {where_clause}
        ORDER BY block_timestamp DESC, block_number DESC
        LIMIT %(limit)s
        """,
        params,
    )


async def _fetch_transfers(
    address: str,
    chain: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Fetch all EVM transfers involving the given address."""
    client = _get_ch_client()
    if client is None:
        return []

    sql, params = _build_transfer_query(address, chain, limit)

    def _query():
        try:
            return client.execute(sql, params)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("evm_transfers query failed: %s", exc)
            return []

    rows = await _run_query(_query)
    if not rows:
        return []

    transfers = []
    for row in rows:
        transfers.append(
            {
                "chain": row[0],
                "block_number": row[1],
                "tx_hash": row[2],
                "from_address": row[3],
                "to_address": row[4],
                "token_address": row[5],
                "value": row[6],
                "block_timestamp": (row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7])),
                "log_index": row[8],
                "token_symbol": row[9] if row[9] else _NATIVE_TOKEN_SYMBOLS.get(row[0]),
                "token_name": row[10],
            }
        )

    return transfers


# ═══════════════════════════════════════════════════════════════════
# QUERY: Wallet labels
# ═══════════════════════════════════════════════════════════════════


async def _fetch_labels(
    addresses: set[str],
) -> dict[str, dict[str, Any]]:
    """Look up wallet labels for a set of addresses across all chains.

    Returns dict: address -> {label_name, label_category, is_sanctioned}
    """
    if not addresses:
        return {}

    client = _get_ch_client()
    if client is None:
        return {}

    addr_list = list(addresses)
    # ClickHouse doesn't support parameterized IN lists well,
    # so we build a format string for the IN clause.
    placeholders = ", ".join(f"%({i})s" for i in range(len(addr_list)))
    params = {str(i): a for i, a in enumerate(addr_list)}

    sql = f"""
        SELECT address, label_name, label_category, is_sanctioned
        FROM wallet_memory.wallet_labels
        WHERE address IN ({placeholders})
        ORDER BY address
    """

    def _query():
        try:
            return client.execute(sql, params)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("wallet_labels query failed: %s", exc)
            return []

    rows = await _run_query(_query)
    if not rows:
        return {}

    results: dict[str, dict[str, Any]] = {}
    for row in rows:
        addr = row[0].lower()
        # Keep the highest-priority label for each address
        category = row[2] or "unknown"
        priority = _LABEL_PRIORITY.get(category, 0)
        existing = results.get(addr)
        if existing is None or _LABEL_PRIORITY.get(existing.get("label_category", ""), 0) < priority:
            results[addr] = {
                "label_name": row[1] or "",
                "label_category": category,
                "is_sanctioned": bool(row[3]),
            }

    return results


# ═══════════════════════════════════════════════════════════════════
# BRIDGE DETECTION
# ═══════════════════════════════════════════════════════════════════


def _is_bridge_contract(address: str, chain: str) -> bool:
    """Check if an address is a known bridge contract on the given chain."""
    addr = address.lower()
    chain_lower = chain.lower()
    bridges = _BRIDGE_CONTRACTS.get(chain_lower, set())
    return addr in bridges


def _infer_bridge_destination(
    from_addr: str,
    to_addr: str,
    chain: str,
) -> str | None:
    """Try to infer bridge destination chain from transfer endpoints.

    If the FROM side is a bridge contract on chain X, the TO side
    likely receives on chain Y. We attempt to determine which chain.
    """
    # Check if either end is a bridge contract
    if _is_bridge_contract(to_addr, chain):
        # Bridge deposit — funds are moving TO the bridge
        # Common bridge destination patterns:
        if chain == "ethereum":
            return "arbitrum"  # most ETH bridges go to L2s
        if chain == "bsc":
            return "ethereum"
        if chain == "base":
            return "ethereum"
        if chain == "arbitrum":
            return "ethereum"
        if chain == "polygon":
            return "ethereum"
        if chain == "optimism":
            return "ethereum"
        return "ethereum"  # default guess

    if _is_bridge_contract(from_addr, chain):
        # Bridge withdrawal — funds are coming FROM the bridge
        # Withdrawals are from bridge contract TO user
        # The source chain was likely ethereum
        return "ethereum"

    return None


# ═══════════════════════════════════════════════════════════════════
# ENTITY ROLE ASSIGNMENT
# ═══════════════════════════════════════════════════════════════════


def _assign_role(
    address: str,
    is_seed: bool,
    label_category: str | None,
) -> str:
    """Assign a semantic role to an address based on its label and position."""
    if is_seed:
        return "origin"
    if label_category in ("exchange",):
        return "exchange"
    if label_category in ("mixer",):
        return "mixer"
    if label_category in ("scam", "exploiter", "malicious", "ponzi", "phishing_scam"):
        return "scam"
    if label_category in ("bridge",):
        return "bridge"
    if label_category in ("defi", "dex", "lending"):
        return "defi"
    if label_category in ("sanctioned", "darknet_market", "money_laundering"):
        return "sanctioned"
    return "intermediate"


# ═══════════════════════════════════════════════════════════════════
# CROSS-CHAIN EXPANSION
# ═══════════════════════════════════════════════════════════════════


def _evm_address_portable(address: str, source_chain: str) -> list[str]:
    """Return all EVM chains where this address is portable.

    Same private key works on all EVM chains — so if we see an address
    on ethereum, it likely also exists on BSC, Polygon, etc.
    """
    if source_chain not in _EVM_CHAINS:
        return []
    return [c for c in ["ethereum", "bsc", "polygon", "arbitrum", "optimism", "base", "avalanche"] if c != source_chain]


# ═══════════════════════════════════════════════════════════════════
# MAIN ANALYZER
# ═══════════════════════════════════════════════════════════════════


class CrossChainTraceAnalyzer:
    """
    Tracks fund flow across chains for a given wallet address.

    Uses chain-indexer ClickHouse data (73M EVM transfers) and
    wallet_labels entity database — all LOCAL, zero external API calls.
    """

    def __init__(self, max_transfers: int = 500, max_depth: int = 2) -> None:
        self.max_transfers = max_transfers
        self.max_depth = max_depth

    async def analyze(
        self,
        wallet_address: str,
        chain: str = "ethereum",
    ) -> CrossChainTraceReport:
        """
        Trace fund flow for a wallet address across chains.

        Args:
            wallet_address: The wallet address to trace.
            chain: Source chain (ethereum, bsc, polygon, etc.)

        Returns:
            CrossChainTraceReport with nodes, edges, bridge hops, and entity flags.
        """
        addr = wallet_address.lower().strip()
        logger.info(
            "CrossChainTrace: tracing %s on %s (depth=%d, max_xfers=%d)",
            addr,
            chain,
            self.max_depth,
            self.max_transfers,
        )

        try:
            return await self._trace(addr, chain)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.error("CrossChainTrace failed for %s: %s", addr, exc)
            return CrossChainTraceReport(
                seed_address=addr,
                seed_chain=chain,
                error=str(exc)[:500],
            )

    async def _trace(
        self,
        address: str,
        chain: str,
    ) -> CrossChainTraceReport:
        # ── Step 1: Fetch all transfers for this address ────────
        transfers = await _fetch_transfers(address, chain, self.max_transfers)

        # ── Step 2: Collect unique addresses + labels ──────────
        all_addresses: set[str] = set()
        for t in transfers:
            all_addresses.add(t["from_address"])
            all_addresses.add(t["to_address"])

        labels = await _fetch_labels(all_addresses) if all_addresses else {}

        # ── Step 3: For EVM addresses, also check other chains ─
        # Same address may exist on other EVM chains
        portable_chains = _evm_address_portable(address, chain)
        additional_transfers: list[dict[str, Any]] = []
        for other_chain in portable_chains[:3]:  # check up to 3 other chains
            xfers = await _fetch_transfers(address, other_chain, min(200, self.max_transfers // 2))
            additional_transfers.extend(xfers)
            for t in xfers:
                all_addresses.add(t["from_address"])
                all_addresses.add(t["to_address"])

        # Merge transfers
        all_transfers = transfers + additional_transfers

        # Re-fetch labels with expanded address set
        if additional_transfers:
            labels = await _fetch_labels(all_addresses)

        # ── Step 4: Build nodes + edges ───────────────────────
        seen_edges: set[tuple[str, str, str, int]] = set()  # (from, to, chain, block_number)
        nodes_map: dict[str, CrossChainNode] = {}
        edges_list: list[CrossChainEdge] = []
        bridge_hops: list[dict[str, Any]] = []
        known_entities: dict[str, str] = {}
        entity_flags: list[str] = []
        total_received = 0.0
        total_sent = 0.0
        chains_involved: set[str] = set()

        for t in all_transfers:
            edge_key = (
                t["from_address"],
                t["to_address"],
                t["chain"],
                t["block_number"],
            )
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            chains_involved.add(t["chain"])

            # Detect bridge interaction
            is_bridge = _is_bridge_contract(t["to_address"], t["chain"]) or _is_bridge_contract(
                t["from_address"], t["chain"]
            )
            bridge_dest = None
            if is_bridge:
                bridge_dest = _infer_bridge_destination(
                    t["from_address"],
                    t["to_address"],
                    t["chain"],
                )
                if bridge_dest:
                    bridge_hops.append(
                        {
                            "bridge_contract": (
                                t["to_address"]
                                if _is_bridge_contract(t["to_address"], t["chain"])
                                else t["from_address"]
                            ),
                            "source_chain": t["chain"],
                            "destination_chain": bridge_dest,
                            "tx_hash": t["tx_hash"],
                            "token_symbol": t.get("token_symbol"),
                            "value": t["value"],
                        }
                    )

            # Parse value for volume tracking (try float, fall back to 0)
            try:
                val_float = float(t["value"]) / 1e18 if t["value"] else 0.0
            except (ValueError, TypeError):
                val_float = 0.0

            # Build edge
            edge = CrossChainEdge(
                from_address=t["from_address"],
                to_address=t["to_address"],
                chain=t["chain"],
                token_address=t["token_address"],
                token_symbol=t.get("token_symbol"),
                value=t["value"],
                tx_hash=t["tx_hash"],
                block_number=t["block_number"],
                block_timestamp=t["block_timestamp"],
                is_bridge=is_bridge,
                bridge_destination_chain=bridge_dest,
            )
            edges_list.append(edge)

            # Build/update nodes
            for addr_key, is_from in [(t["from_address"], True), (t["to_address"], False)]:
                if addr_key not in nodes_map:
                    lbl = labels.get(addr_key, {})
                    lbl_cat = lbl.get("label_category") if lbl else None
                    is_seed = addr_key == address
                    nodes_map[addr_key] = CrossChainNode(
                        address=addr_key,
                        chain=t["chain"],
                        label=lbl.get("label_name") if lbl else None,
                        label_category=lbl_cat,
                        role=_assign_role(addr_key, is_seed, lbl_cat),
                        is_sanctioned=lbl.get("is_sanctioned", False) if lbl else False,
                    )

                node = nodes_map[addr_key]
                node.tx_count += 1
                if is_from:
                    node.total_sent += val_float
                    total_sent += val_float
                else:
                    node.total_received += val_float
                    total_received += val_float

            # Track known entities
            for addr_key in (t["from_address"], t["to_address"]):
                lbl = labels.get(addr_key)
                if lbl and lbl.get("label_category"):
                    known_entities[addr_key] = lbl["label_name"] or lbl["label_category"]

        # ── Step 5: Build entity flags from labels ─────────────
        for addr_key, label_info in labels.items():
            cat = label_info.get("label_category", "")
            if cat in (
                "exchange",
                "mixer",
                "scam",
                "exploiter",
                "malicious",
                "ponzi",
                "sanctioned",
                "darknet_market",
                "phishing_scam",
                "ransomware",
            ):
                entity_flags.append(
                    f"Address {addr_key[:10]}...{addr_key[-6:]} "
                    f"is labeled as {cat}: {label_info.get('label_name', 'unknown')}"
                )

        # ── Step 6: Compute risk score ─────────────────────────
        risk_score = 0.0
        risk_reasons: list[str] = []

        if known_entities:
            # Higher risk for scam/exchange/mixer interactions
            dangerous_labels = {
                "scam",
                "exploiter",
                "malicious",
                "ponzi",
                "phishing_scam",
                "ransomware",
                "mixer",
                "sanctioned",
                "darknet_market",
                "money_laundering",
            }
            dangerous_count = sum(1 for addr, lbl in labels.items() if lbl.get("label_category") in dangerous_labels)
            risk_score += min(dangerous_count * 15, 60)
            if dangerous_count > 0:
                risk_reasons.append(f"interacts with {dangerous_count} high-risk addresses")

        if bridge_hops:
            risk_score += 10
            risk_reasons.append(f"cross-chain activity via {len(bridge_hops)} bridge hops")

        if total_received > 0:
            # Large volume through wallet
            if total_received > 1000:
                risk_score += 5
            if total_received > 100000:
                risk_score += 10

        # Cap at 100
        risk_score = min(risk_score, 100.0)

        if risk_score < 25:
            risk_level = "low"
        elif risk_score < 50:
            risk_level = "moderate"
        elif risk_score < 75:
            risk_level = "high"
        else:
            risk_level = "critical"

        # ── Step 7: Serialize to report ────────────────────────
        node_dicts = [
            {
                "address": n.address,
                "chain": n.chain,
                "label": n.label,
                "label_category": n.label_category,
                "role": n.role,
                "total_received": round(n.total_received, 4),
                "total_sent": round(n.total_sent, 4),
                "tx_count": n.tx_count,
                "is_sanctioned": n.is_sanctioned,
            }
            for n in nodes_map.values()
        ]

        edge_dicts = [
            {
                "from_address": e.from_address,
                "to_address": e.to_address,
                "chain": e.chain,
                "token_address": e.token_address,
                "token_symbol": e.token_symbol,
                "value": e.value,
                "tx_hash": e.tx_hash,
                "block_number": e.block_number,
                "block_timestamp": e.block_timestamp,
                "is_bridge": e.is_bridge,
                "bridge_destination_chain": e.bridge_destination_chain,
            }
            for e in edges_list
        ]

        return CrossChainTraceReport(
            seed_address=address,
            seed_chain=chain,
            nodes=node_dicts,
            edges=edge_dicts,
            bridge_hops=bridge_hops,
            entity_flags=entity_flags,
            known_entities=known_entities,
            total_volume_received=round(total_received, 4),
            total_volume_sent=round(total_sent, 4),
            chains_involved=sorted(chains_involved),
            unique_counterparties=len(nodes_map) - 1,
            risk_score=round(risk_score, 1),
            risk_level=risk_level,
        )


# ═══════════════════════════════════════════════════════════════════
# MODULE COMPATIBILITY WRAPPER
# ═══════════════════════════════════════════════════════════════════
# The sentinel pipeline expects analyze(token_address, chain) -> dict.
# Our module takes (wallet_address, chain) so we provide a wrapper
# that can be called with (address, chain) for direct use,
# and a compatible analyze() for the standard pipeline's token-centric
# flow (which traces the deployer/creator instead).


_cross_chain_trace_analyzer: CrossChainTraceAnalyzer | None = None


def get_analyzer() -> CrossChainTraceAnalyzer:
    """Get the shared CrossChainTraceAnalyzer singleton."""
    global _cross_chain_trace_analyzer
    if _cross_chain_trace_analyzer is None:
        _cross_chain_trace_analyzer = CrossChainTraceAnalyzer()
    return _cross_chain_trace_analyzer


async def analyze_wallet(wallet: str, chain: str = "ethereum") -> dict[str, Any]:
    """Standalone entry point: trace a wallet address across chains.

    This is the primary API — call it directly when you have a wallet address.

    Returns dict for serialization (compatible with SentinelReport field storage).
    """
    analyzer = get_analyzer()
    report = await analyzer.analyze(wallet, chain)
    return _report_to_dict(report)


async def analyze(token_address: str, chain: str = "solana", **kwargs: Any) -> dict[str, Any]:
    """Sentinel pipeline-compatible entry point.

    For token-centric scans, we trace the token contract's deployer
    (if available via chain-indexer) or fall back to tracing the token address
    itself as a wallet.

    Args:
        token_address: Token contract address to trace.
        chain: Blockchain chain.

    Returns:
        Dict with cross-chain trace results (or empty if no deployer found).
    """
    # Try to find the deployer of this token via chain-indexer
    deployer = await _resolve_token_deployer(token_address, chain)
    if deployer:
        logger.info("CrossChainTrace: tracing deployer %s for token %s", deployer, token_address)
        return await analyze_wallet(deployer, chain)

    # Fall back: trace the token address itself as if it were a wallet
    logger.info("CrossChainTrace: tracing token address %s as wallet", token_address)
    return await analyze_wallet(token_address, chain)


async def _resolve_token_deployer(token_address: str, chain: str) -> str | None:
    """Find the deployer address for a token via chain-indexer deployer_events."""
    try:
        from app.domains.databus.providers.chains.chain_indexer import (
            _chain_indexer_deployer_history,
        )

        result = await _chain_indexer_deployer_history(
            address=token_address,
            chain=chain,
            limit=1,
        )
        if result and result.get("data", {}).get("deployer_events"):
            events = result["data"]["deployer_events"]
            if events:
                return events[0].get("deployer")
    except (ImportError, ValueError, RuntimeError, OSError) as exc:
        logger.debug("deployer lookup failed: %s", exc)
    return None


def _report_to_dict(report: CrossChainTraceReport) -> dict[str, Any]:
    """Convert CrossChainTraceReport to a plain dict for serialization."""
    return {
        "seed_address": report.seed_address,
        "seed_chain": report.seed_chain,
        "nodes": report.nodes,
        "edges": report.edges[:50],  # cap edges in output for readability
        "bridge_hops": report.bridge_hops,
        "entity_flags": report.entity_flags,
        "known_entities": report.known_entities,
        "total_volume_received": report.total_volume_received,
        "total_volume_sent": report.total_volume_sent,
        "chains_involved": report.chains_involved,
        "unique_counterparties": report.unique_counterparties,
        "risk_score": report.risk_score,
        "risk_level": report.risk_level,
        "error": report.error,
    }
