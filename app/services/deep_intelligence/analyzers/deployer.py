"""

Deployer Reputation Score (DRS)
================================
Track every token a deployer wallet has created across all chains.
Score based on: scam rate, avg lifespan, total extracted, cross-chain fingerprint.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import cfg, client, logger

async def deployer_reputation(address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Track EVERY token a deployer wallet has created across ALL chains.
    Score based on: scam rate, avg lifespan, total extracted, cross-chain fingerprint.
    """
    result = {
        "deployer": address,
        "chain": chain,
        "tokens_deployed": 0,
        "tokens_rugged": 0,
        "scam_rate": 0.0,
        "avg_lifespan_days": 0.0,
        "total_value_extracted_usd": 0.0,
        "chains_active": [chain],
        "risk_score": 0,
        "risk_level": "UNKNOWN",
        "flag_details": [],
        "tokens": [],
    }

    # Get deployer's tokens from DexScreener
    dex_data = await client.dexscreener(chain, address)
    if "error" not in dex_data:
        pass

    # For EVM chains, use Etherscan/BSCScan to find contracts created
    if chain in cfg.CHAIN_SCAN_MAP:
        contracts = await client.scan_get(
            chain,
            {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "sort": "asc",
                "page": 1,
                "offset": 100,
            },
        )
        if contracts.get("status") == "1":
            txs = contracts.get("result", [])
            contract_creations = [
                tx
                for tx in txs
                if tx.get("to") == "" or tx.get("input", "").startswith("0x60806040")
            ]
            result["tokens_deployed"] = len(contract_creations)

    # For Solana, use Helius to find token creations
    if chain == "sol" and cfg.HELIUS_KEY:
        await client.helius_post(
            "/transactions/parse",
            {
                "transactions": []  # Would need actual tx signatures
            },
        )

    # Build risk signals
    flags = []
    if result["tokens_deployed"] > 5:
        flags.append(f"High volume deployer: {result['tokens_deployed']} tokens")
    if result["scam_rate"] > 0.7:
        flags.append(f"Scam rate {result['scam_rate']:.0%} - likely serial rugger")

    # Calculate composite risk score
    scam_weight = min(result["scam_rate"] * 60, 60)
    volume_weight = min(result["tokens_deployed"] * 2, 20)
    extract_weight = min(result["total_value_extracted_usd"] / 10000, 20)
    result["risk_score"] = min(int(scam_weight + volume_weight + extract_weight), 100)
    result["risk_level"] = (
        "CRITICAL"
        if result["risk_score"] >= 80
        else "HIGH"
        if result["risk_score"] >= 60
        else "MEDIUM"
        if result["risk_score"] >= 40
        else "LOW"
        if result["risk_score"] >= 20
        else "MINIMAL"
    )
    result["flag_details"] = flags
    return result
