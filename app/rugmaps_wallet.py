"""RugMaps wallet enrichment — OUR data, not external APIs.

Batch wallet-label resolution (wallet_v2 Qdrant collection + DataBus wallet
scanner + ClickHouse wallet_memory) and fund-flow tracing (ClickHouse
``wallet_flow`` engine + cross-chain aggregator) for the RugMaps canvas.

This is the RugMaps-side enrichment layer: the token-graph endpoint keeps
its existing per-node flow, while ``batch_lookup_wallet_labels`` powers the
new ``/api/v1/rugmaps/wallet-labels`` route and ``lookup_fund_flows`` powers
``/api/v1/rugmaps/fund-flows``.

Every source is defensive: a missing Qdrant collection, unreachable
ClickHouse, or denied DataBus fetch degrades to the next source instead of
raising — the map must render even when enrichment is empty.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333").rstrip("/")
WALLET_V2_COLLECTION = os.getenv("WALLET_V2_COLLECTION", "wallet_v2")

# Payload keys we probe when scrolling wallet_v2. Different seeders have
# used ``address``, ``addr`` or nested ``metadata.address`` over time, so
# we defensively match whichever key exists.
_WALLET_ADDRESS_KEYS = ("address", "addr", "wallet_address")

# Category buckets used to classify labels for node coloring. Mirrors the
# classification the wallet_flow engine and frontend classifyWallet use.
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
    "hot wallet",
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
    "pump.fun",
    "raydium",
    "orca",
    "jupiter",
)
_MIXER_KEYWORDS = (
    "mixer",
    "tornado",
    "blender",
    "coinjoin",
    "wasabi",
    "privacy pool",
    "cryptocurrency mixer",
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
    "portal",
)
_WHALE_KEYWORDS = ("whale", "smart money", "insider", "large holder", "big holder", "accumulator")
_BOT_KEYWORDS = ("bot", "mev", "arb", "sniping", "sniper", "sandwich", "frontrun")
_DEV_KEYWORDS = ("deployer", "dev wallet", "developer", "creator", "team wallet", "founder", "multisig")


def classify_label(label: str, category: str = "") -> str:
    """Map a wallet label to a node class the frontend can color.

    Returns one of: CEX, DEX, mixer, bridge, whale, bot, sniper, dev, normal.
    """
    cat = (category or "").lower()
    text = (label or "").lower()

    if cat in ("exchange", "cex") or any(kw in text for kw in _CEX_KEYWORDS):
        return "CEX"
    if cat in ("dex", "router", "amm") or any(kw in text for kw in _DEX_KEYWORDS):
        return "DEX"
    if cat == "mixer" or any(kw in text for kw in _MIXER_KEYWORDS):
        return "mixer"
    if cat == "bridge" or any(kw in text for kw in _BRIDGE_KEYWORDS):
        return "bridge"
    if cat in ("whale", "smart_money", "insider") or any(kw in text for kw in _WHALE_KEYWORDS):
        return "whale"
    if cat == "sniper" or any(kw in text for kw in _BOT_KEYWORDS):
        return "sniper" if ("sniper" in text or cat == "sniper") else "bot"
    if cat in ("deployer", "developer", "dev") or any(kw in text for kw in _DEV_KEYWORDS):
        return "dev"
    return "normal"


# ── Qdrant wallet_v2 ────────────────────────────────────────────────────────


async def _scroll_wallet_v2(
    addresses: list[str],
    limit: int = 1000,
) -> dict[str, dict[str, Any]]:
    """Scroll the ``wallet_v2`` Qdrant collection for exact address matches.

    Probes several candidate payload keys (address / addr / wallet_address)
    so we stay resilient to seeder schema drift. Returns
    ``{address_lower: payload}``. Empty on missing collection / unreachable
    Qdrant — the caller falls back to ClickHouse / scanner.
    """
    import httpx

    if not addresses:
        return {}
    addrs = {a.lower() for a in addresses if a}
    if not addrs:
        return {}

    found: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=10.0) as client:
            # Probe collection existence first (fast 404 check).
            r = await client.get(f"/collections/{WALLET_V2_COLLECTION}")
            if r.status_code != 200:
                logger.debug("wallet_v2 collection missing or unreachable status=%d", r.status_code)
                return {}

            for key in _WALLET_ADDRESS_KEYS:
                body: dict[str, Any] = {
                    "limit": min(limit, 1000),
                    "with_payload": True,
                    "filter": {
                        "must": [
                            {
                                "key": key,
                                "match": {"any": sorted(addrs)},
                            }
                        ]
                    },
                }
                s = await client.post(f"/collections/{WALLET_V2_COLLECTION}/points/scroll", json=body)
                if s.status_code != 200:
                    continue
                points = s.json().get("result", {}).get("points", []) or []
                for p in points:
                    payload = p.get("payload") or {}
                    addr = str(payload.get(key) or payload.get("address") or "").lower()
                    if addr in addrs and addr not in found:
                        found[addr] = payload
                if found:
                    break
    except (httpx.HTTPError, httpx.TimeoutException, OSError, ValueError) as e:
        logger.debug("wallet_v2_scroll_error err=%s", e)
    return found


def _qdrant_payload_to_label(addr: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a wallet_v2 point payload into a label record."""
    label = str(payload.get("label") or payload.get("label_name") or payload.get("name") or "")
    category = str(payload.get("label_type") or payload.get("label_category") or payload.get("category") or "")
    chain = str(payload.get("chain") or payload.get("chain_id") or "")
    entity = str(payload.get("entity") or payload.get("entity_name") or "")
    return {
        "address": addr,
        "label": label,
        "category": category,
        "chain": chain,
        "entity": entity,
        "entity_type": str(payload.get("entity_type") or category or ""),
        "source": "wallet_v2_qdrant",
        "class": classify_label(label, category),
        "risk_score": float(payload.get("risk_score") or 0),
    }


# ── ClickHouse wallet_memory (own label store) ─────────────────────────────


def _clickhouse_batch_labels(addresses: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Batch lookup ``wallet_memory.wallet_labels`` in ClickHouse.

    Mirrors ``app/domains/scanner/engines/_common._query_batch_labels``.
    Returns ``{address_lower: [label_dict, ...]}``.
    """
    try:
        from clickhouse_driver import Client as CHClient
    except ImportError:
        return {}

    addrs = [a.lower() for a in addresses if a]
    if not addrs:
        return {}

    try:
        client = CHClient(
            host=os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse"),
            port=int(os.getenv("CLICKHOUSE_PORT", "9000")),
            user=os.getenv("CLICKHOUSE_USER", "rmi"),
            password=os.getenv("CLICKHOUSE_PASSWORD", "") or None,
            database=os.getenv("CLICKHOUSE_DATABASE", "wallet_memory"),
            connect_timeout=3,
            send_receive_timeout=10,
        )
        try:
            rows = client.execute(
                """
                SELECT address, label_name, label_category, label_subtype, source, is_sanctioned
                FROM wallet_memory.wallet_labels
                WHERE address IN %(addresses)s
                ORDER BY loaded_at DESC
                LIMIT 200
                """,
                {"addresses": tuple(addrs)},
                settings={"max_execution_time": 10},
            )
        finally:
            client.disconnect()

        out: dict[str, list[dict[str, Any]]] = {a: [] for a in addrs}
        for row in rows:
            addr = str(row[0] or "").lower()
            if addr not in out:
                continue
            out[addr].append(
                {
                    "address": addr,
                    "label": str(row[1] or ""),
                    "category": str(row[2] or ""),
                    "subtype": str(row[3] or ""),
                    "source": str(row[4] or ""),
                    "sanctioned": bool(row[5]),
                    "class": classify_label(str(row[1] or ""), str(row[2] or "")),
                }
            )
        return out
    except (ImportError, ConnectionError, TimeoutError, OSError, ValueError) as e:
        logger.debug("clickhouse_batch_labels_error err=%s", e)
        return {}


# ── DataBus wallet scanner (entity enrichment) ──────────────────────────────


async def _scanner_labels(
    addresses: list[str],
    chain: str,
    concurrency: int = 5,
) -> dict[str, dict[str, Any]]:
    """Resolve entity_name/entity_type/persona/risk via DataBus wallet_labels.

    ``databus.fetch("wallet_labels", address=...)`` hits our local providers
    first (ClickHouse wallet_memory) then external enrichment. Bounded
    concurrency so 50-node graphs don't stampede the wallet API.
    """
    from app.databus import databus

    results: dict[str, dict[str, Any]] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _one(addr: str) -> None:
        async with sem:
            try:
                res = await databus.fetch("wallet_labels", address=addr, chain=chain)
            except (TimeoutError, OSError, ValueError) as e:
                logger.debug("scanner_label_fetch_failed addr=%s err=%s", addr[:10], e)
                return
            if not res or not isinstance(res, dict):
                return
            data = res.get("data") if isinstance(res.get("data"), dict) else res
            entity_name = str(data.get("entity_name") or "")
            entity_type = str(data.get("entity_type") or "")
            persona = str(data.get("persona") or "")
            risk = data.get("risk_score")
            if isinstance(risk, (int, float)):
                risk = float(risk)
            else:
                risk = 0.0
            if entity_name or entity_type or persona:
                results[addr.lower()] = {
                    "address": addr,
                    "label": entity_name or persona,
                    "entity_name": entity_name,
                    "entity_type": entity_type,
                    "persona": persona,
                    "risk_score": risk,
                    "source": "databus_wallet_scanner",
                    "class": classify_label(entity_name or persona, entity_type),
                }

    await asyncio.gather(*[_one(a) for a in addresses], return_exceptions=True)
    return results


def _merge_label_sources(
    qdrant: dict[str, dict[str, Any]],
    clickhouse: dict[str, list[dict[str, Any]]],
    scanner: dict[str, dict[str, Any]],
    addresses: list[str],
) -> dict[str, dict[str, Any]]:
    """Merge the three label sources into one per-address payload.

    Precedence: DataBus scanner entity (richest) → ClickHouse first label →
    Qdrant point. All addresses get a key so the frontend can render
    "unknown" for unlabeled wallets.
    """
    out: dict[str, dict[str, Any]] = {}
    for addr in addresses:
        key = addr.lower()
        rec: dict[str, Any] = {
            "address": addr,
            "label": "",
            "entity_type": "",
            "class": "normal",
            "source": "",
            "risk_score": 0.0,
            "labels": [],
        }

        # ClickHouse labels (list) — first non-empty label wins for display.
        ch_labels = clickhouse.get(key, [])
        if ch_labels:
            rec["labels"] = ch_labels[:10]
            first = ch_labels[0]
            rec["label"] = first.get("label") or ""
            rec["entity_type"] = first.get("category") or ""
            rec["class"] = first.get("class") or classify_label(rec["label"], rec["entity_type"])
            rec["source"] = first.get("source") or "clickhouse_wallet_memory"
            rec["sanctioned"] = bool(first.get("sanctioned"))

        # Scanner entity enrichment — richest signal, overrides display label.
        sc = scanner.get(key)
        if sc:
            if sc.get("entity_name"):
                rec["label"] = sc["entity_name"]
            if sc.get("entity_type"):
                rec["entity_type"] = sc["entity_type"]
            rec["class"] = sc.get("class") or classify_label(rec["label"], rec["entity_type"])
            rec["source"] = sc.get("source") or rec["source"]
            rec["persona"] = sc.get("persona", "")
            rec["entity_name"] = sc.get("entity_name", "")
            rec["risk_score"] = float(sc.get("risk_score") or rec.get("risk_score") or 0)

        # Qdrant point — fallback if neither ClickHouse nor scanner hit.
        qp = qdrant.get(key)
        if qp and not rec["label"]:
            rec["label"] = qp.get("label") or ""
            rec["entity_type"] = qp.get("entity_type") or qp.get("category") or ""
            rec["class"] = qp.get("class") or classify_label(rec["label"], rec["entity_type"])
            rec["source"] = qp.get("source") or "wallet_v2_qdrant"
            if qp.get("risk_score"):
                rec["risk_score"] = float(qp["risk_score"])

        out[key] = rec
    return out


async def batch_lookup_wallet_labels(
    addresses: list[str],
    chain: str = "solana",
) -> dict[str, Any]:
    """Batch wallet-label resolution for the RugMaps canvas.

    Sources (all ours): wallet_v2 Qdrant collection, ClickHouse
    ``wallet_memory.wallet_labels``, DataBus wallet scanner.
    """
    clean = [a.strip() for a in addresses if a and a.strip()][:200]
    if not clean:
        return {"addresses": [], "labels": {}, "labeled": 0, "total": 0}

    qdrant, clickhouse, scanner = await asyncio.gather(
        _scroll_wallet_v2(clean),
        asyncio.to_thread(_clickhouse_batch_labels, clean),
        _scanner_labels(clean, chain),
        return_exceptions=True,
    )
    if isinstance(qdrant, BaseException):
        qdrant = {}
    if isinstance(clickhouse, BaseException):
        clickhouse = {}
    if isinstance(scanner, BaseException):
        scanner = {}

    labels = _merge_label_sources(qdrant, clickhouse, scanner, clean)
    labeled = sum(1 for v in labels.values() if v.get("label") or v.get("entity_type") or v.get("labels"))

    return {
        "addresses": clean,
        "labels": labels,
        "labeled": labeled,
        "total": len(clean),
    }


# ── Fund flows ──────────────────────────────────────────────────────────────


async def lookup_fund_flows(
    token: str,
    address: str,
    chain: str = "solana",
    hops: int = 1,
) -> dict[str, Any]:
    """Return inbound/outbound fund flows for a wallet on a token map.

    Uses our own ClickHouse ``wallet_flow`` engine (transfers FROM/TO the
    wallet, USD-normalized) plus the cross-chain aggregator profile.

    Response::

        {
          "token": ..., "address": ..., "chain": ...,
          "inbound": [{"from", "to", "token", "usd", "tx_count", "tx_hashes"}],
          "outbound": [{"from", "to", "token", "usd", "tx_count", "tx_hashes"}],
          "cross_chain": {...},
          "source": "wallet_flow_clickhouse",
        }
    """
    try:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        flow = await build_flow_graph(address, chain, hops=hops, max_nodes=60)
    except (ImportError, OSError, ValueError, TimeoutError) as e:
        logger.debug("fund_flow_build_failed addr=%s err=%s", address[:10], e)
        flow = {"result": "unavailable", "edges": [], "nodes": []}

    addr_l = (address or "").lower()
    edges = flow.get("edges", []) if isinstance(flow, dict) else []

    inbound: list[dict[str, Any]] = []
    outbound: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        from_a = str(e.get("from") or "").lower()
        to_a = str(e.get("to") or "").lower()
        rec = {
            "from": from_a,
            "to": to_a,
            "token": e.get("token", ""),
            "usd": e.get("usd", 0),
            "tx_count": e.get("tx_count", 0),
            "tx_hashes": e.get("tx_hashes", []),
        }
        if to_a == addr_l:
            inbound.append(rec)
        elif from_a == addr_l:
            outbound.append(rec)

    inbound.sort(key=lambda x: float(x.get("usd") or 0), reverse=True)
    outbound.sort(key=lambda x: float(x.get("usd") or 0), reverse=True)

    cross_chain: dict[str, Any] = {}
    try:
        from app.cross_chain import get_cross_chain_profile

        profile = await get_cross_chain_profile(address, chain)
        if isinstance(profile, dict):
            cross_chain = {
                "linked_addresses": profile.get("addresses", [])[:20],
                "chains": profile.get("chains", []),
                "total_chains": profile.get("total_chains", 0),
                "label": profile.get("label", ""),
            }
    except (ImportError, OSError, ValueError, TimeoutError) as e:
        logger.debug("cross_chain_profile_failed addr=%s err=%s", address[:10], e)

    return {
        "token": token,
        "address": address,
        "chain": chain,
        "inbound": inbound,
        "outbound": outbound,
        "inbound_total_usd": round(sum(float(i.get("usd") or 0) for i in inbound), 2),
        "outbound_total_usd": round(sum(float(i.get("usd") or 0) for i in outbound), 2),
        "cross_chain": cross_chain,
        "source": "wallet_flow_clickhouse",
        "nodes": flow.get("nodes", []) if isinstance(flow, dict) else [],
    }
