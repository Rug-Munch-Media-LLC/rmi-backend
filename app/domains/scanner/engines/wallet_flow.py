"""
C1 — Wallet-to-Wallet Flow Visualization (v1) — Arkham replacement
====================================================================
Builds a labeled, USD-denominated directed graph of fund flows around a
wallet, entirely from our own ClickHouse data:

  - ``wallet_memory.evm_transfers`` — ERC-20 transfer edges (sender → receiver)
  - ``wallet_memory.wallet_labels``  — node labels (39.4M curated)
  - ``wallet_memory.dex_events``     — contract detection (node type)
  - F2 price oracle (``price_oracle.normalize_amount``) — USD normalization

Algorithm (spec C1):
  1. Hop 1 — query every evm_transfer FROM/TO the root wallet, aggregate per
     counterparty.
  2. Hop 2 — for the top-10 counterparties by USD volume, repeat the query
     (excluding edges that touch the root wallet to avoid duplicates).
  3. Batch label lookup for every node in the graph.
  4. Node types: CEX / DEX / bridge / mixer from wallet_labels; contract from
     dex_events contract addresses; otherwise EOA.
  5. Output: ``{nodes: [...], edges: [...]}`` with net USD and tx counts.

Every ClickHouse query is bounded by ``max_execution_time`` (time-boxed
traversal — see Cross-Cutting Principles). Edges carry sample tx hashes
(evidence output principle).

Caching:
  - Redis key ``wallet_flow:{chain}:{wallet}:{hops}`` with 5m TTL (graceful
    fallback when Redis is unavailable).
  - In-process dict mirror (2 min) consistent with the other own-data engines.

Usage:
    from app.domains.scanner.engines.wallet_flow import build_flow_graph

    graph = await build_flow_graph("0x...", "ethereum", hops=2, max_nodes=60)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.core.logging import get_logger
from app.domains.databus.providers.chains.chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_TTL_SECONDS = 300  # 5 minutes
REDIS_CACHE_PREFIX = "wallet_flow"
_FLOW_CACHE: dict[str, dict[str, Any]] = {}
_FLOW_CACHE_TTL = 120  # 2 minutes in-process mirror
_FLOW_CACHE_MAX = 200

# ── Traversal limits (time-boxing) ─────────────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10
_HOP1_LIMIT = 500  # transfers involving the root wallet
_HOP2_LIMIT = 200  # transfers per expanded counterparty
_TOP_COUNTERPARTIES = 10
_DEFAULT_MAX_NODES = 60

# ── Classification keywords ───────────────────────────────────────────────
_CEX_KEYWORDS = (
    "binance",
    "coinbase",
    "kraken",
    "okx",
    "okex",
    "bybit",
    "huobi",
    "gate.io",
    "gateio",
    "kucoin",
    "bitfinex",
    "mexc",
    "crypto.com",
    "bitget",
    "upbit",
    "bithumb",
    "gemini",
    "bitstamp",
    "exchange",
    "cex",
)
_DEX_KEYWORDS = (
    "uniswap",
    "pancake",
    "sushiswap",
    "curve",
    "balancer",
    "aave",
    "router",
    "dex",
    "amm",
    "1inch",
    "camelot",
    "trader joe",
    "spooky",
    "quickswap",
    "pancakeswap",
)
_BRIDGE_KEYWORDS = (
    "bridge",
    "wormhole",
    "axelar",
    "multichain",
    "layerzero",
    "hop protocol",
    "polygon bridge",
    "arbitrum bridge",
)
_MIXER_KEYWORDS = (
    "mixer",
    "tornado",
    "blender",
    "coinjoin",
    "wasabi",
    "privacy pool",
)

# Zero address — skip as a counterparty (burn/mint artifacts)
_ZERO_ADDR = "0x0000000000000000000000000000000000000000"


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def build_flow_graph(
    wallet: str,
    chain: str,
    hops: int = 2,
    max_nodes: int = _DEFAULT_MAX_NODES,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Build a labeled USD-denominated fund-flow graph around a wallet.

    Args:
        wallet: Root wallet address (lowercased internally).
        chain: Chain name (ethereum, bsc, arbitrum, ...).
        hops: Traversal depth — 1 (root's counterparties) or 2 (expand top-10).
        max_nodes: Hard cap on graph nodes (prevents runaway expansion).
        force_fresh: Bypass all caches.

    Returns:
        Dict with::

            {
                "check": "wallet_flow",
                "result": "scanned" | "unavailable" | "error",
                "wallet": "0x...",
                "chain": "ethereum",
                "hops": 2,
                "nodes": [{"id", "type", "label", "net_usd", "tx_count"}],
                "edges": [{"from", "to", "token", "usd", "tx_count", "tx_hashes"}],
                "node_count": int,
                "edge_count": int,
                "note": "...",
            }
    """
    wallet = wallet.lower().strip()
    chain_norm = _normalize_chain(chain)
    hops = max(1, min(int(hops), 2))  # C1 restricts to 1-2 hops
    max_nodes = max(2, min(int(max_nodes), 200))
    _start = time.monotonic()

    if not wallet or not chain_norm:
        return _error_result(wallet, chain_norm, hops, "wallet and chain are required")

    # ── 1. Redis fast cache ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{wallet}:{hops}:{max_nodes}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("wallet_flow_redis_hit", key=cache_key)
            return cached

    # ── 2. In-process mirror cache ──
    if not force_fresh and cache_key in _FLOW_CACHE:
        entry = _FLOW_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _FLOW_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_graph(wallet, chain_norm, hops, max_nodes)
    except Exception as exc:
        logger.debug("wallet_flow_query_failed: %s", exc)
        result = _unavailable_result(wallet, chain_norm, hops, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _FLOW_CACHE[cache_key] = result
        if len(_FLOW_CACHE) > _FLOW_CACHE_MAX:
            _FLOW_CACHE.pop(next(iter(_FLOW_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "wallet_flow_latency",
        ms=round(_elapsed_ms, 1),
        wallet=wallet[:10],
        chain=chain_norm,
        hops=hops,
        nodes=result.get("node_count", 0),
        edges=result.get("edge_count", 0),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_graph(wallet: str, chain: str, hops: int, max_nodes: int) -> dict[str, Any]:
    """Run the hop-1 / hop-2 queries and assemble the graph payload."""
    # ── Hop 1: transfers involving the root wallet ──
    hop1_rows = await _run_sync(_query_wallet_transfers, wallet, chain, _HOP1_LIMIT)
    if not hop1_rows:
        return _unavailable_result(wallet, chain, hops, reason="no transfer history")

    # Aggregates: (from, to, token) -> {"raw": sum, "count": n, "hashes": [...]}
    edge_raw: dict[tuple[str, str, str], dict[str, Any]] = {}
    token_raw_total: dict[str, float] = {}

    def _accumulate_rows(rows: list[tuple[Any, ...]]) -> None:
        for row in rows:
            from_addr = str(row[0] or "").lower()
            to_addr = str(row[1] or "").lower()
            token = str(row[2] or "").lower()
            try:
                raw = float(row[3] or 0)
            except (ValueError, TypeError):
                raw = 0.0
            tx_hash = str(row[4] or "")
            if raw <= 0 or not from_addr or not to_addr:
                continue
            if from_addr == _ZERO_ADDR or to_addr == _ZERO_ADDR:
                continue
            key = (from_addr, to_addr, token)
            agg = edge_raw.setdefault(key, {"raw": 0.0, "count": 0, "hashes": []})
            agg["raw"] += raw
            agg["count"] += 1
            if tx_hash and len(agg["hashes"]) < 3:
                agg["hashes"].append(tx_hash)
            token_raw_total[token] = token_raw_total.get(token, 0.0) + raw

    _accumulate_rows(hop1_rows)

    # ── Hop 2: expand top-N counterparties (skip root-touching edges) ──
    counterparties = _rank_counterparties(wallet, edge_raw)
    top_cps = [cp for cp, _usd in counterparties[:_TOP_COUNTERPARTIES]]
    expanded_nodes: set[str] = {wallet}
    if hops >= 2 and top_cps:
        # Parallel, bounded per-counterparty queries
        tasks = [_run_sync(_query_counterparty_transfers, cp, wallet, chain, _HOP2_LIMIT) for cp in top_cps]
        cp_rows_lists = await asyncio.gather(*tasks, return_exceptions=True)
        for cp, rows_result in zip(top_cps, cp_rows_lists, strict=False):
            if isinstance(rows_result, BaseException) or not rows_result:
                continue
            _accumulate_rows(rows_result)
            expanded_nodes.add(cp)
            for row in rows_result:
                from_addr = str(row[0] or "").lower()
                to_addr = str(row[1] or "").lower()
                if from_addr != _ZERO_ADDR:
                    expanded_nodes.add(from_addr)
                if to_addr != _ZERO_ADDR:
                    expanded_nodes.add(to_addr)
                if len(expanded_nodes) >= max_nodes:
                    break

    # ── USD conversion (one oracle call per unique token, linear allocation) ──
    token_usd_total: dict[str, float] = {}
    for token, raw_total in token_raw_total.items():
        usd = await _normalize_token_usd(token, chain, raw_total)
        if usd is not None:
            token_usd_total[token] = usd

    edges: list[dict[str, Any]] = []
    node_net: dict[str, float] = {}
    node_tx_count: dict[str, int] = {}

    for (from_addr, to_addr, token), agg in edge_raw.items():
        raw = agg["raw"]
        token_total = token_raw_total.get(token, 0.0)
        token_usd = token_usd_total.get(token, 0.0)
        usd = token_usd * (raw / token_total) if token_total > 0 else 0.0
        usd = round(usd, 2)
        if usd <= 0 and agg["count"] == 0:
            continue
        edges.append(
            {
                "from": from_addr,
                "to": to_addr,
                "token": token,
                "usd": usd,
                "tx_count": agg["count"],
                "tx_hashes": agg["hashes"][:3],
            }
        )
        node_net[to_addr] = node_net.get(to_addr, 0.0) + usd
        node_net[from_addr] = node_net.get(from_addr, 0.0) - usd
        node_tx_count[to_addr] = node_tx_count.get(to_addr, 0) + agg["count"]
        node_tx_count[from_addr] = node_tx_count.get(from_addr, 0) + agg["count"]

    # ── Nodes: collect all addresses, cap at max_nodes ──
    node_ids = list(dict.fromkeys([wallet, *node_net.keys()]))[:max_nodes]
    node_ids = [n for n in node_ids if n and n != _ZERO_ADDR]

    # ── Batch labels + contract detection ──
    labels_map, contract_addresses = await _enrich_nodes(node_ids, chain)

    nodes: list[dict[str, Any]] = []
    for node_id in node_ids:
        node_type, label = _classify_node(node_id, labels_map.get(node_id, []), contract_addresses)
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "label": label or None,
                "net_usd": round(node_net.get(node_id, 0.0), 2),
                "tx_count": node_tx_count.get(node_id, 0),
            }
        )

    # Deduplicate edges to keep payload lean (aggregate identical (from,to,token))
    dedup_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in edges:
        key = (e["from"], e["to"], e["token"])
        if key in dedup_edges:
            dedup_edges[key]["usd"] = round(dedup_edges[key]["usd"] + e["usd"], 2)
            dedup_edges[key]["tx_count"] += e["tx_count"]
            for h in e["tx_hashes"]:
                if h and len(dedup_edges[key]["tx_hashes"]) < 3:
                    dedup_edges[key]["tx_hashes"].append(h)
        else:
            dedup_edges[key] = e

    return {
        "check": "wallet_flow",
        "result": "scanned",
        "source": "own",
        "wallet": wallet,
        "chain": chain,
        "hops": hops,
        "nodes": nodes,
        "edges": list(dedup_edges.values()),
        "node_count": len(nodes),
        "edge_count": len(dedup_edges),
        "note": "Hop-1 + top-10 hop-2 counterparties; USD via F2 price oracle",
    }


def _rank_counterparties(
    wallet: str,
    edge_raw: dict[tuple[str, str, str], dict[str, Any]],
) -> list[tuple[str, float]]:
    """Rank counterparties by absolute USD proxy volume (raw sums).

    Uses raw token amounts as a volume proxy (cheap, deterministic). USD
    normalization happens per-token afterwards; ranking by raw is sufficient
    for selecting the top-N counterparties.
    """
    volume: dict[str, float] = {}
    for (from_addr, to_addr, _token), agg in edge_raw.items():
        if from_addr == wallet:
            volume[to_addr] = volume.get(to_addr, 0.0) + agg["raw"]
        elif to_addr == wallet:
            volume[from_addr] = volume.get(from_addr, 0.0) + agg["raw"]
    ranked = sorted(volume.items(), key=lambda kv: kv[1], reverse=True)
    return [(addr, vol) for addr, vol in ranked if addr and addr != _ZERO_ADDR]


async def _normalize_token_usd(token: str, chain: str, raw_total: float) -> float | None:
    """Convert a raw token amount to USD via the F2 price oracle."""
    if raw_total <= 0:
        return None
    try:
        from app.domains.scanner.engines.price_oracle import normalize_amount

        return await asyncio.wait_for(
            normalize_amount(token, chain, raw_total),
            timeout=5.0,
        )
    except Exception as exc:
        logger.debug("wallet_flow_usd_failed token=%s: %s", token[:16], exc)
        return None


async def _enrich_nodes(node_ids: list[str], chain: str) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Batch-fetch wallet_labels and dex_events contract addresses for nodes."""
    labels_map: dict[str, list[dict[str, Any]]] = {}
    contract_addresses: set[str] = set()
    if not node_ids:
        return labels_map, contract_addresses

    label_task = _run_sync(_query_batch_labels, node_ids)
    contract_task = _run_sync(_query_contract_addresses, node_ids, chain)
    labels_rows, contract_rows = await asyncio.gather(label_task, contract_task, return_exceptions=True)

    if not isinstance(labels_rows, BaseException) and labels_rows:
        for row in labels_rows:
            addr = str(row[0] or "").lower()
            labels_map.setdefault(addr, []).append(
                {
                    "label_name": row[1],
                    "label_category": row[2],
                    "label_subtype": row[3],
                    "source": row[4],
                    "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                }
            )

    if not isinstance(contract_rows, BaseException) and contract_rows:
        for row in contract_rows:
            addr = str(row[0] or "").lower()
            if addr:
                contract_addresses.add(addr)

    return labels_map, contract_addresses


def _classify_node(
    address: str,
    labels: list[dict[str, Any]],
    contract_addresses: set[str],
) -> tuple[str, str | None]:
    """Return (node_type, display_label) for a graph node."""
    for lbl in labels:
        category = (lbl.get("label_category") or "").lower()
        name = (lbl.get("label_name") or "").lower()
        if category in ("exchange", "cex") or any(kw in name for kw in _CEX_KEYWORDS):
            return "CEX", lbl.get("label_name") or name or None
        if category in ("dex", "router", "amm") or any(kw in name for kw in _DEX_KEYWORDS):
            return "DEX", lbl.get("label_name") or name or None
        if category == "bridge" or any(kw in name for kw in _BRIDGE_KEYWORDS):
            return "bridge", lbl.get("label_name") or name or None
        if category == "mixer" or any(kw in name for kw in _MIXER_KEYWORDS):
            return "mixer", lbl.get("label_name") or name or None

    if address in contract_addresses:
        return "contract", None

    return "EOA", None


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_wallet_transfers(wallet: str, chain: str, limit: int) -> list[tuple[Any, ...]]:
    """Return recent transfers FROM/TO the wallet (hop 1)."""
    client = _get_ch_client()
    if client is None:
        return []
    params: dict[str, Any] = {"wallet": wallet, "chain": chain, "limit": limit}
    try:
        return client.execute(
            """
            SELECT from_address, to_address, token_address, value, tx_hash
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND (from_address = %(wallet)s OR to_address = %(wallet)s)
            ORDER BY block_timestamp DESC
            LIMIT %(limit)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_flow_hop1_failed: %s", exc)
        return []


def _query_counterparty_transfers(cp: str, root_wallet: str, chain: str, limit: int) -> list[tuple[Any, ...]]:
    """Return transfers for a hop-2 counterparty, excluding root-wallet edges."""
    client = _get_ch_client()
    if client is None:
        return []
    params: dict[str, Any] = {
        "cp": cp,
        "wallet": root_wallet,
        "chain": chain,
        "limit": limit,
    }
    try:
        return client.execute(
            """
            SELECT from_address, to_address, token_address, value, tx_hash
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND (from_address = %(cp)s OR to_address = %(cp)s)
              AND from_address != %(wallet)s
              AND to_address != %(wallet)s
            ORDER BY block_timestamp DESC
            LIMIT %(limit)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_flow_hop2_failed cp=%s: %s", cp[:10], exc)
        return []


def _query_batch_labels(addresses: list[str]) -> list[tuple[Any, ...]]:
    """Batch wallet_labels lookup for a node list."""
    client = _get_ch_client()
    if client is None or not addresses:
        return []
    params: dict[str, Any] = {"addresses": tuple(addresses)}
    try:
        return client.execute(
            """
            SELECT address, label_name, label_category, label_subtype, source, is_sanctioned
            FROM wallet_memory.wallet_labels
            WHERE address IN %(addresses)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_flow_labels_failed: %s", exc)
        return []


def _query_contract_addresses(addresses: list[str], chain: str) -> list[tuple[Any, ...]]:
    """Detect which node addresses appear as DEX contract addresses."""
    client = _get_ch_client()
    if client is None or not addresses:
        return []
    params: dict[str, Any] = {"addresses": tuple(addresses), "chain": chain}
    try:
        return client.execute(
            """
            SELECT DISTINCT contract_address
            FROM wallet_memory.dex_events
            WHERE chain = %(chain)s
              AND contract_address IN %(addresses)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_flow_contract_detect_failed: %s", exc)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _redis_get(key: str) -> dict[str, Any] | None:
    """Fetch a cached flow graph from Redis (graceful on failure)."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return None
        raw = await r.get(key)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        logger.debug("wallet_flow_redis_get_failed: %s", exc)
        return None


async def _redis_set(key: str, value: dict[str, Any], ttl: int) -> bool:
    """Store a flow graph in Redis with TTL (graceful on failure)."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return False
        await r.setex(key, ttl, json.dumps(value, default=str))
        return True
    except Exception as exc:
        logger.debug("wallet_flow_redis_set_failed: %s", exc)
        return False


def _unavailable_result(
    wallet: str,
    chain_norm: str,
    hops: int,
    reason: str = "clickhouse unavailable",
) -> dict[str, Any]:
    """Graceful degradation result."""
    return {
        "check": "wallet_flow",
        "result": "unavailable",
        "source": "own",
        "wallet": wallet,
        "chain": chain_norm,
        "hops": hops,
        "nodes": [],
        "edges": [],
        "node_count": 0,
        "edge_count": 0,
        "note": reason,
    }


def _error_result(wallet: str, chain_norm: str, hops: int, message: str) -> dict[str, Any]:
    """Invalid-input error result."""
    return {
        "check": "wallet_flow",
        "result": "error",
        "error": message,
        "wallet": wallet,
        "chain": chain_norm,
        "hops": hops,
        "nodes": [],
        "edges": [],
        "node_count": 0,
        "edge_count": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return {
        "cache_size": len(_FLOW_CACHE),
        "cache_max": _FLOW_CACHE_MAX,
        "cache_ttl_seconds": _FLOW_CACHE_TTL,
        "redis_ttl_seconds": REDIS_TTL_SECONDS,
        "clickhouse_connected": _get_ch_client() is not None,
    }


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _FLOW_CACHE.clear()
