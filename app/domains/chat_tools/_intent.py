"""Tool intent classifier for the chat tool-use layer.

Pure classifier over the user message: price/scan/ta/market/news/trace/
suspicious/prediction/perp/None. Mirrors the address/symbol regexes in
app/routers/chat.py (kept local to avoid a circular import — chat.py
imports the chat_tools package).
"""

from __future__ import annotations

import re

from ._shared import (
    _EVM_ADDRESS_RE,
    _SOLANA_ADDRESS_RE,
    _SYMBOL_RE,
    _extract_price_symbol,
)

_INTENT_PRICE_PHRASES = (
    "price of",
    "price for",
    "price",
    "what's the price",
    "what is the price",
    "whats the price",
    "how much is",
    "how much does",
    "how much for",
    "cost of",
    "worth",
    "usd",
)
_INTENT_SCAN_KEYWORDS = (
    "scan",
    "rug",
    "honeypot",
    "honey pot",
    "rug pull",
    "rugpull",
    "check this token",
    "check this coin",
    "check this address",
    "check this contract",
    "is this a scam",
    "scam check",
    "token check",
    "is it safe",
)
_INTENT_TRACE_PHRASES = (
    "trace",
    "who funded",
    "who is behind",
    "who sent",
    "related wallets",
    "related wallet",
    "cluster",
    "entity",
    "first funder",
    "funding source",
    "whale",
    "syndicate",
)
# Address + a bare forensics word (who/what/related/trace) also implies trace.
_FORENSICS_WORD_RE = re.compile(r"\b(?:who|what|related|trace)\b", re.IGNORECASE)
# "ta" is matched as a standalone word (\b) so it doesn't fire on "data".
_TA_KEYWORD_RE = re.compile(
    r"\b(?:rsi|macd|chart|support|resistance|trend|technicals|candles|ta|ema|sma|bollinger)\b"
    r"|moving average|moving averages|moving avg|technical analysis",
    re.IGNORECASE,
)
_INTENT_MARKET_PHRASES = (
    "market",
    "fear greed",
    "fear & greed",
    "fear and greed",
    "how's the market",
    "how is the market",
    "market cap",
    "market overview",
    "btc/eth",
    "eth/sol",
    "btc eth sol",
)

# Suspicious-token asks: "list me the shady tokens you've seen launching".
# Deliberately does NOT include bare "honeypot"/"scam" (those stay with the
# scan intent for single-token checks); "honeypot tokens" / "scam tokens"
# (the plural list shape) are included. Checked AFTER scan/trace/price so a
# single-address or single-symbol security/price query is never hijacked.
_INTENT_SUSPICIOUS_PHRASES = (
    "suspicious",
    "shady",
    "sketchy",
    "scammy",
    "scam tokens",
    "rug",
    "honeypot tokens",
    "recent launches",
    "new tokens",
    "tokens that launched",
    "tokens launching",
    "looks like a scam",
    "red flags",
)
_SUS_STANDALONE_RE = re.compile(r"\bsus\b", re.IGNORECASE)
# "what is a honeypot" is an educational question, not a token list request —
# exclude definitional questions unless they also use list-request language.
_DEFINITION_QUESTION_RE = re.compile(r"^\s*what(?:'s| is| are)\b", re.IGNORECASE)
_LIST_REQUEST_RE = re.compile(
    r"\b(list|show|give me|found|noticed|seen|spot|find|analysis|analyze)\b",
    re.IGNORECASE,
)

# News — single words get word boundaries so "newsletter" does not fire as
# news; multi-word phrases match as whole phrases. Checked AFTER market so
# explicit market phrasing ("how's the market") stays market, and after the
# ta/scan/trace/price branches so a price/scan query is never reclassified.
_NEWS_KEYWORD_RE = re.compile(
    r"\b(?:news|headlines|latest|breaking|stories)\b"
    r"|\btop stories\b|\bbig news\b|\bwhat's happening\b|\bwhat happened today\b",
    re.IGNORECASE,
)

# Prediction-market asks: "show me interesting prediction markets", "what are
# the odds on X", "polymarket insider trading markets", "will X happen",
# "bet on ...". "insider" is deliberately broad — it catches the Polymarket
# "insider trading"-style markets the bot wants to surface. "market" alone
# stays the market intent (checked first in _detect_tool_intent).
_INTENT_PREDICTION_PHRASES = (
    "prediction market",
    "prediction markets",
    "polymarket",
    "bet on",
    "bets on",
    "betting on",
    "odds",
    "insider",
    "election market",
    "election markets",
    "crypto market odds",
    "will happen",
    "will win",
)

# "will <X> happen?" — prediction-market phrasing with a gap between the words.
_WILL_HAPPEN_RE = re.compile(r"\bwill\b.{0,60}\bhappen\b", re.IGNORECASE)

# Perp-market asks: "hyperliquid funding rate", "show me perps", "open interest
# on HYPE". "hype" is deliberately included (the HYPE/Hyperliquid ticker) but
# this branch sits AFTER price so a "$HYPE" price query stays price, and BEFORE
# market so "hyperliquid market" routes to perps, not the broad snapshot.
_INTENT_PERP_PHRASES = (
    "hyperliquid",
    "hype",
    "perps",
    "perp market",
    "perp markets",
    "funding rate",
    "funding rates",
    "open interest",
)


def _detect_tool_intent(message: str) -> str | None:
    """Classify ``message`` into a tool intent: price/scan/ta/market/news/trace/suspicious/prediction/perp/None.

    Priority: explicit TA keywords > scan (address/symbol + security words)
    > trace (address + forensics phrases, or address + who/what/related/trace)
    > price (phrase + symbol/name, or a lone ``$SYMBOL``) > suspicious (shady /
    new-token LIST asks — checked after scan/trace/price so a single-token
    security/price query stays scan/price) > prediction (Polymarket-style
    market asks, checked BEFORE market so "prediction market"/"election market"
    phrasing does not fall into the generic market intent) > market phrases >
    news keywords.
    News is deliberately checked AFTER market so explicit market/fear-greed/
    btc-eth-sol phrasing ("how's the market") stays market, and after
    ta/scan/trace/price so a price/scan query is not reclassified. Returns
    None when no clear intent (general question → normal RAG/LLM).
    """
    low = message.lower().strip()
    if not low:
        return None

    has_address = bool(_EVM_ADDRESS_RE.search(low)) or bool(_SOLANA_ADDRESS_RE.search(low))
    has_symbol = _SYMBOL_RE.search(low) is not None
    has_symbol_or_name = _extract_price_symbol(low) is not None
    scan_kw = any(k in low for k in _INTENT_SCAN_KEYWORDS)

    # TA — explicit technical-analysis keywords take priority.
    if _TA_KEYWORD_RE.search(low):
        return "ta"

    # Trace — wallet/entity forensics. Sits AFTER scan (security words win)
    # but BEFORE price (an explicit price phrase wins over a generic "what").
    if has_address:
        if scan_kw:
            return "scan"
        if any(k in low for k in _INTENT_TRACE_PHRASES):
            return "trace"
        if any(p in low for p in _INTENT_PRICE_PHRASES):
            return "price"
        if _FORENSICS_WORD_RE.search(low):
            return "trace"
        return None
    if scan_kw and has_symbol_or_name:
        return "scan"

    # Price — price phrase + symbol/name, or a lone $SYMBOL with no other intent.
    if has_symbol_or_name:
        if any(p in low for p in _INTENT_PRICE_PHRASES):
            return "price"
        if has_symbol and not scan_kw and not any(m in low for m in _INTENT_MARKET_PHRASES):
            return "price"

    # Suspicious — "list me the shady/new tokens" ask. Checked AFTER the
    # scan/trace/price branches (single-address/symbol security or price
    # queries returned above) and BEFORE market/news so a shady-tokens ask
    # never falls through to the generic market/news fallback.
    if _is_suspicious_list_request(low):
        return "suspicious"

    # Prediction — Polymarket-style markets. Checked BEFORE market so explicit
    # "prediction market"/"election market"/"crypto market odds" phrasing does
    # not fall into the generic market intent.
    if any(p in low for p in _INTENT_PREDICTION_PHRASES) or _WILL_HAPPEN_RE.search(low):
        return "prediction"

    # Perp — Hyperliquid funding/OI asks. Checked AFTER price (a "$HYPE" price
    # query returns above) and prediction, but BEFORE market so "hyperliquid
    # market" routes to perps, not the broad market snapshot.
    if any(p in low for p in _INTENT_PERP_PHRASES):
        return "perp"

    # Market.
    if any(m in low for m in _INTENT_MARKET_PHRASES):
        return "market"

    # News — checked AFTER market (explicit market phrasing wins) and after
    # the ta/scan/trace/price branches above.
    if _NEWS_KEYWORD_RE.search(low):
        return "news"

    return None


def _is_suspicious_list_request(low: str) -> bool:
    """True when ``low`` asks for a list/analysis of shady or new tokens.

    Requires a suspicious keyword AND a list/analysis shape: a definitional
    "what is X" question (unless it also uses list-request language such as
    "list"/"show"/"noticed") is not a suspicious-token ask. Single-address or
    single-symbol queries never reach this — the scan/trace/price branches in
    ``_detect_tool_intent`` return first.
    """
    if not low:
        return False
    matched = any(p in low for p in _INTENT_SUSPICIOUS_PHRASES) or bool(_SUS_STANDALONE_RE.search(low))
    if not matched:
        return False
    # A definitional "what is X" question is not a suspicious-token ask unless
    # it also uses list-request language ("list"/"show"/"noticed"...).
    if _DEFINITION_QUESTION_RE.search(low):
        return _LIST_REQUEST_RE.search(low) is not None
    return True
