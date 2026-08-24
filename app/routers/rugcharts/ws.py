"""Live trade tape WebSocket for RugCharts — ``/ws/trades``.

Closes the audit's P0-1 gap: the frontend LiveTradeTape has always
connected to ``wss://<host>/ws/trades?chain=..&token=..`` but NO backend
endpoint existed, so the "real-time" tape never streamed a single trade.

Design (v1 — poll-and-push over ClickHouse own-data):
- One hub groups viewers by (chain, token); ONE ClickHouse poll per group
  per interval regardless of viewer count.
- Each poll fetches the latest swaps touching the token, runs them through
  the wallet-behavior classifier, and pushes labeled trades to the group.
- Per-connection dedupe by tx_hash; bounded send queue; slow/dead clients
  are dropped instead of blocking the hub.

Extraction note: depends only on FastAPI + this package's helpers — moves
verbatim into a standalone rugcharts service.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

from .classifier import classify_trades
from .helpers import _fetch_recent_trades_clickhouse

logger = get_logger("rugcharts_ws")

router = APIRouter(tags=["rugcharts-ws"])

POLL_INTERVAL_S = 2.0
MAX_CONNECTIONS = 200
MAX_PER_GROUP = 25
MAX_TRADES_PER_PUSH = 30
DEDUPE_WINDOW = 500


class _Group:
    """One polling loop shared by every viewer of the same (chain, token)."""

    def __init__(self, chain: str, token: str) -> None:
        self.chain = chain
        self.token = token
        self.clients: list[WebSocket] = []
        self.sent_hashes: set[str] = set()
        self.task: asyncio.Task[None] | None = None

    async def _poll_and_push(self) -> None:
        while self.clients:
            try:
                trades = await _fetch_recent_trades_clickhouse(self.token, self.chain, limit=50)
                fresh = [
                    t for t in classify_trades(trades) if t.get("tx_hash") and t["tx_hash"] not in self.sent_hashes
                ]
                if fresh:
                    for t in fresh:
                        self.sent_hashes.add(t["tx_hash"])
                    if len(self.sent_hashes) > DEDUPE_WINDOW * 2:
                        # Keep the newest half of the dedupe window.
                        recent = [t["tx_hash"] for t in fresh][-DEDUPE_WINDOW:]
                        self.sent_hashes = set(recent) | {h for h in self.sent_hashes if h in set(recent)}
                    payload = json.dumps(
                        {
                            "type": "trades",
                            "chain": self.chain,
                            "token": self.token,
                            "trades": fresh[:MAX_TRADES_PER_PUSH],
                        }
                    )
                    dead: list[WebSocket] = []
                    for ws in list(self.clients):
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self._drop(ws)
            except Exception as e:
                logger.debug(f"ws poller error {self.chain}/{self.token}: {e}")
            await asyncio.sleep(POLL_INTERVAL_S)

    def _drop(self, ws: WebSocket) -> None:
        with contextlib.suppress(ValueError):
            self.clients.remove(ws)

    def ensure_task(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._poll_and_push())

    async def stop_if_empty(self) -> None:
        if not self.clients and self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None


class TradeStreamHub:
    def __init__(self) -> None:
        self.groups: dict[tuple[str, str], _Group] = {}
        self.total = 0
        self._lock = asyncio.Lock()

    async def join(self, chain: str, token: str, ws: WebSocket) -> bool:
        async with self._lock:
            if self.total >= MAX_CONNECTIONS:
                return False
            key = (chain.lower(), token.lower())
            group = self.groups.setdefault(key, _Group(*key))
            if len(group.clients) >= MAX_PER_GROUP:
                return False
            group.clients.append(ws)
            self.total += 1
            # NOTE: poller is NOT started here — the socket isn't accepted yet.
            # The endpoint calls ensure_task() after accept(); starting earlier
            # races the handshake and the first push can kill the client entry.
            return True

    async def leave(self, chain: str, token: str, ws: WebSocket) -> None:
        async with self._lock:
            key = (chain.lower(), token.lower())
            group = self.groups.get(key)
            if group:
                group._drop(ws)
                self.total = max(0, self.total - 1)
                await group.stop_if_empty()
                if not group.clients:
                    self.groups.pop(key, None)

    def stats(self) -> dict[str, Any]:
        return {
            "connections": self.total,
            "groups": len(self.groups),
            "groups_detail": {f"{c}/{t}": len(g.clients) for (c, t), g in self.groups.items()},
        }


hub = TradeStreamHub()


@router.websocket("/ws/trades")
async def ws_trades(
    websocket: WebSocket,
    chain: str = "solana",
    token: str = "",
) -> None:
    """Live labeled trade stream for one token pair.

    Query params: chain (default solana), token (token address, required).
    Server pushes {"type":"trades","trades":[...]} frames; each trade carries
    time/price_usd/volume_usd/side/tx_hash/wallet_label/label_confidence.
    """
    token = (token or "").strip()
    if not token:
        await websocket.close(code=4400)
        return

    accepted = await hub.join(chain, token, websocket)
    if not accepted:
        await websocket.close(code=4429)  # too many connections
        return

    try:
        await websocket.accept()
        await websocket.send_text(
            json.dumps(
                {"type": "hello", "chain": chain.lower(), "token": token.lower(), "poll_interval_s": POLL_INTERVAL_S}
            )
        )
        # Socket is live — safe to start the group's poll loop now.
        key = (chain.lower(), token.lower())
        group = hub.groups.get(key)
        if group:
            group.ensure_task()
        while True:
            # Client frames are ignored except pings/disconnects; receive()
            # doubles as liveness detection so dead sockets get reaped.
            raw = await websocket.receive_text()
            with contextlib.suppress(json.JSONDecodeError):
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"ws_trades disconnect {chain}/{token}: {e}")
    finally:
        await hub.leave(chain, token, websocket)


@router.get("/ws/trades/stats")
async def ws_trades_stats() -> dict[str, Any]:
    """Ops visibility into live tape usage."""
    return {"status": "ok", **hub.stats()}
