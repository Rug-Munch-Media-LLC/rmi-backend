"""Chat tool-use layer — live data grounding for the RMI chat.

The chat LLM must ground price / scan / TA / market answers in real numbers
instead of hallucinating. This module provides:

- ``_detect_tool_intent`` — pure classifier over the user message.
- ``_exec_price`` / ``_exec_scan`` / ``_exec_trace`` / ``_exec_ta`` /
  ``_exec_market`` / ``_exec_news`` / ``_exec_suspicious`` — best-effort async
  executors that call the DIRECT data modules (no x402 endpoints). Each is
  fail-open: returns ``None`` on any error/timeout and NEVER raises.
- ``_run_tool_for_intent`` — dispatcher with an 8s timeout.

Everything here is defensive by design: a failed executor is invisible to
the chat route (it simply falls through to RAG/LLM). ``chat.py`` remains the
integration point — see ``_fetch_tool_context`` there.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Tool intent classifier ────────────────────────────────────────────
# Mirrors the address/symbol regexes in app/routers/chat.py (kept local to
# avoid a circular import — chat.py imports this module).
_EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SYMBOL_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,10})\b")

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

# Natural token names → display symbol (price extraction without a $ prefix).
_TOKEN_NAME_TO_SYMBOL: dict[str, str] = {
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
    "solana": "SOL",
    "sol": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
    "binance coin": "BNB",
    "bnb": "BNB",
    "cardano": "ADA",
    "ada": "ADA",
    "dogecoin": "DOGE",
    "doge": "DOGE",
    "avalanche": "AVAX",
    "avax": "AVAX",
    "polkadot": "DOT",
    "dot": "DOT",
    "polygon": "MATIC",
    "matic": "MATIC",
    "chainlink": "LINK",
    "link": "LINK",
    "shiba inu": "SHIB",
    "shib": "SHIB",
    "pepe": "PEPE",
    "tether": "USDT",
    "usdt": "USDT",
    "usd coin": "USDC",
    "usdc": "USDC",
    "litecoin": "LTC",
    "ltc": "LTC",
    "cosmos": "ATOM",
    "atom": "ATOM",
    "tron": "TRX",
    "trx": "TRX",
    "near": "NEAR",
    "aptos": "APT",
    "apt": "APT",
    "arbitrum": "ARB",
    "arb": "ARB",
    "optimism": "OP",
    "op": "OP",
    "sui": "SUI",
}

# Symbol → CoinGecko ID for the free /simple/price path (no key required).
_COINGECKO_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "bnb": "binancecoin",
    "ada": "cardano",
    "doge": "dogecoin",
    "avax": "avalanche-2",
    "dot": "polkadot",
    "matic": "matic-network",
    "polygon": "matic-network",
    "link": "chainlink",
    "shib": "shiba-inu",
    "pepe": "pepe",
    "usdt": "tether",
    "usdc": "usd-coin",
    "ltc": "litecoin",
    "atom": "cosmos",
    "trx": "tron",
    "ton": "the-open-network",
    "near": "near",
    "apt": "aptos",
    "arb": "arbitrum",
    "op": "optimism",
    "sui": "sui",
}


def _detect_tool_intent(message: str) -> str | None:
    """Classify ``message`` into a tool intent: price/scan/ta/market/news/trace/suspicious/prediction/None.

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


def _extract_price_symbol(message: str) -> str | None:
    """Extract a display symbol: ``$TICKER`` mention, else a known token name."""
    m = _SYMBOL_RE.search(message)
    if m:
        return m.group(1).upper()
    low = message.lower()
    for name, sym in _TOKEN_NAME_TO_SYMBOL.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return sym
    return None


def _extract_first_address(message: str) -> str | None:
    """First EVM or Solana address in ``message``, or None."""
    m = _EVM_ADDRESS_RE.search(message)
    if m:
        return m.group(0)
    m = _SOLANA_ADDRESS_RE.search(message)
    return m.group(0) if m else None


def _chain_for_address(address: str) -> str:
    """Heuristic chain id for an address: solana base58 vs EVM 0x."""
    return "solana" if _SOLANA_ADDRESS_RE.search(address) else "ethereum"


# ── Executors ─────────────────────────────────────────────────────────
# Each executor is async + fail-open: catches everything, returns None.
# They call the DIRECT modules (ohlcv_engine / scanner service / CoinGecko
# connector / DexScreener) — never the broken x402 endpoints.


def _to_float(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _best_dex_pair(data: Any) -> dict[str, Any] | None:
    """Best DexScreener pair by USD liquidity (free, no key)."""
    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None

    def _liq(p: Any) -> float:
        liq = p.get("liquidity") if isinstance(p, dict) else None
        return _to_float(liq.get("usd")) if isinstance(liq, dict) else 0.0

    return max(pairs, key=_liq) if pairs else None


async def _exec_price(message: str) -> str | None:
    """Fetch a live token price. Returns a one-line summary or None."""
    symbol = _extract_price_symbol(message)
    if not symbol:
        return None
    try:
        address = _extract_first_address(message)
        if address:
            chain = _chain_for_address(address)
            if chain == "solana":
                from app.databus.provider_implementations.price import _dexscreener_price

                pair = _best_dex_pair(await _dexscreener_price(token=address))
                if not pair:
                    return None
                price = _to_float(pair.get("priceUsd"))
                change = _to_float(pair.get("priceChange24h"))
            else:
                from app.coingecko_connector import get_coingecko_connector

                data = await get_coingecko_connector().get_token_price_by_contract("ethereum", address)
                if not data:
                    return None
                price = _to_float(data.get("usd"))
                change = _to_float(data.get("usd_24h_change"))
        else:
            from app.coingecko_connector import get_coingecko_connector

            cg_id = _COINGECKO_IDS.get(symbol.lower())
            if not cg_id:
                return None
            data = await get_coingecko_connector().get_token_price(cg_id)
            if not data:
                return None
            price = _to_float(data.get("usd"))
            change = _to_float(data.get("usd_24h_change"))

        if not price or price <= 0:
            return None
        change_str = f" (24h {change:+.1f}%)" if change is not None else ""
        return f"{symbol.upper()} is ${price:,.2f}{change_str}"
    except Exception as e:
        logger.debug("chat_tool_price_failed", error=str(e)[:120])
        return None


async def _exec_scan(message: str) -> str | None:
    """Run the scanner entrypoint and return a compact risk summary or None."""
    address = _extract_first_address(message)
    if not address:
        return None
    chain = _chain_for_address(address)
    try:
        from app.domains.scanner.service.scan import _risk_level_from_safety, scan_token

        result = await scan_token(address, chain=chain, tiers=["free"])
        safety = float(getattr(result, "safety_score", 0) or 0)
        risk = _risk_level_from_safety(safety) or "UNKNOWN"
        flags = [str(f) for f in (getattr(result, "risk_flags", None) or [])][:5]
        flags_str = ", ".join(flags) if flags else "None"
        return f"{address[:14]}… on {chain}: Safety score {safety:.0f}/100 ({risk}). Flags: {flags_str}."
    except Exception as e:
        logger.debug("chat_tool_scan_failed", error=str(e)[:120])
        return None


# Per-call timeout for the trace resolver / graph fallback (the dispatcher's
# outer 8s cap still applies when routed through ``_run_tool_for_intent``).
_TRACE_TIMEOUT_SECONDS = 10.0


def _render_trace_summary(result: dict[str, Any]) -> str | None:
    """Compact forensics summary from a cross-chain entity resolution."""
    if not isinstance(result, dict):
        return None
    links = result.get("addresses")
    if not isinstance(links, list):
        links = []
    short = str(result.get("address") or "?")[:14]
    entity = result.get("entity_type") or "unknown"
    risk = result.get("risk") or "unknown"

    parts = [f"entity {entity}, risk {risk}"]
    deployer_chains = sorted(
        {
            str(link.get("chain", ""))
            for link in links
            if isinstance(link, dict) and link.get("evidence_type") == "same_deployer"
        }
    )
    funder_links = [link for link in links if isinstance(link, dict) and link.get("evidence_type") == "same_funder"]
    if deployer_chains:
        parts.append("deployer on " + ", ".join(deployer_chains))
    if funder_links:
        details = funder_links[0].get("details")
        funder = str(details.get("funder") or "?") if isinstance(details, dict) else "?"
        cand_chains = sorted({str(link.get("chain", "")) for link in funder_links})
        cand_addrs = sorted({str(link.get("address", ""))[:10] for link in funder_links})
        funder_short = funder[:10] + "…" if len(funder) > 10 else funder
        parts.append(
            f"first funder {funder_short}; same-funder candidates on {', '.join(cand_chains)} ({', '.join(cand_addrs)})"
        )
    if links:
        all_chains = ", ".join(sorted({str(link.get("chain", "")) for link in links if isinstance(link, dict)}))
        parts.append(f"{len(links)} cross-chain link(s): {all_chains}")
    else:
        parts.append("no cross-chain links found")
    return f"Trace {short}…: " + "; ".join(parts) + "."


def _render_graph_summary(graph: dict[str, Any]) -> str | None:
    """Compact fallback summary from an entity graph build (node/edge counts)."""
    if not isinstance(graph, dict):
        return None
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    edges = graph.get("edges")
    if not isinstance(edges, list):
        edges = []
    stats = graph.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    center = graph.get("center")
    addr = str(center.get("address", ""))[:14] if isinstance(center, dict) else ""

    parts = [f"graph: {len(nodes)} node(s), {len(edges)} edge(s)"]
    entity_types = stats.get("entity_types")
    if isinstance(entity_types, list) and entity_types:
        parts.append("types: " + ", ".join(str(t) for t in entity_types[:5]))
    risk_dist = stats.get("risk_distribution")
    if isinstance(risk_dist, dict) and risk_dist.get("high"):
        parts.append(f"{risk_dist['high']} high-risk node(s)")
    prefix = f"Trace {addr}…: " if addr else "Trace: "
    return prefix + "; ".join(parts) + "."


async def _exec_trace(message: str) -> str | None:
    """Wallet/entity forensics: resolve cross-chain entity, else graph summary.

    Calls ``resolve_cross_chain_entity`` directly (no x402 endpoints) with a
    per-call timeout. When resolution is unavailable or empty, falls back to
    ``build_entity_graph`` for a node/edge summary. Fail-open → None.
    """
    address = _extract_first_address(message)
    if not address:
        return None
    chain = _chain_for_address(address)
    resolved: dict[str, Any] | None = None
    try:
        from app.domains.scanner.engines.cross_chain_entity import resolve_cross_chain_entity

        resolved = await asyncio.wait_for(
            resolve_cross_chain_entity(address, chain),
            timeout=_TRACE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug("chat_tool_trace_resolve_failed", error=str(e)[:120])

    if isinstance(resolved, dict) and resolved.get("result") == "scanned":
        summary = _render_trace_summary(resolved)
        if summary:
            return summary

    # Fallback: entity graph node/edge summary when resolution is unavailable.
    try:
        from app.domains.scanner.entity_graph import build_entity_graph

        graph = await asyncio.wait_for(
            asyncio.to_thread(build_entity_graph, address, chain),
            timeout=_TRACE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug("chat_tool_trace_graph_failed", error=str(e)[:120])
        return None
    return _render_graph_summary(graph)


# Defensive import: the TA domain (app/domains/ta) is being built by a
# parallel agent. Until it lands, these stay None and _exec_ta falls back to
# an inline RSI + change summary.
try:
    from app.domains.ta.summary import ta_summary, ta_summary_text  # type: ignore[no-redef]
except ImportError:  # pragma: no cover — parallel agent may not have landed
    ta_summary = None  # type: ignore[assignment]
    ta_summary_text = None  # type: ignore[assignment]


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder-smoothed RSI over closing prices."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _inline_ta_text(symbol: str, closes: list[float], current: float, change: float) -> str:
    rsi = _rsi(closes) if len(closes) >= 2 else None
    rsi_str = f"RSI(14): {rsi:.1f}" if rsi is not None else "RSI: n/a"
    return f"{symbol} ${current:,.4g} (24h {change:+.1f}%). {rsi_str}."


async def _exec_ta(message: str) -> str | None:
    """Fetch candles + compute TA (ta_summary when available, else inline RSI)."""
    symbol = _extract_price_symbol(message)
    if not symbol:
        return None
    address = _extract_first_address(message)
    chain = _chain_for_address(address) if address else "ethereum"
    token = address or symbol.lower()
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv

        data = await fetch_ohlcv(token=token, chain=chain, timeframe="1h", limit=48)
        if not data or not data.get("candles"):
            return None
        candles = [c for c in data["candles"] if isinstance(c, dict)]
        if not candles:
            return None
        summary = data.get("summary") or {}
        current = _to_float(summary.get("current_price")) or _to_float(candles[-1].get("close")) or 0.0
        if not current:
            return None
        change = _to_float(summary.get("price_change_pct")) or 0.0
        closes = [_to_float(c.get("close")) for c in candles if c.get("close") is not None]
        closes = [c for c in closes if c is not None]

        # Prefer the TA domain's summary when available (parallel agent);
        # fall back to inline RSI + change so TA questions still get numbers.
        if ta_summary is not None:
            try:
                summary_obj = (
                    ta_summary(candles, symbol=symbol)
                    if not inspect.iscoroutinefunction(ta_summary)
                    else await ta_summary(candles, symbol=symbol)
                )
                if summary_obj and ta_summary_text is not None:
                    text = ta_summary_text(summary_obj)
                    if inspect.isawaitable(text):
                        text = await text
                    if isinstance(text, str) and text.strip():
                        return text
            except Exception as e:
                logger.debug("chat_tool_ta_summary_failed", error=str(e)[:120])
        return _inline_ta_text(symbol.upper(), closes, current, change)
    except Exception as e:
        logger.debug("chat_tool_ta_failed", error=str(e)[:120])
        return None


async def _fetch_fear_greed() -> str | None:
    """Fear & Greed index from alternative.me (free, no key). Best-effort."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            if r.status_code != 200:
                return None
            row = r.json().get("data", [{}])[0]
            value = row.get("value")
            if not value:
                return None
            label = row.get("value_classification", "")
            return f"{value}/100{(' (' + label + ')') if label else ''}"
    except Exception:
        return None


async def _exec_market(message: str = "") -> str | None:
    """Live BTC/ETH/SOL snapshot + fear & greed (reuses chat's market fetch)."""
    try:
        from app.routers.chat import _fetch_market_context

        ctx = await _fetch_market_context()
    except Exception:
        ctx = {}
    parts: list[str] = []
    for key, label in (("btc", "BTC"), ("eth", "ETH"), ("sol", "SOL")):
        price = _to_float(ctx.get(f"{key}_price"))
        if not price:
            continue
        change = _to_float(ctx.get(f"{key}_change_24h"))
        change_str = f" ({change:+.1f}%)" if change is not None else ""
        parts.append(f"{label} ${price:,.2f}{change_str}")
    if not parts:
        return None
    text = " | ".join(parts)
    try:
        fng = await _fetch_fear_greed()
        if fng:
            text += f" | Fear & Greed: {fng}"
    except Exception as e:
        logger.debug("chat_tool_fng_failed", error=str(e)[:120])
    return f"Market snapshot: {text}"


# ── News ────────────────────────────────────────────────────────────
# Reuses the Qdrant news_v2 helpers in app/routers/news (imported lazily) —
# never re-implements a news query. Fail-open → None like every executor.

_NEWS_SCROLL_WINDOW = 50  # scroll a window, then keep the newest 10
_NEWS_MAX_ITEMS = 10


def _relative_time(published_at: str | None) -> str:
    """Compact "3h ago" from an ISO-8601 timestamp; "" when unparseable."""
    if not published_at:
        return ""
    try:
        raw = str(published_at).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        seconds = max(int((datetime.now(UTC) - dt).total_seconds()), 0)
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except (TypeError, ValueError, OverflowError):
        return ""


async def _exec_news(message: str = "") -> str | None:
    """Top headlines from the Qdrant news_v2 feed, newest first. Fail-open.

    Calls the in-process ``_scroll_news_v2`` / ``_point_to_article`` helpers
    from ``app.routers.news`` (never the HTTP endpoints, never a second
    network round-trip beyond the single scroll). Renders a compact
    "Top stories" block capped at 10 items; returns None on failure/empty.
    """
    try:
        from app.routers.news import _point_to_article, _scroll_news_v2

        points = await _scroll_news_v2(limit=_NEWS_SCROLL_WINDOW)
        if not points:
            return None
        articles: list[dict[str, Any]] = []
        for p in points:
            a = _point_to_article(p)
            if not isinstance(a, dict):
                continue
            if not str(a.get("title") or "").strip():
                continue
            articles.append(a)
        if not articles:
            return None

        # Newest first (ISO-8601 string compare; missing published_at sinks).
        articles.sort(key=lambda a: str(a.get("published_at") or ""), reverse=True)

        lines: list[str] = []
        for i, a in enumerate(articles[:_NEWS_MAX_ITEMS], start=1):
            title = str(a.get("title") or "").strip()
            source = str(a.get("source") or "").strip() or "?"
            rel = _relative_time(a.get("published_at"))
            rel_str = f" ({rel})" if rel else ""
            url = str(a.get("url") or a.get("source_url") or "").strip()
            lines.append(f"{i}. {title} — {source}{rel_str}")
            if url:
                lines.append(f"   {url}")
        if not lines:
            return None
        return "Top stories:\n" + "\n".join(lines)
    except Exception as e:
        logger.debug("chat_tool_news_failed", error=str(e)[:120])
        return None


# ── Suspicious-tokens listing ────────────────────────────────────────
# "Give me the shady tokens you've seen launching" must surface REAL tokens
# the bot has observed, with concrete reasons — never a generic lecture and
# never fabricated names. Pulls launch records via the in-process detector +
# GMGN provider (no x402 endpoints), then ranks by the consolidated rug-risk
# score (honeypot / mintable / LP-lock / bundle / age / liquidity /
# known-scammer overlap) when the pure scorer is available, else the legacy
# age/liquidity heuristic. Fail-open → None (chat falls back to RAG/LLM).

# Defensive import: the consolidated rug-risk scorer
# (app/domains/scanner/rug_risk_score) is a pure 0-100 scorer; when it is
# unavailable (or mid-refactor and not importable), _exec_suspicious falls
# back to the legacy _launch_suspicion_score. Broad except: a broken module
# (e.g. SyntaxError while being edited) must never break the chat tool layer.
try:
    from app.domains.scanner.rug_risk_score import risk_verdict_text, rug_risk_score
except Exception:  # defensive: scorer availability is optional
    rug_risk_score = None  # type: ignore[assignment]
    risk_verdict_text = None  # type: ignore[assignment]

_SUSPICIOUS_LAUNCH_LIMIT = 30  # detector window to inspect
_SUSPICIOUS_FRESH_LIMIT = 20  # GMGN fresh pairs to supplement with
_SUSPICIOUS_MAX_ROWS = 8  # cap on rendered list
_SUSPICIOUS_YOUNG_SECONDS = 3600  # <1h age is the classic rug window
_SUSPICIOUS_LIQUIDITY_LOW = 1000.0  # <$1k LP
_SUSPICIOUS_LIQUIDITY_THIN = 5000.0  # <$5k LP
_SUSPICIOUS_MC_TINY = 5000.0  # market cap below this is a red flag
# Top-N newest launches that get per-token security enrichment (honeypot /
# mintable / LP-lock / bundle network checks are capped in the detector —
# matches app.databus.token_launch_detector._SECURITY_ENRICH_LIMIT).
_SUSPICIOUS_ENRICH_MAX = 5

_LAUNCH_ENTITY_KEYS = (
    "address",
    "creator",
    "deployer",
    "owner",
    "wallet",
    "maker",
    "launcher",
    "creator_address",
    "deployer_address",
    "owner_address",
)


def _is_truthy(value: Any) -> bool:
    """Best-effort truthiness for provider flag fields ("true"/1/"yes"/...)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "locked", "mintable", "honeypot")
    return False


def _suspicious_flags(launch: dict[str, Any]) -> list[str]:
    """Concrete flag reasons present in the launch record (empty when absent).

    Reads both the legacy flat fields (``is_honeypot``/``is_mintable``/
    ``lp_locked`` from GMGN/provider records) and the enriched ``security``
    sub-dict added by ``TokenLaunchDetector.enrich_launches``. Fields that are
    absent or unknown (None) never fire a reason — fail-closed.
    """
    flags: list[str] = []
    sec = launch.get("security")
    security = sec if isinstance(sec, dict) else {}

    for key in ("is_honeypot", "honeypot", "flagged_honeypot"):
        if _is_truthy(launch.get(key)) or _is_truthy(security.get("honeypot")):
            flags.append("honeypot")
            break
    for key in ("is_mintable", "mintable", "mintable_flag"):
        if _is_truthy(launch.get(key)) or _is_truthy(security.get("mintable")):
            flags.append("mintable")
            break
    if security.get("lp_locked") is not None and not _is_truthy(security.get("lp_locked")):
        flags.append("LP not locked")
    for key in ("lp_locked", "is_lp_locked", "liquidity_locked"):
        val = launch.get(key)
        if val is not None and not _is_truthy(val):
            flags.append("LP not locked")
            break
    if _is_truthy(security.get("bundle_suspect")):
        flags.append("bundled snipers")
    raw_flags = launch.get("risk_flags") or launch.get("flags")
    if isinstance(raw_flags, list):
        for item in raw_flags:
            text = str(item).strip()
            if text and text.lower() not in ("none", "null"):
                flags.append(text[:40])
                break
    elif isinstance(raw_flags, str):
        text = raw_flags.strip()
        if text and text.lower() not in ("none", "null"):
            flags.append(text[:40])
    return flags


def _launch_age_seconds(launch: dict[str, Any]) -> float | None:
    """Launch age in seconds from ``age_seconds`` or ``created_at``; None when unknown."""
    try:
        age = launch.get("age_seconds")
        if age is not None and float(age) > 0:
            return float(age)
    except (TypeError, ValueError):
        pass
    created = launch.get("created_at")
    if created is None:
        return None
    try:
        ts = float(created)
        if ts > 1e11:  # epoch milliseconds
            ts /= 1000.0
        return max(0.0, datetime.now(UTC).timestamp() - ts)
    except (TypeError, ValueError):
        return None


def _launch_scammer_hit(launch: dict[str, Any], scammers: set[str]) -> str | None:
    """Concrete reason when an entity field of the launch matches a known scammer."""
    if not scammers:
        return None
    for key in _LAUNCH_ENTITY_KEYS:
        val = launch.get(key)
        if val is None:
            continue
        norm = str(val).strip()
        if not norm:
            continue
        if norm.startswith("0x"):
            norm = norm.lower()
        if norm in scammers:
            return "deployer matches known scammer" if key != "address" else "address matches known scammer"
    # Enriched security sub-dict — the creator/owner resolution wired by the
    # launch detector ("known creator" reason when it matches a known scammer).
    sec = launch.get("security")
    if isinstance(sec, dict):
        creator = sec.get("creator_address") or sec.get("owner_address")
        if creator:
            norm = str(creator).strip().lower() if str(creator).strip().startswith("0x") else str(creator).strip()
            if norm in scammers:
                return "creator matches known scammer"
    return None


def _launch_suspicion_score(launch: dict[str, Any], scammers: set[str]) -> tuple[float, str]:
    """(suspicion_score, concrete_reason) for a launch record; (0.0, "") when none.

    Signals, all derived from the record's own fields:
      - honeypot / mintable / unlocked-LP / bundled snipers (+60 each) →
        "honeypot" / "mintable" / "LP not locked" / "bundled snipers" — from
        the flat provider flags OR the enriched ``security`` sub-dict; these
        rank ABOVE age/liquidity so concrete scam signals always win.
      - age < 1h                      (+40)  → "age 4m"
      - liquidity 0 / <$1k / <$5k     (+50/35/15) → "0 liquidity" / "LP $850"
      - entity matches known scammer  (+100) → "deployer matches known scammer"
                                              / "creator matches known scammer"
      - market cap < $5k              (+10)  → "MC $1.2k"
    """
    score = 0.0
    reasons: list[str] = []

    age_s = _launch_age_seconds(launch)
    if age_s is not None and age_s < _SUSPICIOUS_YOUNG_SECONDS:
        score += 40
        minutes = int(age_s // 60)
        reasons.append(f"age {minutes}m" if minutes >= 1 else "age <1m")

    liq = _to_float(launch.get("liquidity"))
    if liq is not None:
        if liq <= 0:
            score += 50
            reasons.append("0 liquidity")
        elif liq < _SUSPICIOUS_LIQUIDITY_LOW:
            score += 35
            reasons.append(f"LP ${liq:,.0f}")
        elif liq < _SUSPICIOUS_LIQUIDITY_THIN:
            score += 15
            reasons.append(f"LP ${liq:,.0f}")

    scam_reason = _launch_scammer_hit(launch, scammers)
    if scam_reason:
        score += 100
        reasons.append(scam_reason)

    for flag in _suspicious_flags(launch):
        score += 60
        reasons.append(flag)

    mc = _to_float(launch.get("market_cap"))
    if mc is not None and 0 < mc < _SUSPICIOUS_MC_TINY:
        score += 10
        reasons.append(f"MC ${mc:,.0f}")

    if not reasons:
        return 0.0, ""
    deduped = list(dict.fromkeys(reasons))  # drop duplicate flag wording
    return score, "; ".join(deduped)[:120]


def _launch_rug_signals(launch: dict[str, Any], scammers: set[str]) -> dict[str, Any]:
    """Build the consolidated rug-risk signal dict from a launch record.

    Feeds the enriched ``security`` sub-dict (honeypot / mintable /
    lp_locked / bundle_suspect), the legacy flat provider flags, the record's
    own age / liquidity / market-cap, and any creator/deployer overlap with
    known scammers. ``rug_risk_score`` tolerates unknown keys (absent signal =
    no evidence); a tiny market cap alone is surfaced via the fallback scorer
    when the consolidated one yields nothing.
    """
    sec = launch.get("security")
    security = sec if isinstance(sec, dict) else {}

    def _pick(*keys: str) -> Any:
        for key in keys:
            val = launch.get(key)
            if val is not None:
                return val
        return None

    return {
        "honeypot": (
            security.get("honeypot")
            if security.get("honeypot") is not None
            else _pick("is_honeypot", "honeypot", "flagged_honeypot")
        ),
        "mintable": (
            security.get("mintable")
            if security.get("mintable") is not None
            else _pick("is_mintable", "mintable", "mintable_flag")
        ),
        "lp_locked": (
            security.get("lp_locked")
            if security.get("lp_locked") is not None
            else _pick("lp_locked", "is_lp_locked", "liquidity_locked")
        ),
        "bundled": (
            security.get("bundle_suspect")
            if security.get("bundle_suspect") is not None
            else _pick("bundle_suspect", "bundled", "is_bundled")
        ),
        "deployer_repeat": _pick("deployer_repeat", "repeat_rug", "is_repeat_deployer"),
        "known_scammer": bool(_launch_scammer_hit(launch, scammers)),
        "age_seconds": _launch_age_seconds(launch),
        "liquidity": _to_float(launch.get("liquidity")),
        "market_cap": _to_float(launch.get("market_cap")),
    }


def _launch_rug_risk(launch: dict[str, Any], scammers: set[str]) -> dict[str, Any] | None:
    """Consolidated rug-risk result (score/verdict/flags/reasons) or None.

    Prefers the pure ``rug_risk_score`` over the legacy heuristic: returns the
    scorer's result ONLY when it produced concrete reasons. None (scorer
    unavailable or silent) means the caller falls back to
    ``_launch_suspicion_score``.
    """
    if rug_risk_score is None:
        return None
    try:
        result = rug_risk_score(_launch_rug_signals(launch, scammers))
    except Exception as e:
        logger.debug("chat_tool_rug_risk_failed", error=str(e)[:120])
        return None
    if not isinstance(result, dict):
        return None
    if not (result.get("reasons") or result.get("flags")):
        return None
    return result


def _rug_risk_segment(result: dict[str, Any]) -> str:
    """Render ``score 92/100 CRITICAL — honeypot · mintable`` from a rug-risk result.

    Reuses ``risk_verdict_text``'s canonical flag→text mapping (top 3 flags,
    ``kol_verified`` hidden unless alone); falls back to a bare
    ``score N/100 VERDICT`` when there are no flags to show.
    """
    if not isinstance(result, dict):
        return ""
    score = int(result.get("score") or 0)
    verdict = str(result.get("verdict") or "low").upper()
    rendered = risk_verdict_text(result) if risk_verdict_text is not None else ""
    tail = ""
    if rendered and "—" in rendered:
        tail = rendered.split("—", 1)[1].strip()
        if tail.lower() == "no significant risk flags":
            tail = ""
    if not tail:
        return f"score {score}/100 {verdict}"
    return f"score {score}/100 {verdict} — {tail}"


def _rank_suspicious(launches: list[dict[str, Any]], scammers: set[str]) -> list[tuple[dict[str, Any], str]]:
    """Rank launches by consolidated rug-risk (desc); only records with a reason.

    Prefers the pure ``rug_risk_score`` (honeypot/mintable/LP-lock/bundle +
    age/liquidity + known-scammer overlap) and falls back to the legacy
    ``_launch_suspicion_score`` heuristic when the consolidated scorer is
    unavailable or silent. Records with NO concrete reason are dropped.
    """
    ranked: list[tuple[float, dict[str, Any], str]] = []
    for launch in launches:
        if not isinstance(launch, dict):
            continue
        result = _launch_rug_risk(launch, scammers)
        if result is not None:
            reason = _rug_risk_segment(result)
            if reason:
                ranked.append((float(result.get("score") or 0), launch, reason))
                continue
        score, reason = _launch_suspicion_score(launch, scammers)
        if score > 0 and reason:
            ranked.append((score, launch, reason))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [(launch, reason) for _, launch, reason in ranked]


def _merge_launches(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Concatenate launch records, deduping by address (symbol when address empty)."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for launch in [*primary, *extra]:
        if not isinstance(launch, dict):
            continue
        key = str(launch.get("address") or "").strip() or str(launch.get("symbol") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(launch)
    return merged


def _render_suspicious(ranked: list[tuple[dict[str, Any], str]]) -> str | None:
    """Numbered 🔴 list of the top suspicious launches; None when empty."""
    if not ranked:
        return None
    lines: list[str] = []
    for i, (launch, reason) in enumerate(ranked[:_SUSPICIOUS_MAX_ROWS], start=1):
        symbol = str(launch.get("symbol") or "???").strip().upper()
        chain = str(launch.get("chain") or "solana").strip() or "solana"
        address = str(launch.get("address") or "").strip()
        short = address[:10] + "…" if len(address) > 10 else (address or "?")
        lines.append(f"{i}. 🔴 {symbol} ({chain}) — {short} — {reason}")
    return "Suspicious recent launches:\n" + "\n".join(lines)


async def _exec_suspicious(message: str = "") -> str | None:
    """List recent launches that look shady, ranked by the 25-point rug-risk score.

    Fetches REAL launches via the in-process detector + GMGN provider (never
    the x402 endpoints), best-effort enriches the newest ones with the
    detector's security quick-checks (honeypot/mintable/LP-lock/creator/bundle),
    then ranks by the consolidated rug-risk score — feeding each launch's
    security fields + age/liquidity/mcap + creator/deployer known-scammer
    overlap into the pure scorer; falls back to the legacy age/liquidity
    heuristic when the scorer is unavailable or silent. Records without a
    concrete reason are dropped. Fail-open → None when no launch data is
    available — chat falls back to RAG/LLM. Never invents tokens or reasons.
    """
    launches: list[dict[str, Any]] = []
    try:
        from app.databus.token_launch_detector import get_launch_detector

        detector = get_launch_detector()
        raw = await detector.get_recent_launches(limit=_SUSPICIOUS_LAUNCH_LIMIT)
        if isinstance(raw, list):
            launches = raw
        # Best-effort security enrichment (honeypot/mintable/LP-lock/creator/
        # bundle). Runs only when the detector instance supports it; a missing
        # or failing enricher leaves the raw launch list untouched (fail-open).
        try:
            enricher = getattr(detector, "enrich_launches", None)
            if callable(enricher):
                enriched = await enricher(launches, max_checks=_SUSPICIOUS_ENRICH_MAX)
                if isinstance(enriched, list):
                    launches = enriched
        except Exception as e:
            logger.debug("chat_tool_suspicious_enrich_failed", error=str(e)[:120])
    except Exception as e:
        logger.debug("chat_tool_suspicious_launches_failed", error=str(e)[:120])

    # Supplement with GMGN fresh pairs (fail-open — never blocks the list).
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        fresh = await get_gmgn_provider().get_new_pairs("solana", limit=_SUSPICIOUS_FRESH_LIMIT)
        if isinstance(fresh, list):
            launches = _merge_launches(launches, fresh)
    except Exception as e:
        logger.debug("chat_tool_suspicious_gmgn_failed", error=str(e)[:120])

    if not launches:
        return None

    try:
        from app.databus.shill_detector import load_known_scammers

        scammers: set[str] = set(load_known_scammers())
    except Exception as e:
        logger.debug("chat_tool_suspicious_scammers_failed", error=str(e)[:120])
        scammers = set()

    return _render_suspicious(_rank_suspicious(launches, scammers))


# ── Prediction markets ────────────────────────────────────────────────
# "Show me interesting prediction markets" / "what are the odds on X" must
# surface REAL Polymarket data (Gamma API, no key) — never a generic lecture
# and never fabricated markets. Fail-open → None (chat falls back to RAG/LLM).


async def _exec_prediction(message: str = "") -> str | None:
    """Top interesting Polymarket prediction markets, ranked. Fail-open.

    Calls the in-process ``fetch_trending_markets`` (Gamma API, no key),
    ranks by volume/liquidity/recency, and renders a compact numbered list.
    Returns None on any error/empty — chat falls through to RAG/LLM.
    """
    try:
        from app.domains.markets.prediction_markets import (
            fetch_trending_markets,
            format_market_list,
            rank_interesting,
        )

        markets = await fetch_trending_markets(limit=15)
        ranked = rank_interesting(markets)
        if not ranked:
            return None
        text = format_market_list(ranked, top_n=8)
        return text if text.strip() else None
    except Exception as e:
        logger.debug("chat_tool_prediction_failed", error=str(e)[:120])
        return None


# ── Dispatcher ─────────────────────────────────────────────────────────

TOOL_TIMEOUT_SECONDS = 8.0

_EXECUTORS: dict[str, Any] = {
    "price": _exec_price,
    "scan": _exec_scan,
    "trace": _exec_trace,
    "ta": _exec_ta,
    "market": _exec_market,
    "news": _exec_news,
    "suspicious": _exec_suspicious,
    "prediction": _exec_prediction,
}


async def _run_tool_for_intent(message: str, intent: str) -> str | None:
    """Run the executor for a detected intent with a hard timeout. Fail-open.

    Returns None on unknown intent, executor error, or timeout — callers
    fall through to normal RAG/LLM. Never raises.
    """
    executor = _EXECUTORS.get(intent)
    if executor is None:
        return None
    try:
        results = await asyncio.wait_for(
            asyncio.gather(executor(message)),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("chat_tool_executor_failed", intent=intent)
        return None
    result = results[0] if results else None
    return result if isinstance(result, str) and result.strip() else None
