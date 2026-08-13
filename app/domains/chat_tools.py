"""Chat tool-use layer — live data grounding for the RMI chat.

The chat LLM must ground price / scan / TA / market answers in real numbers
instead of hallucinating. This module provides:

- ``_detect_tool_intent`` — pure classifier over the user message.
- ``_exec_price`` / ``_exec_scan`` / ``_exec_trace`` / ``_exec_ta`` /
  ``_exec_market`` — best-effort async executors that call the DIRECT data
  modules (no x402 endpoints). Each is fail-open: returns ``None`` on any
  error/timeout and NEVER raises.
- ``_run_tool_for_intent`` — dispatcher with an 8s timeout.

Everything here is defensive by design: a failed executor is invisible to
the chat route (it simply falls through to RAG/LLM). ``chat.py`` remains the
integration point — see ``_fetch_tool_context`` there.
"""

from __future__ import annotations

import asyncio
import inspect
import re
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
    """Classify ``message`` into a tool intent: price/scan/ta/market/trace/None.

    Priority: explicit TA keywords > scan (address/symbol + security words)
    > trace (address + forensics phrases, or address + who/what/related/trace)
    > price (phrase + symbol/name, or a lone ``$SYMBOL``) > market phrases.
    Returns None when no clear intent (general question → normal RAG/LLM).
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

    # Market.
    if any(m in low for m in _INTENT_MARKET_PHRASES):
        return "market"

    return None


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


# ── Dispatcher ─────────────────────────────────────────────────────────

TOOL_TIMEOUT_SECONDS = 8.0

_EXECUTORS: dict[str, Any] = {
    "price": _exec_price,
    "scan": _exec_scan,
    "trace": _exec_trace,
    "ta": _exec_ta,
    "market": _exec_market,
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
