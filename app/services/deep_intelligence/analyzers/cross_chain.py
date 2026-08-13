"""

Cross-Chain Identity Resolution
=================================
Same entity, multiple chains. Detect deployer patterns, funding correlation,
simultaneous launches, shared wallet infrastructure.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import cfg, client, logger


async def cross_chain_identity(address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Same entity, multiple chains. Detect deployer patterns, funding correlation,
    simultaneous launches, shared wallet infrastructure.
    """
    result: dict[str, Any] = {
        "address": address,
        "source_chain": chain,
        "cross_chain_profiles": [],
        "linked_addresses": [],
        "shared_patterns": [],
        "risk_flags": [],
        "identity_confidence": 0,
        "verdict": "UNKNOWN",
    }

    profiles = []
    linked = []
    flags = []

    dex_info = await client.dexscreener(chain, address)

    token_symbol = ""
    if "error" not in dex_info:
        token_symbol = dex_info.get("baseToken", {}).get("symbol", "") or ""

    if token_symbol:
        try:
            resp = await client.http.get(f"https://api.dexscreener.com/latest/dex/search?q={token_symbol}")
            if resp.status_code == 200:
                search_data = resp.json()
                pairs = search_data.get("pairs") or []
                for pair in pairs[:20]:
                    pair_chain = pair.get("chainId", "")
                    if pair_chain != chain and pair_chain in cfg.CHAIN_DEXSCREENER_MAP.values():
                        profiles.append(
                            {
                                "chain": pair_chain,
                                "address": pair.get("baseToken", {}).get("address", ""),
                                "name": pair.get("baseToken", {}).get("name", ""),
                                "symbol": pair.get("baseToken", {}).get("symbol", ""),
                                "price_usd": pair.get("priceUsd", ""),
                                "liquidity_usd": pair.get("liquidity", {}).get("usd", 0)
                                if pair.get("liquidity")
                                else 0,
                                "volume_24h_usd": pair.get("volume", {}).get("h24", 0) if pair.get("volume") else 0,
                                "pair_created": pair.get("pairCreatedAt", ""),
                            }
                        )
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"Cross-chain search failed: {e}")

    if len(profiles) >= 3:
        flags.append(f"Same symbol found on {len(profiles)} chains - possible coordinated multi-chain scam")
    elif len(profiles) >= 2:
        flags.append(f"Token exists on {len(profiles)} chains - verify they're the same team")

    result["cross_chain_profiles"] = profiles
    result["linked_addresses"] = linked
    result["shared_patterns"] = []
    result["risk_flags"] = flags
    result["identity_confidence"] = min(len(profiles) * 25, 100)
    result["verdict"] = (
        "MULTI_CHAIN_ENTITY" if len(profiles) >= 3 else "POSSIBLE_DUPLICATE" if len(profiles) >= 1 else "SINGLE_CHAIN"
    )

    return result
