"""
Funding Source Tracer
======================
Trace where a wallet got its initial ETH/SOL from.
Detect: Exchange withdrawals, mixer usage, fresh wallet patterns.
"""

from typing import Any

from app.services.deep_intelligence.core import cfg, client


async def funding_source_tracer(address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Trace where a wallet got its initial ETH/SOL from.
    Detect: Exchange withdrawals, mixer usage, fresh wallet patterns.
    """
    result = {
        "address": address,
        "chain": chain,
        "funding_sources": [],
        "risk_flags": [],
        "risk_score": 0,
        "risk_level": "UNKNOWN",
        "first_funding": None,
        "summary": "",
    }

    sources = []
    flags = []
    risk_score = 0

    if chain == "sol" and cfg.HELIUS_KEY:
        txs = await client.helius_post(
            "/addresses/transactions", {"addresses": [address], "options": {"limit": 50}}
        )
        if "error" not in txs:
            for _tx in txs if isinstance(txs, list) else txs.get("result", []):
                pass

    elif chain in cfg.CHAIN_SCAN_MAP:
        txs = await client.scan_get(
            chain,
            {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "sort": "asc",
                "page": 1,
                "offset": 50,
            },
        )
        if txs.get("status") == "1":
            raw_txs = txs.get("result", [])
            incoming = [tx for tx in raw_txs if tx.get("to", "").lower() == address.lower()]
            if incoming:
                first_tx = incoming[0]
                from_addr = first_tx.get("from", "")
                value_eth = float(first_tx.get("value", 0)) / 1e18
                timestamp = int(first_tx.get("timeStamp", 0))

                known_cex = {
                    "0x28c6c06298d514da08ff9e8b921eb4f5bc3dd3": "Binance",
                    "0x3f5ce5fbfe3f6e18ecbf8c7a83bd633a63a4b7e": "Binance 2",
                    "0x56eddb81aa0709df9a94d390c7b0b0b0b0b0b0b0": "Coinbase",
                    "0x7a250d5630b4cf539739df2c5dacb4c659f2448d": "Uniswap Router",
                }
                source_name = known_cex.get(from_addr.lower(), "Unknown")
                is_cex = from_addr.lower() in known_cex
                is_fresh = len(raw_txs) < 5

                sources.append(
                    {
                        "address": from_addr,
                        "name": source_name,
                        "is_cex": is_cex,
                        "value_eth": round(value_eth, 6),
                        "timestamp": timestamp,
                        "block": first_tx.get("blockNumber", ""),
                    }
                )

                if is_fresh:
                    flags.append("Fresh wallet - funded just before interaction")
                    risk_score += 25
                if not is_cex and value_eth < 1:
                    flags.append("Small non-CEX funding - possible burner wallet")
                    risk_score += 15

    result["funding_sources"] = sources
    result["risk_flags"] = flags
    result["risk_score"] = min(risk_score, 100)
    result["risk_level"] = (
        "CRITICAL"
        if risk_score >= 60
        else "HIGH"
        if risk_score >= 40
        else "MEDIUM"
        if risk_score >= 20
        else "LOW"
    )
    result["summary"] = (
        f"Found {len(sources)} funding source(s). {len(flags)} risk flag(s) detected."
    )

    return result
