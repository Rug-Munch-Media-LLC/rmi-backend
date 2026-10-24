#!/usr/bin/env python3
"""Live proof for the GMGN intelligence layer (Solana).

Runs the real endpoints and prints what came back — no cached fixtures, no
fabricated rows. Exits non-zero when the lifecycle feed is empty, so it can
gate a release.

Usage:  GMGN_API_KEY=... python3 scripts/gmgn_intel_live_proof.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.domains.gmgn_intel import get_gmgn_intel_service

SAMPLE = 5


def _dump(label: str, payload: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str)[:2600])


async def wallet_only(address: str) -> int:
    """Prove the wallet path alone — the free tier allows 5 calls per window."""
    profile = await get_gmgn_intel_service().wallet_intel(address, "solana", "7d")
    _dump(
        f"wallet intel {address}",
        {
            "realized_profit_usd": profile["realized_profit_usd"],
            "realized_profit_pnl": profile["realized_profit_pnl"],
            "winrate": profile["winrate"],
            "tokens_traded": profile["tokens_traded"],
            "pnl_over_5x_count": profile["pnl_over_5x_count"],
            "trades": profile["trades"],
            "tags": profile["tags"],
            "activity_rows": len(profile["activity"]),
            "holdings": profile["holdings"],
            "missing": profile["missing"],
        },
    )
    return 0 if profile["trades"]["buy"] is not None else 1


async def main() -> int:
    if "--wallet-only" in sys.argv:
        return await wallet_only(sys.argv[sys.argv.index("--wallet-only") + 1])
    service = get_gmgn_intel_service()
    _dump("descriptor", service.descriptor())

    lifecycle = await service.lifecycle("solana", 60)
    print("\n=== lifecycle counts ===")
    print(json.dumps(lifecycle["counts"]))
    print(f"token_count={lifecycle['token_count']} sources={lifecycle['sources']}")
    if lifecycle.get("missing"):
        print("missing:", lifecycle["missing"])
    top = lifecycle["tokens"][:SAMPLE]
    print("\n=== our top scored lifecycle tokens ===")
    for token in top:
        print(
            f"{token['score']:>6} {token['score_grade']} {token['stage']:<17} "
            f"{token['symbol'] or '?':<12} {token['address']} "
            f"liq={token['liquidity_usd']} vol24={token['volume_24h_usd']} "
            f"holders={token['holders']} trending={token['trending']}"
        )
    if not lifecycle["tokens"]:
        print("FAIL: lifecycle returned no live rows")
        return 1

    await asyncio.sleep(4)
    trending = await service.ranked_trending("solana", "1h", 10)
    print(f"\n=== trending re-ranked by our score (n={trending['token_count']}) ===")
    for token in trending["tokens"][:SAMPLE]:
        print(
            f"ours#{trending['tokens'].index(token) + 1:<3} gmgn_rank={token['trending_rank']!s:<5} "
            f"score={token['score']:<6} {token['symbol'] or '?':<12} {token['address']}"
        )

    await asyncio.sleep(4)
    target = lifecycle["tokens"][0]["address"]
    token = await service.token_intel(target, "solana", True)
    _dump(
        f"token intel {target}",
        {
            "symbol": token["symbol"],
            "stage": token["stage"],
            "score": token["score"],
            "grade": token["score_grade"],
            "security": {
                k: token["security"].get(k) for k in ("is_honeypot", "rug_ratio", "top_10_holder_rate", "flags")
            },
            "components": token["score_breakdown"]["components"] if token["score_breakdown"] else None,
            "missing": token["missing"],
            "smart_money_count": (token.get("smart_money") or {}).get("smart_money_count"),
        },
    )

    wallets = [row.get("wallet") for row in (token.get("smart_money") or {}).get("smart_money_activity", [])]
    wallets = [w for w in wallets if isinstance(w, str) and w]
    wallet_note = "smart-money tagged holder"
    if not wallets:
        # No smart-money tag on this token: fall back to its top holder so the
        # wallet path is still proven with live data (and say so). The holder
        # call competes for the same 5-calls/window budget, so retry once
        # after the client's park window rather than reporting a false empty.
        from app.databus.gmgn_provider import get_gmgn_provider

        for attempt in (1, 2):
            holders = await get_gmgn_provider().get_holder_analysis(target, "solana") or {}
            wallets = [h.get("address") for h in holders.get("top_holders", []) if isinstance(h.get("address"), str)]
            if wallets:
                break
            print(f"holder analysis empty (attempt {attempt}) — waiting out the rate-limit window")
            await asyncio.sleep(70)
        wallet_note = "top holder (no smart-money tag on this token)"
    if not wallets:
        creator = lifecycle["tokens"][0].get("creator")
        wallets = [creator] if isinstance(creator, str) and creator else []
        wallet_note = "token creator wallet (holder set unavailable)"
    if wallets:
        await asyncio.sleep(3)
        profile = await service.wallet_intel(wallets[0], "solana", "7d")
        print(f"\nwallet source: {wallet_note}")
        _dump(
            f"wallet intel {wallets[0]}",
            {
                "realized_profit_usd": profile["realized_profit_usd"],
                "winrate": profile["winrate"],
                "tokens_traded": profile["tokens_traded"],
                "trades": profile["trades"],
                "activity_rows": len(profile["activity"]),
                "holdings": profile["holdings"],
                "missing": profile["missing"],
            },
        )
    else:
        print("\n=== wallet intel ===")
        print("skipped: no smart-money wallet in this token's holder set")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
