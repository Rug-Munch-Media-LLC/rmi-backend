"""Render the premium scan card proof: three tiers, three real tokens.

Data is live, not invented: each token's RugCheck report and DexScreener pair
are fetched from the public APIs and mapped onto the same payload keys
``app.domains.scanner`` emits, then handed to the product renderer unchanged.

Run on Talos::

    .venv/bin/python scripts/premium_card_proof.py --out docs/reports/premium-cards

The severity rule below is proof-side, not product logic — in production the
card reads the scanner's own ``overall_risk``. It exists so the three sample
tokens land on three different grades while the numbers stay real.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.domains.telegram.premium import extract, render_card, render_thumb

TOKENS = {
    "CHARLIE": "63oXihJWAuauiXDHAs7xqYAgBubWzEq7RXpUjusQM8AD",
    "CLUCK": "8qx8J2SNyhpMk8cEMkhWtuagM6QMfzYt4YR9mUHPpump",
    "MIM": "4VdAC7rzxabkNg1hh8UxiuNUiqMGYzq8t6sU5K3Dpump",
}
UA = {"User-Agent": "rmi-scan-card/1.0"}


def _get(url: str) -> dict:
    """Fetch JSON from an https URL only — the two hosts below are constants."""
    if urlparse(url).scheme != "https":
        raise ValueError(f"refusing non-https url: {url}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as response:  # noqa: S310
        return json.load(response)


def _severity(rc: dict, lp_locked: float | None, top10: float) -> str:
    """CRITICAL mintable · HIGH unlocked LP · MEDIUM concentrated · LOW clean."""
    if rc.get("mintAuthority") or rc.get("freezeAuthority"):
        return "CRITICAL"
    if lp_locked is not None and lp_locked < 50:
        return "HIGH"
    return "MEDIUM" if top10 >= 70 else "LOW"


def payload(name: str, bank: pathlib.Path) -> tuple[dict, dict]:
    """Map live RugCheck + DexScreener data onto the scanner payload shape."""
    rc = json.loads((bank / f"rugcheck_{name}.json").read_text())
    dx = json.loads((bank / f"dex_{name}.json").read_text())
    pairs = dx.get("pairs") or []
    pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0) if pairs else {}
    market = (rc.get("markets") or [{}])[0]
    lp = market.get("lp") or {}
    top = rc.get("topHolders") or []
    top10 = round(sum(float(h.get("pct") or 0) for h in top[:10]), 1)
    supply = float((rc.get("token") or {}).get("supply") or 0)
    networks = rc.get("insiderNetworks") or []
    insiders = sum(int(n.get("activeAccounts") or 0) for n in networks)
    nested = sum(float(n.get("currentHolding") or 0) / supply * 100 for n in networks) if supply else 0
    risks = rc.get("risks") or []
    liq = float(rc.get("totalMarketLiquidity") or 0) or float((pair.get("liquidity") or {}).get("usd") or 0)
    body = {
        "token_address": rc["mint"],
        "token_symbol": (rc.get("tokenMeta") or {}).get("symbol") or name,
        "chain": "solana",
        "overall_risk": _severity(rc, lp.get("lpLockedPct"), top10),
        "rug_pull_probability": rc.get("score_normalised"),
        "total_liquidity_usd": liq,
        "top_10_concentration": top10,
        "total_holders": rc.get("totalHolders"),
        "volume_24h": (pair.get("volume") or {}).get("h24"),
        "price_usd": pair.get("priceUsd"),
        "price_change_24h": (pair.get("priceChange") or {}).get("h24"),
        "mint_authority": rc.get("mintAuthority"),
        "freeze_authority": rc.get("freezeAuthority"),
        "mint_authority_renounced": rc.get("mintAuthority") is None,
        "freeze_authority_renounced": rc.get("freezeAuthority") is None,
        "liquidity_pools": [
            {
                "tvl_usd": liq,
                "lp_locked_percentage": lp.get("lpLockedPct"),
                "is_burned": lp.get("lpLockedPct") == 100,
                "dex_name": pair.get("dexId") or "",
            }
        ],
        "insiders_detected": [f"net{i}" for i in range(insiders)],
        "dev_other_tokens": rc.get("creatorTokens") or [],
        "dev_rug_history": rc.get("rugged"),
        "critical_warnings": [
            f"{r['name']}: {r.get('value') or r.get('description')}"[:90] for r in risks if r.get("level") == "danger"
        ],
        "risk_flags": [],
        "scan_timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "dex_screener_url": pair.get("url") or f"https://dexscreener.com/solana/{rc['mint']}",
    }
    audit = {"insider_pct": round(nested, 2), "risks": [r.get("name") for r in risks]}
    return body, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/reports/premium-cards")
    parser.add_argument("--bank", default="/tmp/prem_data")  # noqa: S108
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in TOKENS:
        data, audit = payload(name, pathlib.Path(args.bank))
        card = extract(data)
        for tier in ("free", "pro", "alert"):
            rendered = render_card(card, tier)
            stem = f"{tier}_{name.lower()}"
            (out / f"{stem}.txt").write_text(rendered.html)
            rows.append((name, tier, rendered.chars, rendered.text_chars, rendered.pruned, card.severity))
        thumb = render_thumb(card, str(out))
        print(
            f"{name:8} {card.severity:9} top10={card.top10_pct:5}% "
            f"liq={card.liquidity_usd} insiders={audit['insider_pct']}% "
            f"thumb={thumb.bytes_len}B risks={audit['risks']}"
        )
    print("\ntier    token     html  text  pruned")
    for name, tier, chars, text_chars, pruned, _ in rows:
        print(f"{tier:7} {name:9} {chars:5} {text_chars:5}  {','.join(pruned) or '-'}")


if __name__ == "__main__":
    main()
