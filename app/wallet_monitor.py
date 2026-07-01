"""
Wallet Monitor — Live Wallet Intelligence Feed
===============================================

Real-time wallet monitoring across EVM (Alchemy WebSocket) and Solana (Helius WebSocket):
  • Watchlist management (subscribe/unsubscribe wallet addresses, persist in Redis)
  • Configurable alert rules (large transfers, scammer interactions, DEX first-timers,
    new token approvals, contract creations)
  • Alert channels: in-app Redis pub/sub + webhook callbacks
  • Intel feed: stream of "interesting" transactions across watched wallets
  • Burst detection: flag wallets with unusual tx velocity
  • Auto-label suggestions: suggest entity label when a new pattern emerges

FastAPI-ready, async websockets, Redis pub/sub, background tasks.
Matches style of mempool_sentinel.py.

Requires: websockets, asyncio, httpx, redis
"""

import asyncio
import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets

# Redis
try:
    import redis.asyncio as redis_async
except ImportError:
    redis_async = None


# ─── CONFIG ─────────────────────────────────────────────────

ALERT_TTL_SECONDS = int(os.getenv("WALLET_ALERT_TTL", "86400"))
WATCHLIST_KEY = "wallet:watchlist"
WALLET_LABELS_KEY = "wallet:labels"
WALLET_HISTORY_KEY_PREFIX = "wallet:history"
WALLET_TX_VELOCITY_WINDOW = int(os.getenv("WALLET_VELOCITY_WINDOW", "300"))  # 5 min
WALLET_BURST_THRESHOLD = int(os.getenv("WALLET_BURST_THRESHOLD", "10"))  # txs per window
LARGE_TRANSFER_USD = float(os.getenv("WALLET_LARGE_TRANSFER_USD", "10000"))
WEBHOOK_TIMEOUT = int(os.getenv("WEBHOOK_TIMEOUT", "10"))

# Provider WebSocket URLs
WS_URLS = {
    "solana": os.getenv("HELIUS_WS", ""),
    "ethereum": os.getenv("ALCHEMY_ETH_WS", ""),
    "base": os.getenv("ALCHEMY_BASE_WS", ""),
    "arbitrum": os.getenv("ALCHEMY_ARB_WS", ""),
    "optimism": os.getenv("ALCHEMY_OPT_WS", ""),
}

# HTTP RPC URLs (for full tx fetch)
RPC_URLS = {
    "ethereum": os.getenv("ETH_RPC", ""),
    "base": os.getenv("BASE_RPC", ""),
    "arbitrum": os.getenv("ARB_RPC", ""),
    "optimism": os.getenv("OPT_RPC", ""),
    "solana": os.getenv("SOLANA_RPC", ""),
}

# Known scammer / blocklist source — shared with threat_intel module
SCAMMER_SET_KEY = "threatintel:blocklist"

# Known DEX router addresses (EVM + Solana)
DEX_ROUTERS = {
    "uniswap_v2": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "uniswap_v3": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "sushiswap": "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
    "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "raydium": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "orca": "9W959DqEETiGZocYWCQPaJ6sBfUzHM7t2vNtdVzJ3rHc",
    "jupiter": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
}

# Token approval event signature (ERC20 Approval)
APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Label pattern heuristics
LABEL_PATTERNS = {
    "dex_trader": {"min_dex_txs": 5, "time_window_hours": 24},
    "nft_flipper": {"min_nft_txs": 5, "time_window_hours": 24},
    "whale": {"min_transfer_usd": 500_000, "time_window_hours": 24},
    "airdrop_farmer": {"min_token_receives": 10, "time_window_hours": 24},
    "contract_deployer": {"min_contract_creates": 3, "time_window_hours": 72},
    "mixer_user": {"min_interactions_with_mixer": 2, "time_window_hours": 72},
}


# ─── REDIS ───────────────────────────────────────────────────

_redis: Any = None


async def _get_redis():
    global _redis
    if _redis is None and redis_async is not None:
        try:
            _redis = redis_async.Redis(
                host=os.getenv("REDIS_HOST", "127.0.0.1"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                db=int(os.getenv("REDIS_DB", "0")),
                decode_responses=True,
            )
        except Exception:
            _redis = None
    return _redis


# ─── DATA MODELS ────────────────────────────────────────────


@dataclass
class WalletTx:
    hash: str
    from_addr: str
    to_addr: str
    value: float
    chain: str
    timestamp: str
    gas_price: float | None = None
    token_transfers: list[dict] = field(default_factory=list)
    is_contract_interaction: bool = False
    is_contract_creation: bool = False
    is_approval: bool = False
    dex_swap: dict | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "hash": self.hash,
            "from": self.from_addr,
            "to": self.to_addr,
            "value": self.value,
            "chain": self.chain,
            "timestamp": self.timestamp,
            "is_approval": self.is_approval,
            "is_contract_interaction": self.is_contract_interaction,
            "is_contract_creation": self.is_contract_creation,
            "dex_swap": self.dex_swap,
            "token_transfers": self.token_transfers,
        }


@dataclass
class WalletAlert:
    alert_type: str
    wallet: str
    chain: str
    tx_hash: str
    severity: str  # info, warning, critical
    message: str
    metadata: dict = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "wallet_alert",
            "alert_type": self.alert_type,
            "wallet": self.wallet,
            "chain": self.chain,
            "tx_hash": self.tx_hash,
            "severity": self.severity,
            "message": self.message,
            "metadata": self.metadata,
            "timestamp": self.timestamp or datetime.now(UTC).isoformat(),
        }


@dataclass
class BurstSignal:
    wallet: str
    chain: str
    tx_count: int
    window_seconds: int
    severity: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "type": "burst",
            "wallet": self.wallet,
            "chain": self.chain,
            "tx_count": self.tx_count,
            "window_seconds": self.window_seconds,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


@dataclass
class LabelSuggestion:
    wallet: str
    chain: str
    suggested_label: str
    confidence: float
    reasoning: str
    pattern_summary: dict
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "type": "label_suggestion",
            "wallet": self.wallet,
            "chain": self.chain,
            "suggested_label": self.suggested_label,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "pattern_summary": self.pattern_summary,
            "timestamp": self.timestamp,
        }


# ─── WATCHLIST MANAGER ──────────────────────────────────────


class WatchlistManager:
    """
    Subscribe / unsubscribe wallet addresses across chains.
    Persisted in Redis as sorted set (score = subscription timestamp).
    """

    @staticmethod
    async def add(wallet: str, chain: str = "ethereum", tags: list[str] | None = None) -> bool:
        r = await _get_redis()
        if not r:
            return False
        key = f"{WATCHLIST_KEY}:{chain}"
        ts = datetime.now(UTC).timestamp()
        await r.zadd(key, {wallet.lower(): ts})
        if tags:
            tag_key = f"{WATCHLIST_KEY}:{chain}:tags:{wallet.lower()}"
            await r.sadd(tag_key, *tags)
        return True

    @staticmethod
    async def remove(wallet: str, chain: str = "ethereum") -> bool:
        r = await _get_redis()
        if not r:
            return False
        key = f"{WATCHLIST_KEY}:{chain}"
        await r.zrem(key, wallet.lower())
        tag_key = f"{WATCHLIST_KEY}:{chain}:tags:{wallet.lower()}"
        await r.delete(tag_key)
        return True

    @staticmethod
    async def list(chain: str = "ethereum", limit: int = 1000) -> list[dict]:
        r = await _get_redis()
        if not r:
            return []
        key = f"{WATCHLIST_KEY}:{chain}"
        wallets = await r.zrevrange(key, 0, limit - 1, withscores=True)
        result = []
        for w, score in wallets:
            tag_key = f"{WATCHLIST_KEY}:{chain}:tags:{w}"
            tags = await r.smembers(tag_key) if r else set()
            result.append(
                {
                    "wallet": w,
                    "chain": chain,
                    "subscribed_at": datetime.fromtimestamp(score, tz=UTC).isoformat(),
                    "tags": list(tags),
                }
            )
        return result

    @staticmethod
    async def is_watched(wallet: str, chain: str = "ethereum") -> bool:
        r = await _get_redis()
        if not r:
            return False
        key = f"{WATCHLIST_KEY}:{chain}"
        score = await r.zscore(key, wallet.lower())
        return score is not None

    @staticmethod
    async def all_watched_set(chain: str = "ethereum") -> set[str]:
        r = await _get_redis()
        if not r:
            return set()
        key = f"{WATCHLIST_KEY}:{chain}"
        wallets = await r.zrange(key, 0, -1)
        return {w.lower() for w in wallets}


# ─── ALERT ENGINE ───────────────────────────────────────────


class AlertEngine:
    """
    Configurable rules engine that evaluates each incoming tx against
    a wallet's history and external threat intel.
    """

    def __init__(self, chain: str = "ethereum"):
        self.chain = chain
        self.rules: list[Callable[[WalletTx, list[WalletTx]], WalletAlert | None]] = [
            self._rule_large_transfer,
            self._rule_scammer_interaction,
            self._rule_first_time_dex,
            self._rule_new_approval,
            self._rule_contract_creation,
        ]

    async def evaluate(self, tx: WalletTx, history: list[WalletTx]) -> list[WalletAlert]:
        alerts: list[WalletAlert] = []
        for rule in self.rules:
            alert = rule(tx, history)
            if alert:
                alerts.append(alert)
        return alerts

    def _rule_large_transfer(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        # Value is raw — simplistic ETH assumption; real impl uses price oracle
        value_eth = tx.value
        if value_eth > (LARGE_TRANSFER_USD / 3000.0):  # assume $3K ETH as rough proxy
            return WalletAlert(
                alert_type="large_transfer",
                wallet=tx.from_addr,
                chain=tx.chain,
                tx_hash=tx.hash,
                severity="warning",
                message=f"Transfer >${LARGE_TRANSFER_USD:,.0f} equivalent detected",
                metadata={"value_eth": value_eth, "estimated_usd": value_eth * 3000},
                timestamp=tx.timestamp,
            )
        return None

    def _rule_scammer_interaction(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        # Heuristic only; real check queries Redis blocklist asynchronously in caller
        # Here we flag any contract interaction with unknown/unverified contract
        if tx.is_contract_interaction and not tx.dex_swap:
            return WalletAlert(
                alert_type="scammer_interaction_heuristic",
                wallet=tx.from_addr,
                chain=tx.chain,
                tx_hash=tx.hash,
                severity="warning",
                message="Contract interaction with non-DEX contract — verify counterparty",
                metadata={"to": tx.to_addr},
                timestamp=tx.timestamp,
            )
        return None

    def _rule_first_time_dex(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        if not tx.dex_swap:
            return None
        prior_dex = any(h.dex_swap for h in history)
        if not prior_dex:
            return WalletAlert(
                alert_type="first_time_dex",
                wallet=tx.from_addr,
                chain=tx.chain,
                tx_hash=tx.hash,
                severity="info",
                message="First-ever DEX trade detected for wallet",
                metadata={"dex": tx.dex_swap},
                timestamp=tx.timestamp,
            )
        return None

    def _rule_new_approval(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        if not tx.is_approval:
            return None
        prior_approvals = sum(1 for h in history if h.is_approval)
        if prior_approvals == 0:
            return WalletAlert(
                alert_type="new_token_approval",
                wallet=tx.from_addr,
                chain=tx.chain,
                tx_hash=tx.hash,
                severity="warning",
                message="First token approval — review spender contract",
                metadata={"spender": tx.to_addr},
                timestamp=tx.timestamp,
            )
        return None

    def _rule_contract_creation(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        if not tx.is_contract_creation:
            return None
        prior_creates = sum(1 for h in history if h.is_contract_creation)
        return WalletAlert(
            alert_type="contract_creation",
            wallet=tx.from_addr,
            chain=tx.chain,
            tx_hash=tx.hash,
            severity="info" if prior_creates > 0 else "warning",
            message="Contract deployed from watched wallet",
            metadata={"contract_address": tx.to_addr or "pending"},
            timestamp=tx.timestamp,
        )


# ─── BURST DETECTOR ─────────────────────────────────────────


class BurstDetector:
    """
    Tracks tx velocity per wallet using a rolling Redis time-series
    (simplified as a Redis sorted set of timestamps).
    """

    @staticmethod
    def _velocity_key(wallet: str, chain: str) -> str:
        return f"wallet:velocity:{chain}:{wallet.lower()}"

    @classmethod
    async def record(cls, wallet: str, chain: str, tx_hash: str) -> None:
        r = await _get_redis()
        if not r:
            return
        key = cls._velocity_key(wallet, chain)
        now = datetime.now(UTC).timestamp()
        await r.zadd(key, {tx_hash: now})
        # Trim old entries
        cutoff = now - WALLET_TX_VELOCITY_WINDOW
        await r.zremrangebyscore(key, 0, cutoff)
        await r.expire(key, WALLET_TX_VELOCITY_WINDOW + 60)

    @classmethod
    async def check(cls, wallet: str, chain: str) -> BurstSignal | None:
        r = await _get_redis()
        if not r:
            return None
        key = cls._velocity_key(wallet, chain)
        now = datetime.now(UTC).timestamp()
        cutoff = now - WALLET_TX_VELOCITY_WINDOW
        count = await r.zcount(key, cutoff, now)
        if count >= WALLET_BURST_THRESHOLD:
            severity = "critical" if count >= WALLET_BURST_THRESHOLD * 2 else "warning"
            return BurstSignal(
                wallet=wallet,
                chain=chain,
                tx_count=count,
                window_seconds=WALLET_TX_VELOCITY_WINDOW,
                severity=severity,
                timestamp=datetime.now(UTC).isoformat(),
            )
        return None


# ─── AUTO LABELER ─────────────────────────────────────────


class AutoLabeler:
    """
    Suggests entity labels by pattern matching on wallet history.
    Stores per-wallet counters in Redis hashes.
    """

    @staticmethod
    def _counter_key(wallet: str, chain: str) -> str:
        return f"wallet:counters:{chain}:{wallet.lower()}"

    @classmethod
    async def update_counters(cls, tx: WalletTx) -> None:
        r = await _get_redis()
        if not r:
            return
        key = cls._counter_key(tx.from_addr, tx.chain)
        pipe = r.pipeline()
        pipe.hincrby(key, "total_txs", 1)
        if tx.dex_swap:
            pipe.hincrby(key, "dex_txs", 1)
        if tx.is_approval:
            pipe.hincrby(key, "approvals", 1)
        if tx.is_contract_creation:
            pipe.hincrby(key, "contract_creates", 1)
        if tx.token_transfers:
            pipe.hincrby(key, "token_transfers", len(tx.token_transfers))
        pipe.expire(key, 86400 * 7)
        await pipe.execute()

    @classmethod
    async def suggest_label(cls, wallet: str, chain: str) -> LabelSuggestion | None:
        r = await _get_redis()
        if not r:
            return None
        key = cls._counter_key(wallet, chain)
        counters = await r.hgetall(key)
        if not counters:
            return None

        total = int(counters.get("total_txs", 0))
        if total < 3:
            return None

        dex = int(counters.get("dex_txs", 0))
        creates = int(counters.get("contract_creates", 0))
        int(counters.get("approvals", 0))
        tokens = int(counters.get("token_transfers", 0))

        # Simple heuristics
        if dex >= LABEL_PATTERNS["dex_trader"]["min_dex_txs"]:
            return LabelSuggestion(
                wallet=wallet,
                chain=chain,
                suggested_label="dex_trader",
                confidence=min(dex / 20, 0.95),
                reasoning=f"{dex} DEX trades in recent window",
                pattern_summary={"dex_txs": dex, "total_txs": total},
                timestamp=datetime.now(UTC).isoformat(),
            )
        if creates >= LABEL_PATTERNS["contract_deployer"]["min_contract_creates"]:
            return LabelSuggestion(
                wallet=wallet,
                chain=chain,
                suggested_label="contract_deployer",
                confidence=min(creates / 10, 0.95),
                reasoning=f"{creates} contracts deployed",
                pattern_summary={"contract_creates": creates, "total_txs": total},
                timestamp=datetime.now(UTC).isoformat(),
            )
        if tokens >= LABEL_PATTERNS["airdrop_farmer"]["min_token_receives"]:
            return LabelSuggestion(
                wallet=wallet,
                chain=chain,
                suggested_label="airdrop_farmer",
                confidence=min(tokens / 30, 0.95),
                reasoning=f"{tokens} token transfer events",
                pattern_summary={"token_transfers": tokens, "total_txs": total},
                timestamp=datetime.now(UTC).isoformat(),
            )
        return None


# ─── ALERT CHANNELS ─────────────────────────────────────────


class AlertChannel:
    """
    Distributes alerts to in-app Redis pub/sub and optional webhook callbacks.
    """

    INTEL_CHANNEL = "wallet:intel"
    ALERT_CHANNEL = "wallet:alerts"

    @staticmethod
    async def publish_intel(payload: dict) -> None:
        r = await _get_redis()
        if r:
            await r.publish(AlertChannel.INTEL_CHANNEL, json.dumps(payload))

    @staticmethod
    async def publish_alert(alert: dict) -> None:
        r = await _get_redis()
        if r:
            await r.publish(AlertChannel.ALERT_CHANNEL, json.dumps(alert))
            # Also persist for replay / dashboards
            alert_id = f"wallet:alert:{alert.get('chain')}:{alert.get('tx_hash')}:{datetime.now(UTC).timestamp()}"
            await r.setex(alert_id, ALERT_TTL_SECONDS, json.dumps(alert))

    @staticmethod
    async def dispatch_webhooks(alert: dict, webhooks: list[str]) -> None:
        if not webhooks:
            return
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
            for url in webhooks:
                with contextlib.suppress(Exception):
                    await client.post(url, json=alert)


# ─── WEBSOCKET MONITOR ──────────────────────────────────────


class WalletMonitor:
    """
    Per-chain real-time wallet monitor.
    Connects to provider WebSocket, filters for watched wallets,
    runs alert engine, burst detection, and label suggestions.
    """

    def __init__(self, chain: str = "ethereum"):
        self.chain = chain
        self.ws_url = WS_URLS.get(chain, "")
        self.rpc_url = RPC_URLS.get(chain, "")
        self.running = False
        self._callbacks: list[Callable] = []
        self.alert_engine = AlertEngine(chain)
        # In-memory watch cache (refreshed periodically)
        self._watched: set[str] = set()
        self._watch_refresh_interval = 30  # seconds

    def on_alert(self, callback: Callable):
        self._callbacks.append(callback)

    async def _refresh_watchlist(self) -> None:
        self._watched = await WatchlistManager.all_watched_set(self.chain)

    def _is_watched(self, addr: str) -> bool:
        return addr and addr.lower() in self._watched

    # ─── EVM WS HANDLER ─────────────────────────────────────

    async def _listen_evm(self) -> None:
        if not self.ws_url:
            print(f"[WalletMonitor] No WS URL for {self.chain}")
            return

        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    # Alchemy: subscribe to pending transactions
                    sub_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": [
                            "alchemy_minedTransactions",
                            {
                                "addresses": [],
                                "includeRemoved": False,
                                "hashesOnly": False,
                            },
                        ],
                    }
                    # Some Alchemy plans support address-filtered WS; fall back to general pending
                    await ws.send(json.dumps(sub_msg))

                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            result = data.get("params", {}).get("result", {})
                            if not result:
                                continue

                            tx = self._parse_evm_tx(result)
                            if self._is_watched(tx.from_addr) or self._is_watched(tx.to_addr):
                                await self._process_tx(tx)

                        except Exception:
                            continue

            except Exception as e:
                print(f"[WalletMonitor] WS error ({self.chain}): {e}, reconnecting...")
                await asyncio.sleep(5)

    def _parse_evm_tx(self, raw: dict) -> WalletTx:
        to_addr = raw.get("to") or ""
        data = raw.get("input", "")
        is_contract_creation = to_addr == "" or to_addr is None
        is_approval = is_contract_creation is False and len(data) >= 10 and data[2:10].lower() == "095ea7b3"
        dex_swap = None
        to_lower = (to_addr or "").lower()
        for name, addr in DEX_ROUTERS.items():
            if to_lower == addr.lower():
                dex_swap = {"dex": name, "router": addr}
                break

        token_transfers: list[dict] = []
        logs = raw.get("logs", [])
        for log in logs:
            topics = log.get("topics", [])
            if topics and topics[0].lower() == TRANSFER_TOPIC.lower():
                token_transfers.append(
                    {
                        "token": log.get("address", ""),
                        "from": "0x" + topics[1][-40:] if len(topics) > 1 else "",
                        "to": "0x" + topics[2][-40:] if len(topics) > 2 else "",
                        "value": log.get("data", "0x"),
                    }
                )

        return WalletTx(
            hash=raw.get("hash", ""),
            from_addr=raw.get("from", ""),
            to_addr=to_addr or raw.get("contractAddress", ""),
            value=float(raw.get("value", 0)) / 1e18,
            chain=self.chain,
            timestamp=datetime.now(UTC).isoformat(),
            gas_price=float(raw.get("gasPrice", 0)) / 1e9,
            is_contract_interaction=len(data) > 2 and not is_contract_creation,
            is_contract_creation=is_contract_creation,
            is_approval=is_approval,
            dex_swap=dex_swap,
            token_transfers=token_transfers,
            raw=raw,
        )

    # ─── SOLANA WS HANDLER ──────────────────────────────────

    async def _listen_solana(self) -> None:
        if not self.ws_url:
            print("[WalletMonitor] No WS URL for solana")
            return

        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    # Helius: subscribe to account transactions for watched wallets
                    # Helius supports accountSubscribe; batch subscribe all watched
                    watched = list(self._watched)
                    for idx, acc in enumerate(watched[:100]):  # cap 100
                        sub_msg = {
                            "jsonrpc": "2.0",
                            "id": idx + 1,
                            "method": "accountSubscribe",
                            "params": [
                                acc,
                                {"commitment": "confirmed", "encoding": "jsonParsed"},
                            ],
                        }
                        await ws.send(json.dumps(sub_msg))

                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            result = data.get("params", {}).get("result", {})
                            if not result:
                                continue
                            # Helius account notifications are sparse; try to map to tx
                            result.get("value", {})
                            acc_key = data.get("params", {}).get("subscription", "")
                            # Fetch last tx for account via RPC to enrich
                            tx = await self._fetch_solana_latest_tx(acc_key)
                            if tx:
                                await self._process_tx(tx)
                        except Exception:
                            continue

            except Exception as e:
                print(f"[WalletMonitor] Solana WS error: {e}, reconnecting...")
                await asyncio.sleep(5)

    async def _fetch_solana_latest_tx(self, account: str) -> WalletTx | None:
        if not self.rpc_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [account, {"limit": 1}],
                    },
                )
                data = resp.json()
                sigs = data.get("result", [])
                if not sigs:
                    return None
                sig = sigs[0]["signature"]
                tx_resp = await client.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            sig,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                            },
                        ],
                    },
                )
                tx_data = tx_resp.json().get("result", {})
                return self._parse_solana_tx(tx_data, sig)
        except Exception:
            return None

    def _parse_solana_tx(self, raw: dict, signature: str) -> WalletTx:
        meta = raw.get("meta", {})
        tx_inner = raw.get("transaction", {})
        msg = tx_inner.get("message", {})
        accts = msg.get("accountKeys", [])
        from_addr = accts[0] if accts else ""
        to_addr = accts[1] if len(accts) > 1 else ""

        # Detect DEX via program IDs in instructions
        dex_swap = None
        instructions = msg.get("instructions", [])
        for ix in instructions:
            prog = ix.get("programId", "")
            for name, addr in DEX_ROUTERS.items():
                if prog == addr:
                    dex_swap = {"dex": name, "router": addr}
                    break

        # Token transfers from meta pre/post token balances
        token_transfers: list[dict] = []
        meta.get("preTokenBalances", [])
        post = meta.get("postTokenBalances", [])
        # Simplified diff
        for p in post:
            token_transfers.append(
                {
                    "token": p.get("mint", ""),
                    "owner": p.get("owner", ""),
                    "ui_amount": p.get("uiTokenAmount", {}).get("uiAmountString", "0"),
                }
            )

        return WalletTx(
            hash=signature,
            from_addr=from_addr,
            to_addr=to_addr,
            value=float(meta.get("fee", 0)) / 1e9,
            chain="solana",
            timestamp=datetime.now(UTC).isoformat(),
            is_contract_interaction=len(instructions) > 0,
            is_contract_creation=False,
            is_approval=False,
            dex_swap=dex_swap,
            token_transfers=token_transfers,
            raw=raw,
        )

    # ─── CORE PROCESSING ──────────────────────────────────────

    async def _process_tx(self, tx: WalletTx) -> None:
        # 1. Persist history
        r = await _get_redis()
        if r:
            key = f"{WALLET_HISTORY_KEY_PREFIX}:{tx.chain}:{tx.from_addr.lower()}"
            await r.lpush(key, json.dumps(tx.to_dict()))
            await r.ltrim(key, 0, 999)
            await r.expire(key, 86400 * 2)

        # 2. Fetch recent history for rule context
        history: list[WalletTx] = []
        if r:
            key = f"{WALLET_HISTORY_KEY_PREFIX}:{tx.chain}:{tx.from_addr.lower()}"
            raw_history = await r.lrange(key, 0, 49)
            for h in raw_history:
                try:
                    d = json.loads(h)
                    history.append(
                        WalletTx(
                            hash=d["hash"],
                            from_addr=d["from"],
                            to_addr=d["to"],
                            value=d["value"],
                            chain=d["chain"],
                            timestamp=d["timestamp"],
                            is_approval=d.get("is_approval", False),
                            dex_swap=d.get("dex_swap"),
                            token_transfers=d.get("token_transfers", []),
                        )
                    )
                except Exception:
                    pass

        # 3. Run alert engine
        alerts = await self.alert_engine.evaluate(tx, history)

        # 4. Blocklist check (async against Redis)
        if r and tx.to_addr:
            is_blocked = await r.sismember(SCAMMER_SET_KEY, tx.to_addr.lower())
            if is_blocked:
                alerts.append(
                    WalletAlert(
                        alert_type="known_scammer_interaction",
                        wallet=tx.from_addr,
                        chain=tx.chain,
                        tx_hash=tx.hash,
                        severity="critical",
                        message=f"Interaction with known scammer {tx.to_addr}",
                        metadata={"scammer": tx.to_addr},
                        timestamp=tx.timestamp,
                    )
                )

        # 5. Burst detection
        await BurstDetector.record(tx.from_addr, tx.chain, tx.hash)
        burst = await BurstDetector.check(tx.from_addr, tx.chain)
        if burst:
            alerts.append(
                WalletAlert(
                    alert_type="burst",
                    wallet=tx.from_addr,
                    chain=tx.chain,
                    tx_hash=tx.hash,
                    severity=burst.severity,
                    message=f"Unusual tx velocity: {burst.tx_count} txs in {burst.window_seconds}s",
                    metadata=burst.to_dict(),
                    timestamp=tx.timestamp,
                )
            )

        # 6. Auto-labeler
        await AutoLabeler.update_counters(tx)
        label_suggestion = await AutoLabeler.suggest_label(tx.from_addr, tx.chain)

        # 7. Publish intel + alerts
        intel = {
            "type": "intel",
            "tx": tx.to_dict(),
            "wallet": tx.from_addr,
            "chain": tx.chain,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await AlertChannel.publish_intel(intel)

        for alert in alerts:
            alert_dict = alert.to_dict()
            await AlertChannel.publish_alert(alert_dict)
            # Callbacks
            for cb in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(alert_dict)
                    else:
                        cb(alert_dict)
                except Exception:
                    pass

        if label_suggestion:
            await AlertChannel.publish_intel(label_suggestion.to_dict())

    # ─── REFRESH LOOP ─────────────────────────────────────────

    async def _watchlist_refresh_loop(self) -> None:
        while self.running:
            await self._refresh_watchlist()
            await asyncio.sleep(self._watch_refresh_interval)

    # ─── PUBLIC API ─────────────────────────────────────────

    async def start(self) -> None:
        self.running = True
        await self._refresh_watchlist()
        print(f"[WalletMonitor] Starting {self.chain} monitor...")

        # Launch watchlist refresher
        _ = asyncio.create_task(self._watchlist_refresh_loop())

        if self.chain == "solana":
            await self._listen_solana()
        else:
            await self._listen_evm()

    async def stop(self) -> None:
        self.running = False

    def get_stats(self) -> dict:
        return {
            "chain": self.chain,
            "watched_count": len(self._watched),
            "running": self.running,
        }


# ─── MANAGER ────────────────────────────────────────────────


class WalletMonitorManager:
    """
    Manages WalletMonitor instances across multiple chains.
    Provides unified watchlist API and dispatches to all active monitors.
    """

    def __init__(self):
        self.monitors: dict[str, WalletMonitor] = {}
        self._webhooks: list[str] = []

    def register_webhook(self, url: str) -> None:
        self._webhooks.append(url)

    async def start_chain(self, chain: str) -> None:
        if chain in self.monitors:
            return
        monitor = WalletMonitor(chain)
        monitor.on_alert(self._alert_handler)
        self.monitors[chain] = monitor
        _ = asyncio.create_task(monitor.start())

    async def stop_chain(self, chain: str) -> None:
        mon = self.monitors.pop(chain, None)
        if mon:
            await mon.stop()

    async def stop_all(self) -> None:
        for chain in list(self.monitors.keys()):
            await self.stop_chain(chain)

    async def _alert_handler(self, alert: dict) -> None:
        # Default: publish to webhooks
        if self._webhooks:
            await AlertChannel.dispatch_webhooks(alert, self._webhooks)

    async def add_watch(self, wallet: str, chain: str = "ethereum", tags: list[str] | None = None) -> dict:
        ok = await WatchlistManager.add(wallet, chain, tags)
        # Refresh local cache if monitor already running
        mon = self.monitors.get(chain)
        if mon:
            await mon._refresh_watchlist()
        return {"success": ok, "wallet": wallet, "chain": chain}

    async def remove_watch(self, wallet: str, chain: str = "ethereum") -> dict:
        ok = await WatchlistManager.remove(wallet, chain)
        mon = self.monitors.get(chain)
        if mon:
            await mon._refresh_watchlist()
        return {"success": ok, "wallet": wallet, "chain": chain}

    async def list_watches(self, chain: str = "ethereum", limit: int = 1000) -> list[dict]:
        return await WatchlistManager.list(chain, limit)

    def get_stats(self) -> dict[str, Any]:
        return {chain: mon.get_stats() for chain, mon in self.monitors.items()}


# ─── EXPORTS ──────────────────────────────────────────────

__all__ = [
    "AlertChannel",
    "AlertEngine",
    "AutoLabeler",
    "BurstDetector",
    "BurstSignal",
    "LabelSuggestion",
    "WalletAlert",
    "WalletMonitor",
    "WalletMonitorManager",
    "WalletTx",
    "WatchlistManager",
]
