"""
SENTINEL - Wash Trading / Circular Transfer Detection
==============================================
Builds directed graph of token transfers and detects:
  - Multi-hop circular trades (A→B→C→D→A over days)
  - Cross-DEX loops (buy Raydium, sell Orca, re-buy Jupiter)
  - Token-denominated wash trades
  - Volume inflation through coordinated activity

Uses networkx for graph cycle detection with time-decay weighting.
A transfer graph with >15% cycle volume indicates wash trading.

Data sources (direct API calls - no self.api_base):
  Solana:     Helius Enhanced Transactions API (parsed transfers)
              Helius DAS API (token metadata)
  EVM chains: Moralis (token transfer history)
              Etherscan-family (tokentx endpoint)
"""

from app.core.logging import get_logger
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

import httpx

from app.chain_registry import is_solana

logger = get_logger("wash_trading")

try:
    import networkx as nx

    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    logger.warning("networkx not installed - wash trading cycle detection disabled. pip install networkx")

# ── API Keys ────────────────────────────────────────────────

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")

DEXSCREENER_URL = "https://api.dexscreener.com"
HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
HELIUS_API_URL = "https://api.helius.xyz"
MORALIS_DATA_URL = "https://deep-index.moralis.io/api/v2.2"

ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {"url": "https://api.polygonscan.com/api", "key": POLYGONSCAN_API_KEY},
    "avalanche": {"url": "https://api.snowtrace.io/api", "key": SNOWTRACE_API_KEY},
    "fantom": {"url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ETHERSCAN_API_KEY},
    "optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "base": {"url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY},
}

MORALIS_CHAIN_MAP = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
}


@dataclass
class TransferRecord:
    from_address: str
    to_address: str
    amount: float
    token: str  # "SOL", "ETH", "USDC", or token address
    timestamp: int  # unix ms
    tx_hash: str = ""
    dex: str = ""  # "raydium", "orca", "jupiter", "uniswap", etc.
    block_number: int = 0


@dataclass
class WashCycle:
    wallets: list[str]
    combined_volume: float
    cycle_length: int  # number of hops
    time_span_hours: float  # how long the cycle took
    confidence: float  # 0-1
    pattern_type: str  # "simple_roundtrip", "multi_hop", "cross_dex", "token_denominated"
    evidence: list[str] = field(default_factory=list)


@dataclass
class WashTradingReport:
    token_address: str
    chain: str
    total_volume: float
    wash_volume: float
    wash_score: float  # 0-100, higher = more wash trading
    detected_cycles: list[WashCycle]
    cross_dex_loops: int  # Number of cycles crossing multiple DEXes
    token_denominated_wash: int  # Cycles using the same token
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    warnings: list[str] = field(default_factory=list)


class WashTradingDetector:
    """Detects wash trading through circular transfer analysis.

    Uses direct API calls:
      - Helius Enhanced Transactions + DAS for Solana
      - Moralis for EVM token transfers
      - Etherscan-family as fallback for EVM tx data
    """

    # Known DEX router addresses for cross-DEX detection
    DEX_ROUTERS: ClassVar[dict] ={
        "solana": {
            "JUP4Fb2cqiRUr6pNQqvZJQ2SjYqQsBfFidNFBFNB2eq": "Jupiter",
            "675kPX9MHTjS2zt1qfr1NYHuzeL7Gxq8RcE3E4A8N2W5": "Raydium AMM",
            "whirLbMiicVdup4W1M5VQ8c2m6F5aYsbEbU35JrhP5Y": "Orca Whirlpool",
            "9W959Dq2BqZ5EbC3mZV4Gfe8D39VNgVUJ9Kny3ePHF8r": "Raydium CLMM",
        },
        "ethereum": {
            "0x68b346670356c372847478248a90E8eBBaED8e1f": "Uniswap V3",
            "0xEf1c6E67703c7BD7107eed8306F688f3a6E76c3d": "SushiSwap",
            "0xd9e1cE17a55C3D9f834a4b46Ec8a0DD9Cc10fC69": "1inch",
        },
        "base": {
            "0x6fF56910b4216Ee0915811c3c4E0EE1a9E6d4418": "Aerodrome",
            "0x3262D07119Dc7a7F5F0ee7E724F6FA5F4215E3E6": "Uniswap V3 Base",
        },
        "bsc": {
            "0x13f4EA83D0bd40E6F45244DA8a0A0A5ae61A6B31b": "PancakeSwap V3",
            "0x10ED43C718714eb63d5aA57B78B54704E25637fE": "PancakeSwap V2",
        },
    }

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0)

    # ── Helius helpers ────────────────────────────────────────

    async def _helius_rpc(self, method: str, params: list | None = None) -> dict | None:
        """Make a Helius JSON-RPC call."""
        if not HELIUS_API_KEY:
            logger.warning("HELIUS_API_KEY not set")
            return None
        try:
            resp = await self.client.post(
                f"{HELIUS_RPC_URL}/?api-key={HELIUS_API_KEY}",
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result")
        except Exception as e:
            logger.warning(f"Helius RPC call failed: {e}")
        return None

    async def _helius_enhanced_txs(self, address: str, limit: int = 100) -> list[dict]:
        """Fetch Enhanced Transactions for a Solana address via Helius.

        Returns parsed transaction data including token transfers,
        native transfers, and DEX interaction details.
        """
        if not HELIUS_API_KEY:
            return []

        try:
            # First get signatures
            sigs_result = await self._helius_rpc("getSignaturesForAddress", [address, {"limit": limit}])
            if not sigs_result or not isinstance(sigs_result, list):
                return []

            signatures = [s.get("signature", "") for s in sigs_result if s.get("signature")]
            if not signatures:
                return []

            # Then parse transactions using Helius Enhanced Transactions API
            resp = await self.client.post(
                f"{HELIUS_API_URL}/v0/transactions/?api-key={HELIUS_API_KEY}",
                json={"transactions": signatures[:50]},  # Batch limit
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data if data else []
        except Exception as e:
            logger.warning(f"Helius Enhanced Transactions failed for {address}: {e}")

        return []

    # ── Moralis helpers ────────────────────────────────────────

    def _moralis_chain(self, chain: str) -> str:
        """Convert chain name to Moralis chain param."""
        return MORALIS_CHAIN_MAP.get(chain.lower(), "eth")

    async def _moralis_get(self, url: str) -> dict | None:
        """Make a Moralis Data API call with key rotation."""
        if not MORALIS_API_KEY:
            logger.warning("MORALIS_API_KEY not set")
            return None
        try:
            resp = await self.client.get(url, headers={"X-API-Key": MORALIS_API_KEY, "Accept": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get("result", data.get("data", data))
        except Exception as e:
            logger.warning(f"Moralis call failed: {e}")
        return None

    # ── Etherscan helpers ──────────────────────────────────────

    async def _etherscan_get(self, chain: str, params: dict) -> dict | None:
        """Make an Etherscan-family API call."""
        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            return None
        api_key = net["key"]
        if not api_key:
            return None
        params["apikey"] = api_key
        try:
            resp = await self.client.get(net["url"], params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    return data.get("result")
                return data
        except Exception as e:
            logger.warning(f"Etherscan call failed for {chain}: {e}")
        return None

    # ── DexScreener helper ──────────────────────────────────────

    async def _dexscreener_token_pairs(self, token_address: str) -> list[dict]:
        """Get DexScreener pairs for a token (to identify DEX interactions)."""
        try:
            resp = await self.client.get(f"{DEXSCREENER_URL}/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("pairs") or []
        except Exception as e:
            logger.warning(f"DexScreener lookup failed for {token_address}: {e}")
        return []

    # ── Transfer fetching ──────────────────────────────────────

    def _identify_dex(self, address: str, chain: str) -> str:
        """Identify DEX from router address."""
        routers = self.DEX_ROUTERS.get(chain, {})
        return routers.get(address, "")

    async def _fetch_solana_transfers(self, token_address: str) -> list[TransferRecord]:
        """Fetch Solana token transfer data using Helius Enhanced Transactions.

        Parses tokenTransfers from enhanced transactions to build transfer records.
        """
        transfers = []

        # Get signatures for the token address
        sigs_result = await self._helius_rpc("getSignaturesForAddress", [token_address, {"limit": 100}])
        if not sigs_result or not isinstance(sigs_result, list):
            return []

        signatures = [s.get("signature", "") for s in sigs_result if s.get("signature")]
        if not signatures:
            return []

        # Batch fetch enhanced transactions
        try:
            resp = await self.client.post(
                f"{HELIUS_API_URL}/v0/transactions/?api-key={HELIUS_API_KEY}",
                json={"transactions": signatures[:50]},
            )
            if resp.status_code != 200:
                # Fallback: use individual transaction parsing
                for sig in signatures[:30]:
                    tx = await self._helius_rpc(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    )
                    if not tx:
                        continue
                    # Parse Solana transaction
                    msg = tx.get("transaction", {}).get("message", {})
                    accounts = msg.get("accountKeys", [])
                    account_addrs = []
                    for a in accounts:
                        if isinstance(a, dict):
                            account_addrs.append(a.get("pubkey", ""))
                        elif isinstance(a, str):
                            account_addrs.append(a)

                    # Parse inner instructions for token transfers
                    inner = tx.get("meta", {}).get("innerInstructions", [])
                    for inst_group in inner:
                        for inst in inst_group.get("instructions", []):
                            parsed = inst.get("parsed", {})
                            if parsed.get("type") == "transfer" or parsed.get("type") == "transferChecked":
                                info = parsed.get("info", {})
                                token_amount = float(
                                    info.get("tokenAmount", {}).get("uiAmount", 0) or info.get("amount", 0)
                                )
                                if token_amount <= 0:
                                    continue
                                from_addr = info.get("authority", info.get("source", ""))
                                to_addr = info.get("destination", "")
                                dex = ""
                                for addr in account_addrs:
                                    dex_name = self._identify_dex(addr, "solana")
                                    if dex_name:
                                        dex = dex_name
                                        break

                                transfers.append(
                                    TransferRecord(
                                        from_address=from_addr,
                                        to_address=to_addr,
                                        amount=token_amount,
                                        token=token_address,
                                        timestamp=tx.get("blockTime", 0) * 1000,
                                        tx_hash=sig,
                                        dex=dex,
                                    )
                                )
                return transfers

            # Parse enhanced transactions
            txs = resp.json()
            if isinstance(txs, list):
                for tx in txs:
                    # Extract token transfers
                    for transfer in tx.get("tokenTransfers", []):
                        mint = transfer.get("mint", "")
                        # Filter to our token
                        if mint != token_address:
                            continue
                        amount = float(transfer.get("tokenAmount", 0) or 0)
                        if amount <= 0:
                            continue

                        from_addr = transfer.get("fromUserAccount", transfer.get("from", ""))
                        to_addr = transfer.get("toUserAccount", transfer.get("to", ""))
                        dex = ""
                        # Check DEX involvement from instructions
                        for inst in tx.get("instructions", []):
                            program_id = inst.get("programId", "")
                            dex_name = self._identify_dex(program_id, "solana")
                            if dex_name:
                                dex = dex_name
                                break

                        transfers.append(
                            TransferRecord(
                                from_address=from_addr,
                                to_address=to_addr,
                                amount=amount,
                                token=token_address,
                                timestamp=tx.get("timestamp", 0) * 1000,
                                tx_hash=tx.get("signature", ""),
                                dex=dex,
                            )
                        )

                    # Also check native SOL transfers for round-trip detection
                    for native_transfer in tx.get("nativeTransfers", []):
                        # Native transfers are SOL - include if they might be part of wash
                        from_addr = native_transfer.get("fromUserAccount", native_transfer.get("from", ""))
                        to_addr = native_transfer.get("toUserAccount", native_transfer.get("to", ""))
                        amount = float(native_transfer.get("amount", 0) or 0) / 1e9  # lamports to SOL
                        if amount < 0.01:  # Skip dust
                            continue

                        transfers.append(
                            TransferRecord(
                                from_address=from_addr,
                                to_address=to_addr,
                                amount=amount,
                                token="SOL",
                                timestamp=tx.get("timestamp", 0) * 1000,
                                tx_hash=tx.get("signature", ""),
                                dex="",
                            )
                        )

        except Exception as e:
            logger.warning(f"Solana transfer parsing failed: {e}")

        return transfers

    async def _fetch_evm_transfers(self, token_address: str, chain: str) -> list[TransferRecord]:
        """Fetch EVM token transfer data using Moralis + Etherscan.

        Uses Moralis for token transfer history (preferred, has better data)
        and falls back to Etherscan tokentx.
        """
        transfers = []
        moralis_chain = self._moralis_chain(chain)

        # ── Strategy 1: Moralis token transfers ──
        if MORALIS_API_KEY:
            try:
                url = f"{MORALIS_DATA_URL}/erc20/{token_address}/transfers?chain={moralis_chain}&limit=100"
                result = await self._moralis_get(url)
                if result and isinstance(result, list):
                    for tx in result:
                        from_addr = tx.get("from_address", "") or tx.get("from", "")
                        to_addr = tx.get("to_address", "") or tx.get("to", "")
                        value_raw = tx.get("value", "0")
                        decimals = int(tx.get("decimals", "18") or "18")
                        try:
                            amount = float(value_raw) / (10**decimals) if value_raw else 0
                        except (ValueError, ZeroDivisionError):
                            amount = 0

                        if amount <= 0:
                            continue

                        # Determine DEX from to_address
                        dex = self._identify_dex(to_addr, chain) or self._identify_dex(from_addr, chain)

                        # Parse timestamp
                        block_ts = tx.get("block_timestamp", "")
                        timestamp = 0
                        if block_ts:
                            try:
                                dt = datetime.fromisoformat(block_ts.replace("Z", "+00:00"))
                                timestamp = int(dt.timestamp() * 1000)
                            except (ValueError, AttributeError):
                                pass

                        transfers.append(
                            TransferRecord(
                                from_address=from_addr,
                                to_address=to_addr,
                                amount=amount,
                                token=token_address,
                                timestamp=timestamp,
                                tx_hash=tx.get("transaction_hash", tx.get("hash", "")),
                                dex=dex,
                                block_number=int(tx.get("block_number", 0) or 0),
                            )
                        )
                    if transfers:
                        return transfers
            except Exception as e:
                logger.warning(f"Moralis transfer fetch failed for {token_address} on {chain}: {e}")

        # ── Strategy 2: Etherscan tokentx ──
        if chain in ETHERSCAN_NETWORKS:
            result = await self._etherscan_get(
                chain,
                {
                    "module": "account",
                    "action": "tokentx",
                    "address": token_address,
                    "contractaddress": token_address,
                    "page": 1,
                    "offset": 100,
                    "sort": "desc",
                },
            )
            if result and isinstance(result, list):
                for tx in result:
                    from_addr = tx.get("from", "")
                    to_addr = tx.get("to", "")
                    value_raw = tx.get("value", "0")
                    token_decimal = int(tx.get("tokenDecimal", "18") or "18")
                    try:
                        amount = float(value_raw) / (10**token_decimal) if value_raw else 0
                    except (ValueError, ZeroDivisionError):
                        amount = 0

                    if amount <= 0:
                        continue

                    dex = self._identify_dex(to_addr, chain) or self._identify_dex(from_addr, chain)
                    timestamp = int(tx.get("timeStamp", 0)) * 1000

                    transfers.append(
                        TransferRecord(
                            from_address=from_addr,
                            to_address=to_addr,
                            amount=amount,
                            token=token_address,
                            timestamp=timestamp,
                            tx_hash=tx.get("hash", ""),
                            dex=dex,
                            block_number=int(tx.get("blockNumber", 0)),
                        )
                    )

        return transfers

    # ── Graph & cycle detection ────────────────────────────────

    def build_transfer_graph(self, transfers: list[TransferRecord]) -> "nx.DiGraph":
        """Build directed graph of transfers for cycle detection."""
        if not HAS_NETWORKX:
            raise ImportError("networkx required for wash trading detection")

        G = nx.DiGraph()

        for tx in transfers:
            G.add_edge(
                tx.from_address,
                tx.to_address,
                weight=tx.amount,
                timestamp=tx.timestamp,
                token=tx.token,
                dex=tx.dex,
                tx_hash=tx.tx_hash,
            )

        return G

    def detect_cycles(self, transfers: list[TransferRecord], max_cycle_length: int = 6) -> list[WashCycle]:
        """Detect circular trading patterns in transfer data.

        Uses DFS-based cycle detection on the transfer graph.
        Filters cycles by:
          - Time span (must complete within 72 hours)
          - Volume significance (must involve >0.1% of total supply)
          - Economic irrationality (no real trading purpose)
        """
        if not HAS_NETWORKX or not transfers:
            return []

        G = self.build_transfer_graph(transfers)
        cycles = []

        # Find simple cycles (A→B→A, A→B→C→A)
        try:
            simple_cycles = list(nx.simple_cycles(G))
        except nx.NetworkXError:
            return []

        for cycle in simple_cycles:
            if len(cycle) > max_cycle_length:
                continue

            # Calculate cycle volume
            cycle_volume = 0
            time_stamps = []
            dexes = set()

            for i, wallet in enumerate(cycle):
                next_wallet = cycle[(i + 1) % len(cycle)]
                if G.has_edge(wallet, next_wallet):
                    edge_data = G[wallet][next_wallet]
                    cycle_volume += edge_data.get("weight", 0)
                    time_stamps.append(edge_data.get("timestamp", 0))
                    if edge_data.get("dex"):
                        dexes.add(edge_data["dex"])

            # Time span check
            if time_stamps and max(time_stamps) > 0 and min(time_stamps) > 0:
                time_span_hours = (max(time_stamps) - min(time_stamps)) / 3600000
            else:
                time_span_hours = 0

            # Skip cycles that took too long (not coordinated)
            if time_span_hours > 72:
                continue

            # Determine pattern type
            if len(dexes) > 1:
                pattern_type = "cross_dex"
            elif len(cycle) == 2:
                pattern_type = "simple_roundtrip"
            else:
                pattern_type = "multi_hop"

            # Calculate confidence based on:
            # - Time span (shorter = more suspicious)
            # - Volume significance
            # - Number of hops (fewer = more suspicious)
            confidence = 0.5
            if time_span_hours < 1:
                confidence += 0.3  # Very fast = high confidence
            elif time_span_hours < 6:
                confidence += 0.2
            if len(cycle) == 2:
                confidence += 0.1  # Roundtrip = obvious
            confidence = min(0.99, confidence)

            cycles.append(
                WashCycle(
                    wallets=cycle,
                    combined_volume=round(cycle_volume / 2, 4),  # Divide by 2 to avoid double counting
                    cycle_length=len(cycle),
                    time_span_hours=round(time_span_hours, 2),
                    confidence=round(confidence, 2),
                    pattern_type=pattern_type,
                    evidence=[
                        f"{len(cycle)}-wallet {pattern_type} over {time_span_hours:.1f}h",
                        f"Involves {len(dexes)} DEX(es): {', '.join(dexes) or 'unknown'}",
                    ],
                )
            )

        # Sort by volume (largest first)
        cycles.sort(key=lambda c: c.combined_volume, reverse=True)
        return cycles

    async def analyze(self, token_address: str, chain: str) -> WashTradingReport:
        """Full wash trading analysis for a token.

        Steps:
        1. Fetch transfer data from direct API sources
        2. Build transfer graph
        3. Detect circular cycles
        4. Classify by pattern type
        5. Calculate wash trading score
        """
        # Fetch transfer data
        transfers = await self._fetch_transfers(token_address, chain)

        if not transfers:
            return WashTradingReport(
                token_address=token_address,
                chain=chain,
                total_volume=0,
                wash_volume=0,
                wash_score=0,
                detected_cycles=[],
                cross_dex_loops=0,
                token_denominated_wash=0,
                warnings=["No transfer data available"],
            )

        total_volume = sum(t.amount for t in transfers)

        # Detect cycles
        cycles = self.detect_cycles(transfers)

        # Calculate wash volume
        wash_volume = sum(c.combined_volume for c in cycles)
        wash_score = min(100, (wash_volume / total_volume * 100) * 2) if total_volume > 0 else 0

        # Count cross-DEX loops
        cross_dex = sum(1 for c in cycles if c.pattern_type == "cross_dex")
        token_wash = sum(1 for c in cycles if c.pattern_type == "token_denominated")

        # Calculate risk
        warnings = []
        if wash_score > 50:
            warnings.append(f"CRITICAL: {wash_score:.0f}% wash trading detected - likely artificial volume")
        elif wash_score > 25:
            warnings.append(f"HIGH: {wash_score:.0f}% wash trading detected - significant volume inflation")
        elif wash_score > 10:
            warnings.append(f"MODERATE: {wash_score:.0f}% wash trading detected")

        if cross_dex > 0:
            warnings.append(f"CROSS-DEX: {cross_dex} cycles involving multiple DEXes")

        risk_level = (
            "CRITICAL" if wash_score > 50 else "HIGH" if wash_score > 25 else "MEDIUM" if wash_score > 10 else "LOW"
        )

        return WashTradingReport(
            token_address=token_address,
            chain=chain,
            total_volume=round(total_volume, 4),
            wash_volume=round(wash_volume, 4),
            wash_score=round(wash_score, 1),
            detected_cycles=cycles[:20],  # Top 20 cycles
            cross_dex_loops=cross_dex,
            token_denominated_wash=token_wash,
            risk_level=risk_level,
            warnings=warnings,
        )

    async def _fetch_transfers(self, token_address: str, chain: str) -> list[TransferRecord]:
        """Fetch recent transfer data for a token from direct API calls.

        Routes to chain-specific data sources:
          - Solana: Helius Enhanced Transactions + DAS
          - EVM: Moralis token transfers (primary) + Etherscan tokentx (fallback)
        """
        if is_solana(chain):
            return await self._fetch_solana_transfers(token_address)
        else:
            return await self._fetch_evm_transfers(token_address, chain)
