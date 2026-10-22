"""Entity extraction for collected Telegram messages.

Single entry point: extract(msgs) -> entities dict.
Tokens are keyed by chain:contract when a CA is present, else $TICKER.
"""
from __future__ import annotations

import re

SOL_RE = re.compile(r"(?<![0-9A-Za-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![0-9A-Za-z])")
EVM_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
TICK_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,14})\b")
TICK2_RE = re.compile(r"[Tt]icker:\s*\$?([A-Za-z][A-Za-z0-9]{1,14})")
NAME_RE = re.compile(r"([A-Za-z0-9][\w .'\-]{1,40}?)\s*\(\$([A-Za-z][A-Za-z0-9]{1,14})\)")
URL_RE = re.compile(r"https?://\S+")
TG_RE = re.compile(r"(?:https?://)?t\.me/\S+")
REF_RE = re.compile(
    r"(?:[?&](?:ref|start|invite|aff|code)=|\bref(?:errral)?[ _-]?code\b"
    r"|\buse (?:my |this )?(?:code|ref)\b|\binvite code\b|\bpromo code\b"
    r"|utm_source=|\baffiliate\b|\breferral\b)",
    re.I,
)
SOL_TAGS = re.compile(r"\[(SOL|SOLANA)\]|solana|pumpfun|pump\.fun", re.I)
EVM_TAGS = re.compile(r"\[(ETH|EVM|BASE)\]|ethereum|base chain", re.I)
BSC_TAGS = re.compile(r"\[BSC\]|\bBSC\b|\bBNB\b|binance smart chain|pancakeswap", re.I)

_SKIP_SOL = {
    "11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}


def _chain_for(text: str, ca: str) -> str:
    if ca.startswith("0x"):
        if BSC_TAGS.search(text):
            return "bsc"
        return "ethereum" if EVM_TAGS.search(text) else "evm"
    if ca.endswith("pump") or SOL_TAGS.search(text):
        return "solana"
    if EVM_TAGS.search(text):
        return "evm"
    return "solana"


def _scan_text(text: str, out: dict) -> None:
    """Accumulate entities from one message body into `out`."""
    links = URL_RE.findall(text)
    out["links"].extend(links)
    out["refs"].extend(REF_RE.findall(text))
    out["tg_links"].extend(TG_RE.findall(text))

    named = {t.upper(): n.strip() for n, t in NAME_RE.findall(text)}
    for raw in EVM_RE.findall(text) + SOL_RE.findall(text):
        ca = raw if not raw.startswith("0x") else raw.lower()
        if ca in _SKIP_SOL or (len(ca) < 32 and not ca.startswith("0x")):
            continue
        chain = _chain_for(text, ca)
        out["tokens"].setdefault(f"{chain}:{ca}", {
            "key": f"{chain}:{ca}", "ca": ca, "chain": chain,
            "ticker": None, "name": None,
        })
        out["cas"].append(ca)

    tickers = [t for t in TICK_RE.findall(text)]
    tickers += [t for t in TICK2_RE.findall(text)]
    for t in tickers:
        up = t.upper()
        if len(up) < 2 or up in {"USD", "MC", "LP", "N/A", "K", "M", "B"}:
            continue
        out["tickers"].append(up)
        out["tokens"].setdefault(f"${up}", {
            "key": f"${up}", "ca": None, "chain": None,
            "ticker": up, "name": named.get(up),
        })


def extract(msgs: list[dict]) -> dict:
    """Extract tokens, contract addresses, links and referral markers."""
    out: dict = {"tokens": {}, "cas": [], "tickers": [], "links": [], "refs": [], "tg_links": []}
    for m in msgs:
        _scan_text(m.get("t") or "", out)
    for key, rec in out["tokens"].items():
        if rec["ticker"] is None:
            rec["ticker"] = rec["name"] or key.split(":")[-1][:8]
    return out
