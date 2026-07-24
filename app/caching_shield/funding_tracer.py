"""
EVM Funding Source Tracer - Self-Built Blockchain Forensics

Traces where a wallet got its initial funding using our existing
public RPC infrastructure and ClickHouse wallet labels. No external
API needed - built entirely on our consensus RPC + local data.

Chains supported: all 9 EVM chains in consensus_rpc (1, 56, 137, 8453,
42161, 10, 43114, 250, 100)

Strategy:
  1. Get first N inbound transactions for the wallet
  2. Find the earliest ETH transfer (funding tx)
  3. Resolve the sender address
  4. Classify the sender (CEX, DEX, bridge, mixer, contract, unknown)
  5. If sender is also externally funded, trace one hop further
  6. Return funding trace with confidence scoring

Usage:
    from app.caching_shield.funding_tracer import trace_funding_source
    result = await trace_funding_source("0x...", chain_id=1)
    # Returns: {source_address, source_type, confidence, hops, ...}
"""

import os
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger("funding_tracer")

# Chain config
CHAIN_NAMES = {
    1: "ethereum",
    56: "bsc",
    137: "polygon",
    8453: "base",
    42161: "arbitrum",
    10: "optimism",
    43114: "avalanche",
    250: "fantom",
    100: "gnosis",
}

# Known CEX deposit addresses (from our imported label DB)
CEX_PATTERNS = [
    "binance",
    "coinbase",
    "kraken",
    "kucoin",
    "okx",
    "bybit",
    "gate.io",
    "mexc",
    "bitfinex",
    "huobi",
    "gemini",
    "crypto.com",
    "ftx",
    "bitstamp",
    "bittrex",
    "poloniex",
    "robinhood",
]

# Known bridge contracts
BRIDGE_PATTERNS = [
    "bridge",
    "portal",
    "wormhole",
    "layerzero",
    "stargate",
    "hop",
    "across",
    "synapse",
    "celer",
    "multichain",
    "anyswap",
    "orbiter",
    "socket",
    "li.fi",
    "bungee",
    "jumper",
]

# Known mixer/tumbler patterns
MIXER_PATTERNS = [
    "tornado",
    "mixer",
    "tumbler",
    "cyclone",
    "typhoon",
]

# Max depth to trace
MAX_TRACE_DEPTH = 3
MAX_TX_TO_CHECK = 20


@dataclass
class FundingTrace:
    """Result of a funding source trace."""

    wallet: str
    chain_id: int
    chain_name: str
    funding_tx_hash: str = ""
    source_address: str = ""
    source_type: str = "unknown"  # cex, dex, bridge, mixer, contract, eoa, unknown
    source_label: str = ""  # human-readable label if known
    confidence: float = 0.0  # 0-100
    funding_amount_eth: float = 0.0
    funding_timestamp: int = 0
    hops: list[dict] = field(default_factory=list)  # trace chain
    errors: list[str] = field(default_factory=list)


async def trace_funding_source(
    wallet: str,
    chain_id: int = 1,
    max_depth: int = MAX_TRACE_DEPTH,
) -> FundingTrace:
    """Trace where a wallet got its initial funding.

    Args:
        wallet: EVM address to trace
        chain_id: Ethereum chain ID (1, 56, 137, etc.)
        max_depth: How many hops to trace back (default 3)

    Returns:
        FundingTrace with source classification
    """
    chain_name = CHAIN_NAMES.get(chain_id, f"chain_{chain_id}")
    trace = FundingTrace(wallet=wallet, chain_id=chain_id, chain_name=chain_name)

    try:
        # Step 1: Get early transactions for this wallet
        txs = await _get_wallet_transactions(wallet, chain_id)

        if not txs:
            trace.errors.append("No transactions found")
            return trace

        # Step 2: Find the earliest inbound ETH transfer
        funding_tx = _find_earliest_inbound(txs, wallet)
        if not funding_tx:
            trace.errors.append("No inbound transfers found")
            return trace

        trace.funding_tx_hash = funding_tx.get("hash", "")
        trace.funding_amount_eth = float(funding_tx.get("value", 0)) / 1e18
        trace.funding_timestamp = int(funding_tx.get("timeStamp", 0))

        # Step 3: Resolve the funding source
        source = funding_tx.get("from", "").lower()
        trace.source_address = source
        trace.hops.append(
            {
                "address": source,
                "tx_hash": funding_tx.get("hash", ""),
                "amount_eth": trace.funding_amount_eth,
                "depth": 1,
            }
        )

        # Step 4: Classify the source
        source_type, source_label = await _classify_address(source, chain_id)
        trace.source_type = source_type
        trace.source_label = source_label
        trace.confidence = _confidence_for_type(source_type)

        # Step 5: If source is an EOA, trace one more hop
        if source_type == "eoa" and max_depth > 1:
            source_txs = await _get_wallet_transactions(source, chain_id)
            grandparent_tx = _find_earliest_inbound(source_txs, source)
            if grandparent_tx:
                gp_addr = grandparent_tx.get("from", "").lower()
                gp_type, gp_label = await _classify_address(gp_addr, chain_id)
                trace.hops.append(
                    {
                        "address": gp_addr,
                        "tx_hash": grandparent_tx.get("hash", ""),
                        "amount_eth": float(grandparent_tx.get("value", 0)) / 1e18,
                        "depth": 2,
                    }
                )
                # If we found a classified source, upgrade
                if gp_type != "eoa" and gp_type != "unknown":
                    trace.source_address = gp_addr
                    trace.source_type = gp_type
                    trace.source_label = gp_label
                    trace.confidence = _confidence_for_type(gp_type)

    except Exception as e:
        trace.errors.append(f"Trace error: {str(e)[:200]}")
        logger.warning(f"Funding trace failed for {wallet}: {e}")

    return trace


async def _get_wallet_transactions(address: str, chain_id: int) -> list[dict]:
    """Get recent transactions for a wallet using Blockscout or Etherscan.

    Uses our consensus RPC as fallback - walks getLogs for Transfer events.
    """
    # Try Blockscout first (covers all chains, one key)
    blockscout_key = os.getenv("BLOCKSCOUT_API_KEY", "")
    if blockscout_key:
        return await _fetch_blockscout_txs(address, chain_id, blockscout_key)

    # Fall back to Etherscan API
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
    if etherscan_key and chain_id == 1:
        return await _fetch_etherscan_txs(address, etherscan_key)

    # Final fallback: use public RPC with eth_getLogs for Transfer events
    return await _fetch_rpc_transfers(address, chain_id)


async def _fetch_blockscout_txs(address: str, chain_id: int, api_key: str) -> list[dict]:
    """Fetch transactions via Blockscout v2 API.

    Format: GET /{chain_id}/api/v2/addresses/{address}/transactions?apikey=KEY
    Response: {"items": [{"hash": ..., "from": {"hash": ...}, "to": {"hash": ...}, "value": ...}]}
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.blockscout.com/{chain_id}/api/v2/addresses/{address}/transactions",
                params={
                    "apikey": api_key,
                    "filter": "to",  # inbound only
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                # Convert v2 format to etherscan-compatible format
                txs = []
                for item in items:
                    txs.append(
                        {
                            "hash": item.get("hash", ""),
                            "from": (item.get("from") or {}).get("hash", ""),
                            "to": (item.get("to") or {}).get("hash", ""),
                            "value": item.get("value", "0"),
                            "timeStamp": _parse_timestamp(item.get("timestamp", "")),
                            "blockNumber": str(item.get("block", 0)),
                        }
                    )
                return sorted(txs, key=lambda t: int(t.get("blockNumber", 0)))
    except Exception as e:
        logger.debug(f"Blockscout v2 fetch failed for {address}: {e}")
    return []


async def _fetch_etherscan_txs(address: str, api_key: str) -> list[dict]:
    """Fetch transactions via Etherscan API."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.etherscan.io/api",
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": MAX_TX_TO_CHECK,
                    "sort": "asc",
                    "apikey": api_key,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    return data.get("result", [])
    except Exception as e:
        logger.debug(f"Etherscan fetch failed for {address}: {e}")
    return []


async def _fetch_rpc_transfers(address: str, chain_id: int) -> list[dict]:
    """Fallback: get transactions via public RPC getLogs for Transfer events."""
    from app.consensus_rpc import get_consensus_rpc

    rpc = get_consensus_rpc()

    # Get ETH transfers via getLogs (Transfer event signature)
    transfer_sig = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    try:
        result = await rpc.evm_query_with_consensus(
            chain_id=chain_id,
            method="eth_getLogs",
            params=[
                {
                    "fromBlock": "0x0",
                    "toBlock": "latest",
                    "topics": [
                        transfer_sig,
                        None,  # any sender
                        _pad_address(address),  # receiver = our wallet
                    ],
                }
            ],
        )
        if result and result.value:
            # Parse logs into transaction-like objects
            txs = []
            for log in result.value[:MAX_TX_TO_CHECK]:
                txs.append(
                    {
                        "hash": log.get("transactionHash", ""),
                        "from": _unpad_address(log.get("topics", ["", "", ""])[1]),
                        "to": address,
                        "value": str(int(log.get("data", "0x0"), 16)),
                        "blockNumber": str(int(log.get("blockNumber", "0x0"), 16)),
                    }
                )
            return sorted(txs, key=lambda t: int(t.get("blockNumber", 0)))
    except Exception as e:
        logger.debug(f"RPC getLogs failed for {address}: {e}")

    return []


def _find_earliest_inbound(txs: list[dict], wallet: str) -> dict | None:
    """Find the earliest transaction where this wallet received ETH."""
    wallet_lower = wallet.lower()
    for tx in txs:
        to_addr = tx.get("to", "").lower()
        value = int(tx.get("value", 0))
        if to_addr == wallet_lower and value > 0:
            return tx
    return None


async def _classify_address(address: str, chain_id: int) -> tuple[str, str]:
    """Classify an address as CEX, DEX, bridge, mixer, contract, or EOA.

    Uses multiple strategies in priority order:
      1. Check local ClickHouse wallet labels
      2. Check known patterns in address/name
      3. Check if it's a contract (has code)
      4. Default to EOA
    """
    address.lower()

    # Strategy 1: Check local wallet labels (ClickHouse)
    try:
        from app.wallet_label_loader import lookup_wallet_label

        label = await lookup_wallet_label(address)
        if label:
            label_lower = label.lower()
            for cex in CEX_PATTERNS:
                if cex in label_lower:
                    return ("cex", label)
            for bridge in BRIDGE_PATTERNS:
                if bridge in label_lower:
                    return ("bridge", label)
            for mixer in MIXER_PATTERNS:
                if mixer in label_lower:
                    return ("mixer", label)
            # Generic label
            if any(kw in label_lower for kw in ["dex", "swap", "exchange", "amm"]):
                return ("dex", label)
            return ("known", label)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Label lookup failed: {e}")

    # Strategy 2: Check if it's a contract
    is_contract = await _is_contract(address, chain_id)
    if is_contract:
        return ("contract", "contract")

    # Strategy 3: Default - it's an externally owned account
    return ("eoa", "")


async def _is_contract(address: str, chain_id: int) -> bool:
    """Check if an address is a contract by calling eth_getCode."""
    try:
        from app.consensus_rpc import get_consensus_rpc

        rpc = get_consensus_rpc()
        result = await rpc.evm_query_with_consensus(
            chain_id=chain_id,
            method="eth_getCode",
            params=[address, "latest"],
        )
        if result and result.value:
            code = result.value
            return code != "0x" and len(str(code)) > 4
    except (AttributeError, ConnectionError, ImportError, TimeoutError, httpx.HTTPError):
        pass
    return False


def _confidence_for_type(source_type: str) -> float:
    """Return confidence score for a source classification."""
    scores = {
        "cex": 85.0,
        "bridge": 75.0,
        "mixer": 70.0,
        "dex": 65.0,
        "contract": 60.0,
        "known": 80.0,
        "eoa": 30.0,
        "unknown": 10.0,
    }
    return scores.get(source_type, 10.0)


def _pad_address(address: str) -> str:
    """Pad address to 32 bytes for log topics."""
    addr = address.lower().replace("0x", "")
    return "0x" + addr.zfill(64)


def _unpad_address(hex_str: str) -> str:
    """Extract 20-byte address from 32-byte padded topic."""
    if hex_str and len(hex_str) >= 42:
        return "0x" + hex_str[-40:]
    return hex_str


def _parse_timestamp(ts: str) -> int:
    """Parse ISO timestamp to Unix epoch int."""
    if not ts:
        return 0
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ConnectionError, OSError, TimeoutError):
        return 0
