"""Rule-based classification of Telegram channel messages.

Four labels, in this resolution order: ``exploit-news`` > ``scam-warning`` >
``shill`` > ``market`` > ``other``. Order matters: a post announcing a drain
usually also contains "buy" or a chart link, and calling that a shill would
bury the one message an operator must read.

The second job is narrower and is the reason this module exists: a message that
carries a **contract address AND a scam keyword** is a candidate for a scan.
The address alone is not interesting (every shill has one); the pairing is what
says "someone is telling people this thing is bad".
"""

from __future__ import annotations

import re

# EVM: 0x + 40 hex. Solana: base58, 32-44 chars, no 0/O/I/l — the same shape
# the chain itself accepts, so mints copied out of a post match verbatim.
EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

KEYWORDS = {
    "exploit-news": (
        "exploit",
        "exploited",
        "hacked",
        "hack",
        "vulnerability",
        "cve",
        "drained",
        "drainer",
        "compromised",
        "stolen",
        "breach",
        "attacked",
    ),
    "scam-warning": (
        "scam",
        "scammer",
        "rug",
        "rugged",
        "rugpull",
        "rug pull",
        "honeypot",
        "fake",
        "phishing",
        "blacklist",
        "ponzi",
        "exit scam",
        "avoid",
        "warning",
        "beware",
        "do not buy",
        "don't buy",
        "malicious",
    ),
    "shill": (
        "100x",
        "1000x",
        "gem",
        "presale",
        "launching",
        "stealth launch",
        "ca:",
        "contract:",
        "moon",
        "send it",
        "lfg",
        "dyor",
        "airdrop",
        "next solana",
        "buy now",
        "apes",
        "ape in",
        "low cap",
        "mic cap",
    ),
    "market": (
        "etf",
        "fed",
        "cpi",
        "liquidation",
        "funding rate",
        "open interest",
        "dominance",
        "ath",
        "all-time high",
        "tvl",
        "volume",
        "rally",
        "dump",
        "support",
        "resistance",
        "breakout",
    ),
}

# Scam wording that qualifies a message as a warning even when the classifier
# scores another label higher (a market post saying "this is a rug").
SCAM_MARKERS = KEYWORDS["scam-warning"] + KEYWORDS["exploit-news"]

CHAIN_HINTS = {
    "bsc": ("bsc", "bnb", "binance smart chain", "pancakeswap"),
    "base": ("base chain", "basescan", "@base"),
    "arbitrum": ("arbitrum", "arb"),
    "ethereum": ("eth", "ethereum", "uniswap", "erc20"),
}


def extract_addresses(text: str) -> list[tuple[str, str]]:
    """Return ``(address, chain)`` for every contract address in ``text``.

    Chain is guessed from the address shape, then overridden by a hint in the
    message body (an EVM address mentioned alongside "bsc" is a BSC token).
    """
    found: list[tuple[str, str]] = []
    lowered = text.lower()
    hint = next(
        (chain for chain, words in CHAIN_HINTS.items() if any(w in lowered for w in words)), None
    )
    for match in EVM_RE.findall(text):
        found.append((match, hint or "ethereum"))
    for match in SOL_RE.findall(text):
        if not EVM_RE.fullmatch(match):
            found.append((match, "solana"))
    return found


def classify(text: str) -> str:
    """Label one message. The highest keyword count wins; ties break by order."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return "other"
    scores = {
        label: sum(1 for word in words if word in lowered) for label, words in KEYWORDS.items()
    }
    best = max(scores.values())
    if not best:
        return "market" if "$" in lowered or any(c.isdigit() for c in lowered) else "other"
    for label in ("exploit-news", "scam-warning", "shill", "market"):
        if scores[label] == best:
            return label
    return "other"


def why_interesting(text: str, addresses: list[tuple[str, str]]) -> str:
    """Short reason string for the jsonl row — empty when nothing stands out."""
    lowered = (text or "").lower()
    markers = [word for word in SCAM_MARKERS if word in lowered]
    if not addresses:
        return ""
    if markers:
        return f"address + scam wording ({', '.join(markers[:3])})"
    if classify(text) == "shill":
        return "address posted as a promotion"
    return ""


def assess(message: dict) -> dict:
    """Enrich one normalized message with addresses, label and interest flag.

    ``message`` needs ``text``; ``channel`` and ``message_id`` are passed through
    so the caller can write a jsonl row without re-reading the source.
    """
    text = str(message.get("text") or "")
    addresses = extract_addresses(text)
    label = classify(text)
    reason = why_interesting(text, addresses)
    return {
        **message,
        "label": label,
        "addresses": [{"address": address, "chain": chain} for address, chain in addresses],
        "interesting": bool(reason),
        "why_interesting": reason,
    }
