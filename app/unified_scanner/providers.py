"""External data providers: SENTINEL, Wallet Memory, threat intel, token scanning, etc."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════
# SENTINEL INTEGRATION - Labels + Intel
# ═══════════════════════════════════════════


async def _fetch_sentinel_labels(address: str, chain: str) -> dict[str, Any]:
    """Get rich labels from SENTINEL address_labeler."""
    try:
        from app.domains.scanners.address_labeler import AddressLabeler

        labeler = AddressLabeler()
        result = await labeler.analyze(address, chain)
        if result:
            return {
                "labels": getattr(result, "labels", []),
                "category": getattr(result, "category", ""),
                "risk_score": getattr(result, "risk_score", 0),
                "sources": getattr(result, "sources", []),
            }
    except Exception as e:
        logger.debug(f"SENTINEL labels failed for {address}: {e}")
    return {}


async def _fetch_wallet_memory(address: str, chain: str) -> dict[str, Any]:
    """Cross-chain identity + cluster intel from Wallet Memory Bank."""
    try:
        from app.wallet_memory.engine import get_wallet_engine

        engine = get_wallet_engine()
        intel = await engine.get_deployer_intelligence(address, chain)
        if intel:
            return {
                "entity_id": intel.get("entity_id"),
                "linked_wallets": intel.get("linked_wallets", []),
                "risk_score": intel.get("risk_score", 0),
                "scam_associations": intel.get("scam_associations", []),
                "deployer_history": intel.get("deployer_history"),
                "cross_chain_presence": intel.get("cross_chain_presence"),
            }
    except Exception as e:
        logger.debug(f"Wallet Memory Bank failed for {address}: {e}")
    return {}


# ═══════════════════════════════════════════
# TOKEN PORTFOLIO SCAN
# ═══════════════════════════════════════════


async def _scan_held_tokens(tokens: list, chain: str) -> dict[str, Any]:
    """Scan held tokens using SENTINEL - parallel with full enrichments."""
    if not tokens:
        return {"scanned": 0, "risks": [], "deployers_to_flag": []}

    sorted_tokens = sorted(tokens, key=lambda t: float(t.get("amount", 0) if isinstance(t, dict) else 0), reverse=True)
    mints = []
    for token in sorted_tokens[:5]:
        mint = token.get("mint", "") if isinstance(token, dict) else token
        if mint:
            mints.append(mint)

    if not mints:
        return {"scanned": 0, "risks": [], "deployers_to_flag": []}

    async def _scan_one(mint: str) -> dict | None:
        try:
            from app.token_scanner import scan_token

            scan = await scan_token(mint, chain, tier="free")
            enrich = {}
            if hasattr(scan, "free") and isinstance(scan.free, dict):
                e = scan.free
                enrich = {
                    "simulation": e.get("simulation"),
                    "holders": e.get("holders"),
                    "deployer": e.get("deployer"),
                    "deep_deployer": e.get("deep_deployer"),
                    "birdeye": e.get("birdeye"),
                    "copycat_check": e.get("copycat_check"),
                    "volume_anomaly": e.get("volume_anomaly"),
                }
            return {
                "token": mint,
                "symbol": scan.symbol,
                "name": scan.name,
                "safety_score": scan.safety_score,
                "confidence": getattr(scan, "confidence", 0),
                "risk_flags": scan.risk_flags,
                "enrichments": enrich,
            }
        except (ConnectionError, OSError, TimeoutError):
            return None

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_scan_one(m) for m in mints], return_exceptions=True), timeout=15.0
        )
    except TimeoutError:
        results = []

    risks = []
    deployers_to_flag = []
    highest = None
    for r in results if isinstance(results, list) else []:
        if isinstance(r, dict) and r:
            risks.append(r)
            if r["safety_score"] < 30:
                enrich = r.get("enrichments", {})
                deployer = enrich.get("deployer", {}) or {}
                dev_addr = deployer.get("deployer") if isinstance(deployer, dict) else None
                if dev_addr and r["safety_score"] < 20:
                    deployers_to_flag.append(
                        {
                            "deployer": dev_addr,
                            "token": r["token"],
                            "symbol": r["symbol"],
                            "chain": chain,
                            "risk_score": r["safety_score"],
                            "flags": r["risk_flags"][:3],
                        }
                    )
            if highest is None or r["safety_score"] < highest.get("safety_score", 100):
                highest = r

    return {
        "scanned": len(risks),
        "risks": risks,
        "highest_risk": highest,
        "deployers_to_flag": deployers_to_flag,
        "avg_safety": round(sum(r["safety_score"] for r in risks) / len(risks), 1) if risks else 0,
        "high_risk_count": sum(1 for r in risks if r["safety_score"] < 30),
    }


# ═══════════════════════════════════════════
# THREAT INTELLIGENCE
# ═══════════════════════════════════════════


async def _check_hapi_risk(address: str, chain: str) -> dict[str, Any] | None:
    """Query HAPI Protocol on-chain risk scoring."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://hapi.one/api/v1/address/{address}",
                params={"chain": chain.lower()},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "risk_score": data.get("risk_score", 0),
                    "risk_level": data.get("risk_level", "unknown"),
                    "is_malicious": data.get("is_malicious", False),
                    "categories": data.get("categories", []),
                    "source": "hapi",
                }
    except Exception as e:
        logger.debug(f"HAPI check failed: {e}")
    return None


async def _check_misttrack_light(address: str, chain: str) -> dict[str, Any] | None:
    """Query SlowMist MistTrack Light - free wallet risk assessment."""
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"https://misttrack.io/api/v1/address/{address}",
                params={"chain": chain.lower()},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "risk_score": data.get("risk_score", 0),
                    "risk_level": data.get("risk_level", "unknown"),
                    "labels": data.get("labels", []),
                    "related_addresses": data.get("related", [])[:5],
                    "source": "misttrack",
                }
    except Exception as e:
        logger.debug(f"MistTrack check failed: {e}")
    return None


async def _check_honeydb_ip(ip_address: str) -> dict[str, Any] | None:
    """Query HoneyDB for threat actor data on IP address."""
    try:
        api_id = os.getenv("HONEYDB_API_ID", "")
        api_key = os.getenv("HONEYDB_API_KEY", "")
        if not api_id or not api_key:
            return None

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://honeydb.io/api/v1/indicator/ipv4",
                params={"ip": ip_address},
                headers={"X-HoneyDB-APIID": api_id, "X-HoneyDB-APIKey": api_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "is_threat": data.get("is_threat", False),
                    "threat_score": data.get("threat_score", 0),
                    "events": data.get("events", []),
                    "source": "honeydb",
                }
    except Exception as e:
        logger.debug(f"HoneyDB check failed: {e}")
    return None


async def _check_trm_sanctions_wallet(address: str) -> dict[str, Any] | None:
    """Screen wallet address against TRM Labs sanctions list."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.trmlabs.com/public/v1/sanctions/screening",
                json={"address": [address]},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 201:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    r = results[0]
                    return {
                        "is_sanctioned": r.get("isSanctioned", False),
                        "screening_address": r.get("address", address),
                        "source": "trm_labs",
                        "risk_level": "critical" if r.get("isSanctioned") else "low",
                    }
            elif resp.status_code == 429:
                logger.warning("TRM Labs rate limit hit")
                return {"source": "trm_labs", "rate_limited": True, "is_sanctioned": False}
    except Exception as e:
        logger.debug(f"TRM sanctions check failed: {e}")
    return None


# ═══════════════════════════════════════════
# FUNDING SOURCE TRACING & ENTITY RESOLUTION
# ═══════════════════════════════════════════


async def _trace_funding_source(address: str, chain: str) -> dict[str, Any]:
    """Trace initial funding source using on-chain transaction history."""
    try:
        if chain == "solana":
            from app.unified_scanner.helius import _get_helius_key

            key = _get_helius_key()
            if not key:
                return {}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://mainnet.helius-rpc.com/v0/addresses/{address}/transactions",
                    params={"api-key": key, "limit": 10, "type": "TRANSFER"},
                )
                if resp.status_code != 200:
                    return {}
                txs = resp.json()
                if not txs:
                    return {"source": "unknown", "hops": 0}

                first_in = None
                for tx in txs:
                    transfers = tx.get("tokenTransfers", []) + tx.get("nativeTransfers", [])
                    for t in transfers:
                        if t.get("toUserAccount") == address:
                            first_in = t
                            break
                    if first_in:
                        break

                source = first_in.get("fromUserAccount", "unknown") if first_in else "unknown"
                source_label = await _resolve_entity_free(source, chain)
                return {
                    "source": source,
                    "source_label": source_label.get("label", "unknown"),
                    "is_cex": "exchange" in str(source_label).lower() or "cex" in str(source_label).lower(),
                    "is_mixer": "mixer" in str(source_label).lower() or "tornado" in str(source_label).lower(),
                    "hops": 1,
                }
        return {"source": "unknown", "hops": 0}
    except (ConnectionError, OSError, TimeoutError):
        return {"source": "unknown", "hops": 0}


async def _resolve_entity_free(address: str, chain: str) -> dict[str, Any]:
    """Basic cross-chain entity resolution - FREE tier."""
    try:
        from app.domains.scanners.address_labeler import AddressLabeler

        labeler = AddressLabeler()
        labels = await labeler.analyze(address, chain)
        if labels and labels.primary_label:
            return {"label": labels.primary_label, "source": "sentinel", "chains": [chain]}

        from app.wallet_memory.labels import get_label

        label = get_label(address, chain)
        if label:
            return {"label": label, "source": "wallet_labels", "chains": [chain]}

        return {"label": "unknown", "source": "none", "chains": [chain]}
    except (AttributeError, ImportError):
        return {"label": "unknown", "source": "none", "chains": [chain]}


async def _calculate_basic_pnl(address: str, chain: str, tokens: list, native_balance: float = 0) -> dict[str, Any]:
    """Basic PnL calculation - FREE tier."""
    try:
        total_value = native_balance or 0
        token_count = len(tokens)

        if tokens and chain == "solana":
            mints = [t.get("mint", "") for t in tokens[:10] if t.get("mint")]
            if mints:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get("https://quote-api.jup.ag/v6/price", params={"ids": ",".join(mints[:5])})
                    if resp.status_code == 200:
                        prices = resp.json().get("data", {})
                        for mint, pdata in prices.items():
                            price = float(pdata.get("price", 0))
                            for t in tokens:
                                if t.get("mint") == mint:
                                    balance = float(t.get("amount", 0)) / (10 ** (t.get("decimals", 0) or 0))
                                    total_value += balance * price

        return {
            "estimated_value_usd": round(total_value, 2),
            "token_count": token_count,
            "priced_tokens": min(len(tokens), 5),
            "method": "jupiter" if total_value > native_balance else "native_only",
        }
    except (ConnectionError, OSError, TimeoutError):
        return {
            "estimated_value_usd": round(native_balance, 2),
            "token_count": len(tokens),
            "method": "native_only",
        }


# ═══════════════════════════════════════════
# DATA FLYWHEEL
# ═══════════════════════════════════════════


async def _close_data_flywheel(deployers: list) -> None:
    """Feed scam findings back to Wallet Memory Bank."""
    if not deployers:
        return
    try:
        from app.wallet_memory.engine import get_wallet_engine

        engine = get_wallet_engine()
        for d in deployers[:5]:
            await engine.flag_deployer(
                d["deployer"],
                d["chain"],
                reason=f"scam_token_{d['symbol']}",
                token=d["token"],
                score=100 - d["risk_score"],
            )
        logger.info(f"Data flywheel: flagged {len(deployers[:5])} deployers")
    except Exception as e:
        logger.warning(f"Data flywheel failed: {e}")
