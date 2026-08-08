"""Wallet Monitor — Per-chain WebSocket Monitor."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import websockets

from app.core.logging import get_logger
from app.domains.wallet.monitor.alert_engine import AlertEngine
from app.domains.wallet.monitor.burst import BurstDetector
from app.domains.wallet.monitor.channel import AlertChannel
from app.domains.wallet.monitor.config import (
    CONFIG,
    DEX_ROUTERS,
    RPC_URLS,
    SCAMMER_SET_KEY,
    TRANSFER_TOPIC,
    WS_URLS,
    get_redis,
)
from app.domains.wallet.monitor.labeler import AutoLabeler
from app.domains.wallet.monitor.models import WalletAlert, WalletTx
from app.domains.wallet.monitor.watchlist import WatchlistManager

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


class WalletMonitor:
    """Per-chain real-time wallet monitor with WebSocket connection."""

    def __init__(self, chain: str = "ethereum") -> None:
        self.chain = chain
        self.ws_url = WS_URLS.get(chain, "")
        self.rpc_url = RPC_URLS.get(chain, "")
        self.running = False
        self._callbacks: list[Callable] = []
        self.alert_engine = AlertEngine(chain)
        self._watched: set[str] = set()
        self._watch_refresh_interval = 30

    def on_alert(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    async def _refresh_watchlist(self) -> None:
        self._watched = await WatchlistManager.all_watched_set(self.chain)

    def _is_watched(self, addr: str) -> bool:
        return addr and addr.lower() in self._watched

    # ─── EVM ────────────────────────────────────────────────────
    async def _listen_evm(self) -> None:
        if not self.ws_url:
            logger.warning("evm_no_ws_url chain=%s", self.chain)
            return
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "eth_subscribe",
                                "params": [
                                    "alchemy_minedTransactions",
                                    {"addresses": [], "includeRemoved": False, "hashesOnly": False},
                                ],
                            }
                        )
                    )
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
                        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                            continue
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
                websockets.ConnectionClosed,
            ) as e:
                logger.warning("evm_ws_error chain=%s error=%s", self.chain, e)
                await asyncio.sleep(5)

    def _parse_evm_tx(self, raw: dict[str, Any]) -> WalletTx:
        to_addr = raw.get("to") or ""
        data = raw.get("input", "")
        is_contract_creation = not to_addr
        is_approval = not is_contract_creation and len(data) >= 10 and data[2:10].lower() == "095ea7b3"
        dex_swap = None
        to_lower = to_addr.lower()
        for name, addr in DEX_ROUTERS.items():
            if to_lower == addr.lower():
                dex_swap = {"dex": name, "router": addr}
                break
        token_transfers: list[dict[str, Any]] = []
        for log in raw.get("logs", []):
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

    # ─── SOLANA ────────────────────────────────────────────────
    async def _listen_solana(self) -> None:
        if not self.ws_url:
            logger.warning("solana_no_ws_url")
            return
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    watched = list(self._watched)
                    for idx, acc in enumerate(watched[:100]):
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": idx + 1,
                                    "method": "accountSubscribe",
                                    "params": [acc, {"commitment": "confirmed", "encoding": "jsonParsed"}],
                                }
                            )
                        )
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            if not data.get("params", {}).get("result", {}):
                                continue
                            acc_key = data.get("params", {}).get("subscription", "")
                            tx = await self._fetch_solana_latest_tx(acc_key)
                            if tx:
                                await self._process_tx(tx)
                        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                            continue
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
                websockets.ConnectionClosed,
            ) as e:
                logger.warning("solana_ws_error error=%s", e)
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
                sigs = (resp.json()).get("result", [])
                if not sigs:
                    return None
                sig = sigs[0]["signature"]
                tx_resp = await client.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    },
                )
                return self._parse_solana_tx(tx_resp.json().get("result", {}), sig)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            return None

    def _parse_solana_tx(self, raw: dict[str, Any], signature: str) -> WalletTx:
        meta = raw.get("meta", {})
        tx_inner = raw.get("transaction", {})
        msg = tx_inner.get("message", {})
        accts = msg.get("accountKeys", [])
        from_addr = accts[0] if accts else ""
        to_addr = accts[1] if len(accts) > 1 else ""
        dex_swap = None
        for ix in msg.get("instructions", []):
            prog = ix.get("programId", "")
            for name, addr in DEX_ROUTERS.items():
                if prog == addr:
                    dex_swap = {"dex": name, "router": addr}
                    break
        token_transfers: list[dict[str, Any]] = []
        for p in meta.get("postTokenBalances", []):
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
            is_contract_interaction=len(msg.get("instructions", [])) > 0,
            is_contract_creation=False,
            is_approval=False,
            dex_swap=dex_swap,
            token_transfers=token_transfers,
            raw=raw,
        )

    # ─── CORE PROCESSING ──────────────────────────────────────
    async def _process_tx(self, tx: WalletTx) -> None:
        r = await get_redis()
        if r:
            key = f"{CONFIG['WALLET_HISTORY_KEY_PREFIX']}:{tx.chain}:{tx.from_addr.lower()}"
            await r.lpush(key, json.dumps(tx.to_dict()))
            await r.ltrim(key, 0, 999)
            await r.expire(key, 86400 * 2)
        history: list[WalletTx] = []
        if r:
            key = f"{CONFIG['WALLET_HISTORY_KEY_PREFIX']}:{tx.chain}:{tx.from_addr.lower()}"
            for h in await r.lrange(key, 0, 49):
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
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass
        alerts = await self.alert_engine.evaluate(tx, history)
        if r and tx.to_addr and await r.sismember(SCAMMER_SET_KEY, tx.to_addr.lower()):
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
        await AutoLabeler.update_counters(tx)
        label = await AutoLabeler.suggest_label(tx.from_addr, tx.chain)
        intel = {
            "type": "intel",
            "tx": tx.to_dict(),
            "wallet": tx.from_addr,
            "chain": tx.chain,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await AlertChannel.publish_intel(intel)
        for alert in alerts:
            d = alert.to_dict()
            await AlertChannel.publish_alert(d)
            for cb in self._callbacks:
                with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                    await cb(d) if asyncio.iscoroutinefunction(cb) else cb(d)
        if label:
            await AlertChannel.publish_intel(label.to_dict())

    async def _watchlist_refresh_loop(self) -> None:
        while self.running:
            await self._refresh_watchlist()
            await asyncio.sleep(self._watch_refresh_interval)

    async def start(self) -> None:
        self.running = True
        await self._refresh_watchlist()
        logger.info("starting_monitor chain=%s", self.chain)
        asyncio.create_task(self._watchlist_refresh_loop())
        if self.chain == "solana":
            await self._listen_solana()
        else:
            await self._listen_evm()

    async def stop(self) -> None:
        self.running = False

    def get_stats(self) -> dict[str, Any]:
        return {"chain": self.chain, "watched_count": len(self._watched), "running": self.running}
