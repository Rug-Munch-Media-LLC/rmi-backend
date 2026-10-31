"""Intent signals for the crypto skill router — compiled patterns only.

Split from ``skill_router`` for the same reason ``chat/_prompts.py`` is split
from ``personas.py``: the patterns are quoted in review and edited far more often
than the routing order, and both files then stay inside the 200-line ceiling.

No logic, no I/O, stdlib only. Address patterns are duplicated from
``app/domains/chat_tools/_shared.py`` because importing that package pulls the
executor stack (and ``app.config``); the same duplication already exists between
``routers/chat.py`` and ``chat_tools/_shared.py``.
"""

from __future__ import annotations

import re
from typing import Final

# Any EVM or Solana address: enough to make a message about one specific token.
EVM_RE: Final = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOL_RE: Final = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
SYMBOL_RE: Final = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,10})\b")

# "my bags", "my pnl" — the user is asking about their own book, not one token.
PORTFOLIO_RE: Final = re.compile(
    r"\b(?:my (?:portfolio|bags|holdings|positions|wallet)|portfolio|holdings|pnl|am i up)\b",
    re.IGNORECASE,
)
# Forensics words: who funded / who is behind / cluster. Beats a bare address.
WALLET_RE: Final = re.compile(
    r"\b(?:wallet|who funded|who sent|who is behind|first funder|funding source|trace|cluster|"
    r"related wallets?|entity|syndicate|deployer)\b",
    re.IGNORECASE,
)
# Exploit, incident and news: the only class that earns the reasoning lane.
EXPLOIT_RE: Final = re.compile(
    r"\b(?:exploit\w*|hack(?:ed)?|drained|vulnerab\w*|reentran\w*|flash ?loan attack|incident|"
    r"post.?mortem|news|headlines|breaking)\b",
    re.IGNORECASE,
)
# Single-token safety words. "safe" alone is excluded — it fires on "is it safe
# to bridge" — so only the safety-question phrases count.
RISK_RE: Final = re.compile(
    r"\b(?:rug|rugpull|rug ?pull|honeypot|honey ?pot|scam|audit|mintable|blacklist|tax|"
    r"lp (?:locked|pulled)|scan|check this (?:token|coin|contract|address))\b"
    r"|is (?:it|this) safe|safety score",
    re.IGNORECASE,
)
MARKET_RE: Final = re.compile(
    r"\b(?:market|fear (?:&|and) greed|market cap|overview|altseason|dominance)\b", re.IGNORECASE
)
PRICE_RE: Final = re.compile(r"\b(?:price|worth|how much is|how much does|cost of|trading at|usd)\b", re.IGNORECASE)
# How-to / definitional openers: education, answerable from the corpus alone.
HOWTO_RE: Final = re.compile(
    r"^\s*(?:how (?:do|does|can|to)|what (?:is|are|s)|why (?:is|are|does)|explain|"
    r"difference between|tutorial|guide|walk me through)\b",
    re.IGNORECASE,
)
# "what is a honeypot" is a definition, not a token check — the same exclusion
# ``chat_tools/_intent.py`` applies to its suspicious-list branch.
DEFINITION_RE: Final = re.compile(r"^\s*(?:what(?:'s| is| are)|explain)\b", re.IGNORECASE)

# An address plus an interrogative ("who sent this") is a wallet question, not a
# token-safety one; the address alone is the risk ask.
INTERROGATIVE_RE: Final = re.compile(r"\bwho\b", re.IGNORECASE)
