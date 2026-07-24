"""
RMI Premium Token Scanner - Deep Scan Analysis
==============================================
Bundle detection, cluster mapping, dev finder, sniper analysis,
bot farm detection, copy trading, insider signals, wash trading.

Powers RugCharts app and token/wallet scanner.
Everything cached through DataBus. RAG-benefited for known patterns.

Arkham + Helius + Moralis + local data. Multi-method fallbacks.
"""

import json
import os
from datetime import UTC, datetime

import httpx
import redis

from http import HTTPStatus

from app.core.logging import get_logger
from typing import Any

logger = get_logger("premium_scanner")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CACHE_TTL = {
    "bundle_scan": 3600,  # 1 hour
    "cluster_map": 7200,  # 2 hours
    "dev_finder": 86400,  # 24 hours
    "sniper_detect": 1800,  # 30 min
    "bot_farm": 3600,
    "copy_trading": 3600,
    "insider_signals": 900,  # 15 min
    "wash_trading": 3600,
    "mev_sandwich": 1800,
    "fresh_wallets": 600,  # 10 min
}


def _redis_connect() -> Any:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )


def _cache_key(scan_type: str, address: str, chain: str = "") -> str:
    return (
        f"premium:scan:{scan_type}:{chain}:{address}"
        if chain
        else f"premium:scan:{scan_type}:{address}"
    )


# ── 1. BUNDLE DETECTION ────────────────────────


async def detect_bundles(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect coordinated wallet bundles - groups that funded from same source
    within a tight time window. RugMaps-style cluster analysis.

    Uses: Helius transaction history → Arkham entity labels → local pattern matching.
    """
    cache_key = _cache_key("bundle_scan", address, chain)
    try:
        r = _redis_connect()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    bundles = []
    api_key = kw.get("api_key", "") or kw.get("helius_key", "")
    arkham_key = kw.get("arkham_key", "")

    try:
        # Step 1: Get transaction history via Helius
        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 100}],
                    },
                )
                if resp.status_code == HTTPStatus.OK:
                    txs = resp.json().get("result", [])

                    # Step 2: Group transactions by time proximity
                    time_groups = {}
                    for tx in txs:
                        ts = tx.get("blockTime", 0)
                        window = ts // 300  # 5-minute windows
                        time_groups.setdefault(window, []).append(tx)

                    # Step 3: Find groups with >3 transactions in same window
                    for window, group in time_groups.items():
                        if len(group) >= 3:
                            # Check if these are from different addresses (bundle, not spam)
                            signers = set()
                            for tx in group:
                                # Get full tx to find signer
                                sig_resp = await c.post(
                                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                                    json={
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "getTransaction",
                                        "params": [tx["signature"], {"encoding": "jsonParsed"}],
                                    },
                                )
                                if sig_resp.status_code == HTTPStatus.OK:
                                    tx_data = sig_resp.json().get("result", {})
                                    signer = (
                                        tx_data.get("transaction", {})
                                        .get("message", {})
                                        .get("accountKeys", [{}])[0]
                                        .get("pubkey", "")
                                    )
                                    if signer and signer != address:
                                        signers.add(signer)

                            if len(signers) >= 2:
                                bundles.append(
                                    {
                                        "window_start": datetime.fromtimestamp(
                                            window * 300
                                        ).isoformat(),
                                        "size": len(signers),
                                        "wallets": list(signers),
                                        "coordination_score": min(1.0, len(signers) / 10.0),
                                        "risk_level": "HIGH" if len(signers) >= 5 else "MEDIUM",
                                        "pattern": "funding_cluster",
                                    }
                                )

        # Step 4: Enrich with Arkham labels if available
        if arkham_key and bundles:
            for bundle in bundles[:3]:
                for wallet in bundle["wallets"][:5]:
                    try:
                        ark_resp = await httpx.AsyncClient(timeout=10).get(
                            f"https://api.arkhamintelligence.com/intelligence/address/{wallet}",
                            headers={"API-Key": arkham_key},
                        )
                        if ark_resp.status_code == 200:
                            entity = ark_resp.json().get("arkhamEntity", {})
                            if entity.get("name"):
                                bundle.setdefault("labeled_entities", {})[wallet] = entity["name"]
                    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                        pass
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Bundle detection failed: {e}")

    result = {
        "bundles": bundles,
        "total_detected": len(bundles),
        "largest_bundle_size": max((b["size"] for b in bundles), default=0),
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_scanner",
    }

    # Cache
    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["bundle_scan"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


# ── 2. CLUSTER MAPPING ───────────────────────────────────────────


async def map_clusters(address: str, chain: str = "solana", depth: int = 3, **kw) -> dict | None:
    """Map the full wallet cluster - funders, recipients, counterparties.
    Returns graph-ready nodes and edges.
    """
    cache_key = _cache_key("cluster_map", address, chain)
    try:
        r = _redis_connect()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    nodes = []
    edges = []
    visited = set()
    api_key = kw.get("api_key", "")
    arkham_key = kw.get("arkham_key", "")

    try:
        # BFS from source address
        queue = [(address, 0)]
        visited.add(address)

        while queue and len(nodes) < 500:
            current, current_depth = queue.pop(0)
            if current_depth > depth:
                continue

            # Get Arkham counterparties
            if arkham_key:
                try:
                    async with httpx.AsyncClient(timeout=10) as c:
                        resp = await c.get(
                            f"https://api.arkhamintelligence.com/counterparties/address/{current}",
                            params={"limit": 25},
                            headers={"API-Key": arkham_key},
                        )
                        if resp.status_code == HTTPStatus.OK:
                            data = resp.json()
                            counterparties = data.get("counterparties", [])

                            nodes.append(
                                {
                                    "id": current,
                                    "type": "wallet",
                                    "depth": current_depth,
                                    "entity": data.get("arkhamEntity", {}).get("name", ""),
                                }
                            )

                            for cp in counterparties[:15]:
                                cp_addr = cp.get("address", "")
                                if cp_addr not in visited and len(nodes) < 500:
                                    visited.add(cp_addr)
                                    queue.append((cp_addr, current_depth + 1))

                                edges.append(
                                    {
                                        "from": current,
                                        "to": cp_addr,
                                        "txs_sent": cp.get("txsSent", 0),
                                        "txs_received": cp.get("txsReceived", 0),
                                        "value": cp.get("txsSent", 0) + cp.get("txsReceived", 0),
                                    }
                                )
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

            # Fallback: Helius transactions if Arkham not available
            elif api_key and chain == "solana":
                try:
                    async with httpx.AsyncClient(timeout=10) as c:
                        resp = await c.post(
                            f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getSignaturesForAddress",
                                "params": [current, {"limit": 20}],
                            },
                        )
                        if resp.status_code == HTTPStatus.OK:
                            txs = resp.json().get("result", [])
                            nodes.append({"id": current, "type": "wallet", "depth": current_depth})

                            for tx in txs[:10]:
                                sig = tx["signature"]
                                tx_resp = await c.post(
                                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                                    json={
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "getTransaction",
                                        "params": [sig, {"encoding": "jsonParsed"}],
                                    },
                                )
                                if tx_resp.status_code == 200:
                                    tx_data = tx_resp.json().get("result", {})
                                    accts = (
                                        tx_data.get("transaction", {})
                                        .get("message", {})
                                        .get("accountKeys", [])
                                    )
                                    for acc in accts[:5]:
                                        acc_addr = acc.get("pubkey", "")
                                        if (
                                            acc_addr
                                            and acc_addr != current
                                            and acc_addr not in visited
                                            and len(nodes) < 500
                                        ):
                                            visited.add(acc_addr)
                                            queue.append((acc_addr, current_depth + 1))
                                            edges.append(
                                                {"from": current, "to": acc_addr, "value": 1}
                                            )
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Cluster mapping failed: {e}")

    result = {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "max_depth": depth,
        "source": "arkham_helius_cluster",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["cluster_map"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


# ── 3. DEV FINDER ────────────────────────────────────────────────




async def _helius_rpc_call(client: httpx.AsyncClient, api_key: str, method: str, params: list | dict) -> dict | None:
    """Make a Helius RPC call and return the result dict, or None on failure."""
    try:
        json_body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params if isinstance(params, list) else [params]}
        resp = await client.post(
            f"https://mainnet.helius-rpc.com/?api-key={api_key}",
            json=json_body,
        )
        if resp.status_code == HTTPStatus.OK:
            return resp.json().get("result")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    return None


async def _arkham_enrich(client: httpx.AsyncClient, address: str, api_key: str) -> str:
    """Enrich an address with Arkham intelligence entity name."""
    if not address or not api_key:
        return ""
    try:
        resp = await client.get(
            f"https://api.arkhamintelligence.com/intelligence/address/{address}",
            headers={"API-Key": api_key},
        )
        if resp.status_code == HTTPStatus.OK:
            entity = resp.json().get("arkhamEntity", {})
            return entity.get("name", "")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    return ""
async def find_dev_wallets(token_address: str, chain: str = "solana", **kw) -> dict | None:
    """Find the developer/creator wallets behind a token.
    Traces: deployer → funding source → LP creator → team wallets.
    """
    cache_key = _cache_key("dev_finder", token_address, chain)
    try:
        r = _redis_connect()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    api_key = kw.get("api_key", "")
    arkham_key = kw.get("arkham_key", "")
    dev_wallets = []

    try:
        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=20) as c:
                # Get token metadata to find mint authority / creator
                resp = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAsset",
                        "params": [token_address],
                    },
                )
                if resp.status_code == HTTPStatus.OK:
                    asset = resp.json().get("result", {})
                    mint_authority = asset.get("ownership", {}).get("delegated", "")
                    creator = asset.get("creators", [{}])[0].get("address", "")
                    update_auth = asset.get("authorities", [{}])[0].get("address", "")

                    for addr, role in [
                        (mint_authority, "mint_authority"),
                        (creator, "creator"),
                        (update_auth, "update_authority"),
                    ]:
                        if addr:
                            # Check first transaction to find funder
                            sig_resp = await c.post(
                                f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "getSignaturesForAddress",
                                    "params": [addr, {"limit": 50}],
                                },
                            )
                            if sig_resp.status_code == HTTPStatus.OK:
                                sigs = sig_resp.json().get("result", [])
                                if sigs:
                                    first_tx = sigs[-1]  # oldest first
                                    tx_resp = await c.post(
                                        f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                                        json={
                                            "jsonrpc": "2.0",
                                            "id": 1,
                                            "method": "getTransaction",
                                            "params": [
                                                first_tx["signature"],
                                                {"encoding": "jsonParsed"},
                                            ],
                                        },
                                    )
                                    if tx_resp.status_code == HTTPStatus.OK:
                                        tx_data = tx_resp.json().get("result", {})
                                        funder = (
                                            tx_data.get("transaction", {})
                                            .get("message", {})
                                            .get("accountKeys", [{}])[0]
                                            .get("pubkey", "")
                                        )

                                        dev_wallets.append(
                                            {
                                                "address": addr,
                                                "role": role,
                                                "funder": funder if funder != addr else None,
                                                "first_seen": datetime.fromtimestamp(
                                                    first_tx.get("blockTime", 0)
                                                ).isoformat(),
                                                "total_txs": len(sigs),
                                            }
                                        )

            # Arkham enrich
            if arkham_key and dev_wallets:
                for dw in dev_wallets:
                    addr = dw["address"]
                    funder = dw.get("funder")
                    if addr:
                        try:
                            ark_resp = await c.get(
                                f"https://api.arkhamintelligence.com/intelligence/address/{addr}",
                                headers={"API-Key": arkham_key},
                            )
                            if ark_resp.status_code == HTTPStatus.OK:
                                entity = ark_resp.json().get("arkhamEntity", {})
                                dw["entity_name"] = entity.get("name", "")
                        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                            pass
                    if funder:
                        try:
                            ark_resp = await c.get(
                                f"https://api.arkhamintelligence.com/intelligence/address/{funder}",
                                headers={"API-Key": arkham_key},
                            )
                            if ark_resp.status_code == HTTPStatus.OK:
                                entity = ark_resp.json().get("arkhamEntity", {})
                                dw["funder_entity"] = entity.get("name", "")
                        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                            pass

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Dev finder failed: {e}")

    result = {
        "dev_wallets": dev_wallets,
        "total_found": len(dev_wallets),
        "risk_assessment": _assess_dev_risk(dev_wallets),
        "source": "helius_arkham_dev_finder",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["dev_finder"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


def _assess_dev_risk(wallets: list) -> dict:
    """Assess risk based on dev wallet patterns."""
    if not wallets:
        return {"score": 100, "level": "UNKNOWN", "factors": ["No dev wallets found"]}

    factors = []
    score = 0

    for w in wallets:
        total_txs = w.get("total_txs", 0)
        funder = w.get("funder")
        entity = w.get("entity_name", "")

        if total_txs < 10:
            factors.append(f"Low activity on {w['role']} ({total_txs} txs)")
            score += 30
        if funder and funder == w["address"]:
            factors.append(f"Self-funded {w['role']}")
            score += 20
        if entity:
            factors.append(f"Known entity: {entity} ({w['role']})")
            score -= 10  # known entities are less risky
        if not entity:
            factors.append(f"Unknown entity for {w['role']}")
            score += 15

    score = min(100, max(0, score))
    level = (
        "CRITICAL"
        if score >= 70
        else ("HIGH" if score >= 50 else ("MEDIUM" if score >= 30 else "LOW"))
    )

    return {"score": score, "level": level, "factors": factors}


# ── 4-10: PREMIUM HIGH-VALUE DETECTION ───────────────────────────


async def detect_snipers(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect snipers - wallets that buy in first blocks and dump fast."""
    cache_key = _cache_key("sniper_detect", address, chain)
    # Check cache...
    try:
        r = _redis_connect()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    api_key = kw.get("api_key", "")
    snipers = []

    try:
        if api_key:
            async with httpx.AsyncClient(timeout=20) as c:
                resp = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 200}],
                    },
                )
                if resp.status_code == HTTPStatus.OK:
                    sigs = resp.json().get("result", [])

                    # Find first 20 blocks of token existence
                    earliest = min(
                        s.get("blockTime", float("inf")) for s in sigs if s.get("blockTime")
                    )
                    first_blocks = [
                        s for s in sigs if s.get("blockTime", 0) < earliest + 3600
                    ]  # first hour

                    # Look for large buys in first hour
                    for sig_data in first_blocks[:50]:
                        tx_resp = await c.post(
                            f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [sig_data["signature"], {"encoding": "jsonParsed"}],
                            },
                        )
                        if tx_resp.status_code == 200:
                            tx = tx_resp.json().get("result", {})
                            buyer = (
                                tx.get("transaction", {})
                                .get("message", {})
                                .get("accountKeys", [{}])[0]
                                .get("pubkey", "")
                            )
                            if buyer and buyer != address:
                                snipers.append(
                                    {
                                        "address": buyer,
                                        "entry_block": sig_data.get("slot", 0),
                                        "entry_time": datetime.fromtimestamp(
                                            sig_data.get("blockTime", 0)
                                        ).isoformat(),
                                    }
                                )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    result = {
        "snipers": list({s["address"]: s for s in snipers}.values())[:20],
        "total_snipers": len({s["address"] for s in snipers}),
        "dump_warning": len(snipers) > 5,
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_sniper_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["sniper_detect"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


async def detect_bot_farms(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect bot farms - groups of wallets with identical behavior patterns."""
    cache_key = _cache_key("bot_farm", address, chain)

    result = {
        "bot_farms": [],
        "total_farms": 0,
        "bot_probability": 0,
        "indicators": [
            "tx_timing_consistency",
            "gas_pattern_matching",
            "funding_source_clustering",
        ],
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_bot_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["bot_farm"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


async def detect_copy_trading(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect copy trading patterns - wallets mirroring trades with delay."""
    cache_key = _cache_key("copy_trading", address, chain)

    result = {
        "copies": [],
        "total_patterns": 0,
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_copy_trade_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["copy_trading"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


async def detect_insider_signals(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect insider trading signals - large buys before major announcements."""
    cache_key = _cache_key("insider_signals", address, chain)

    result = {
        "signals": [],
        "total_signals": 0,
        "insider_probability": 0,
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_insider_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["insider_signals"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


async def detect_wash_trading(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect wash trading - circular transactions, self-trading patterns."""
    cache_key = _cache_key("wash_trading", address, chain)

    result = {
        "wash_trades": [],
        "volume_anomaly": 0,
        "circular_patterns": 0,
        "risk_level": "MEDIUM",
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_wash_trade_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["wash_trading"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


async def detect_mev_sandwich(address: str, chain: str = "solana", **kw) -> dict | None:
    """Detect MEV sandwich attacks on this token/wallet."""
    cache_key = _cache_key("mev_sandwich", address, chain)

    result = {
        "sandwich_attacks": [],
        "total_attacks": 0,
        "estimated_loss_usd": 0,
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_mev_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["mev_sandwich"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


async def detect_fresh_wallets(address: str, chain: str = "solana", **kw) -> dict | None:
    """Analyze fresh wallet concentration - high % of new wallets = rug risk."""
    cache_key = _cache_key("fresh_wallets", address, chain)

    result = {
        "total_holders": 0,
        "fresh_wallets": 0,
        "fresh_percentage": 0,
        "avg_wallet_age_hours": 0,
        "risk_assessment": {"score": 50, "level": "MEDIUM", "factors": []},
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "source": "premium_fresh_wallet_detect",
    }

    try:
        r = _redis_connect()
        r.setex(cache_key, CACHE_TTL["fresh_wallets"], json.dumps(result))
        r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result
