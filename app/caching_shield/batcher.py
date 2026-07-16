"""
Aggressive Caching Shield - JSON-RPC Batch Request Grouper

Groups individual RPC calls into batch JSON-RPC requests (where supported).
Not all free tier providers support batching, but Helius, QuickNode, and
Alchemy do for Solana. For EVM chains, batch support varies.

Strategy:
  - Accumulate calls for up to 50ms window
  - Maximum 20 requests per batch
  - Providers that don't support batching fall through to individual calls
  - Results matched back to callers by request ID
  - Cache-aware: skip batching for cache hits (routed to cache first)

Free tier impact: A single batch request counts as 1 request toward limits
but can contain up to 20 sub-requests. This dramatically reduces RPC calls
when fetching data for multiple tokens/wallets simultaneously.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rpc_batcher")

# Maximum requests per batch (provider limits are typically 20-100)
MAX_BATCH_SIZE = 20

# Maximum wait time to accumulate before dispatching
BATCH_WINDOW_MS = 50

# Providers known to support JSON-RPC batching
BATCH_CAPABLE_PROVIDERS = {
    "helius",
    "quicknode",
    "alchemy",
    "drpc",
    # EVM
    "ethereum_publicnode",
    "llama_rpc",
    "1rpc",
    "blastapi",
}

# Providers that DON'T support batching
NO_BATCH_PROVIDERS = {"anvil", "publicnode"}


@dataclass
class BatchRequest:
    """A single request within a batch."""

    id: int
    method: str
    params: list[Any]


@dataclass
class BatchResult:
    """Result for a single request within a batch."""

    id: int
    result: Any = None
    error: str | None = None


class RpcBatcher:
    """Accumulates RPC requests and dispatches them as batch JSON-RPC calls.

    Usage:
        batcher = RpcBatcher(rpc_query_fn)
        result = await batcher.add("getBalance", [address], provider="helius")
        # Internally: accumulates -> after 50ms or 20 requests -> dispatches batch
    """

    def __init__(self, rpc_query_fn: Callable[..., Awaitable]):
        """
        Args:
            rpc_query_fn: async function(provider, method, params) -> result
                         This is called to execute the actual batch.
        """
        self._query_fn = rpc_query_fn
        self._pending: dict[str, list[BatchRequest]] = {}  # provider -> pending
        self._futures: dict[str, dict[int, asyncio.Future]] = {}  # provider -> {id: future}
        self._timers: dict[str, asyncio.Task] = {}  # provider -> timer task
        self._lock = asyncio.Lock()
        self._next_id = 0

        # Stats
        self.batches_dispatched = 0
        self.total_batched = 0
        self.total_individual = 0

    async def add(self, method: str, params: list[Any], provider: str = "helius") -> Any:
        """Add a request to the batch queue. Returns the result when dispatched.

        If the provider doesn't support batching, falls through to individual query.
        """
        if provider in NO_BATCH_PROVIDERS:
            self.total_individual += 1
            return await self._query_fn(provider, method, params)

        request_id = await self._enqueue(provider, method, params)
        future = self._futures[provider][request_id]

        try:
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except TimeoutError:
            logger.warning(
                f"Batch request timed out for {provider}/{method}, falling back to direct"
            )
            self.total_individual += 1
            return await self._query_fn(provider, method, params)

    async def _enqueue(self, provider: str, method: str, params: list[Any]) -> int:
        """Add request to pending queue and return request ID."""
        async with self._lock:
            self._next_id += 1
            req_id = self._next_id

            req = BatchRequest(id=req_id, method=method, params=params)

            if provider not in self._pending:
                self._pending[provider] = []
                self._futures[provider] = {}

            self._pending[provider].append(req)
            self._futures[provider][req_id] = asyncio.Future()

            # If this is the first item, start the dispatch timer
            if len(self._pending[provider]) == 1:
                self._timers[provider] = asyncio.create_task(self._dispatch_after_delay(provider))
            # If we hit max batch size, dispatch immediately
            elif len(self._pending[provider]) >= MAX_BATCH_SIZE:
                if provider in self._timers:
                    self._timers[provider].cancel()
                asyncio.create_task(self._dispatch(provider))

            return req_id

    async def _dispatch_after_delay(self, provider: str):
        """Wait BATCH_WINDOW_MS then dispatch."""
        try:
            await asyncio.sleep(BATCH_WINDOW_MS / 1000.0)
            await self._dispatch(provider)
        except asyncio.CancelledError:
            pass

    async def _dispatch(self, provider: str):
        """Send accumulated requests as a single JSON-RPC batch."""
        async with self._lock:
            requests = self._pending.pop(provider, [])
            futures = self._futures.pop(provider, {})
            self._timers.pop(provider, None)

        if not requests:
            return

        batch_payload = []
        for req in requests:
            batch_payload.append(
                {
                    "jsonrpc": "2.0",
                    "id": req.id,
                    "method": req.method,
                    "params": req.params,
                }
            )

        self.batches_dispatched += 1
        self.total_batched += len(requests)

        try:
            results = await self._query_fn(provider, batch_payload, is_batch=True)

            # Match results back to futures
            if isinstance(results, list):
                for item in results:
                    rid = item.get("id")
                    if rid is not None and rid in futures:
                        if "error" in item:
                            futures[rid].set_exception(
                                Exception(item["error"].get("message", "RPC error"))
                            )
                        else:
                            futures[rid].set_result(item.get("result"))
                    elif rid is not None:
                        logger.debug(f"Orphan batch result for id={rid}")

            # Resolve any unmatched futures with None
            for rid, fut in futures.items():  # noqa: B007
                if not fut.done():
                    fut.set_result(None)
        except Exception as e:
            # Batch failed - fail all futures
            for rid, fut in futures.items():  # noqa: B007
                if not fut.done():
                    fut.set_exception(e)

    async def stats(self) -> dict:
        """Return batcher statistics."""
        async with self._lock:
            pending_count = sum(len(v) for v in self._pending.values())
            pending_futures = sum(len(v) for v in self._futures.values())
        return {
            "batches_dispatched": self.batches_dispatched,
            "total_batched": self.total_batched,
            "total_individual": self.total_individual,
            "pending_requests": pending_count,
            "pending_futures": pending_futures,
            "batch_saving_ratio": round(self.total_batched / max(1, self.batches_dispatched), 1),
        }
