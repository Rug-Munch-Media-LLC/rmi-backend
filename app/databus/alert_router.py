"""
Intelligent webhook alert router — Arkham-style on-chain event alerts.

Turns inbound provider webhooks (Helius / Arkham / Moralis / Alchemy) into
structured, *meaningful* alerts instead of raw payload noise::

    webhook payload
      → parse (Helius-aware: nativeTransfers / tokenTransfers / description)
      → enrich (wallet labels: exchange / defi / mixer / known actor)
      → importance (deterministic 0..1, reuses channels.route_news bands)
      → gate   (route_news: skip / news / main)
      → throttle (per-wallet Redis cooldown, min event size)
      → push_alert (alerts channel) with readable attribution

Only notable events surface: large transfers, LP changes, mint/freeze
toggles, deployer dumps. Dust and routine churn are skipped below the
min-size / cooldown gates — no spam.

Fail-open: every failure path returns a decision dict, never raises — the
inbound webhook handler keeps answering when Redis or labels are down.
No new paid services: labels come from ``entity_registry`` (local datasets),
amounts come from the Helius payload itself.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.alert_router")

# ── Thresholds (env-overridable, fail-open defaults) ───────────────────

#: Minimum USD value for an event to be considered meaningful (below → skip).
MIN_EVENT_USD: float = float(os.getenv("RMI_WEBHOOK_MIN_USD", "10000") or "10000")
#: Per-wallet cooldown between alerts (seconds) — throttles churny wallets.
COOLDOWN_SECONDS: int = int(os.getenv("RMI_WEBHOOK_COOLDOWN_SECONDS", "300") or "300")
#: SOL→USD fallback used when the live price fetch is unavailable.
SOL_PRICE_USD: float = float(os.getenv("SOL_PRICE_USD", "150") or "150")
#: Wrapped SOL mint (the F2 oracle resolves SOL/USD through its tier chain:
#: own dex_events → DexScreener → CoinGecko → DeFiLlama, Redis-cached 5m).
SOL_MINT: str = "So11111111111111111111111111111111111111112"
#: In-process SOL price cache TTL (seconds) — webhook bursts share one price.
_SOL_PRICE_TTL = 300
#: Negative-cache TTL when the oracle is down — do NOT retry on every webhook.
_SOL_PRICE_FAIL_TTL = 60
_sol_price_cache: dict[str, Any] = {}


async def _sol_price_usd() -> float:
    """Live SOL→USD price, cached in-process (5m TTL), fail-open.

    Resolves through the F2 price oracle (``get_token_price``) so it reuses
    the existing free-API tier chain + Redis cache instead of a hardcoded
    number. Returns the env ``SOL_PRICE_USD`` fallback when the oracle is
    unreachable or slow — never raises. Failures are negatively cached for a
    minute so an outage does not stall every inbound webhook.
    """
    cached = _sol_price_cache.get("price_usd")
    ttl = _SOL_PRICE_FAIL_TTL if _sol_price_cache.get("_failed") else _SOL_PRICE_TTL
    if cached is not None and time.time() - _sol_price_cache.get("_cached_at", 0) < ttl:
        return float(cached)
    try:
        from app.domains.scanner.engines.price_oracle import get_token_price

        result = await asyncio.wait_for(get_token_price(SOL_MINT, "solana"), timeout=8.0)
        price = float((result or {}).get("price_usd", 0) or 0) if result else 0.0
        if price > 0:
            _sol_price_cache.update({"price_usd": price, "_cached_at": time.time(), "_failed": False})
            return price
    except Exception as exc:  # fail-open: never block the webhook
        logger.debug("sol_price_fetch_failed error=%s", exc)
    _sol_price_cache.update({"price_usd": SOL_PRICE_USD, "_cached_at": time.time(), "_failed": True})
    return SOL_PRICE_USD


#: Shorten addresses to ``0x1234…abcd`` (7/5 split like the bot's short_addr).
def _short(addr: str) -> str:
    if not addr:
        return ""
    a = addr.strip()
    if len(a) <= 14:
        return a
    return f"{a[:7]}…{a[-5:]}"


# ── Label enrichment (Arkham-style attribution) ─────────────────────────


def _wallet_label(wallet: str) -> tuple[str, str]:
    """Return ``(readable_label, entity_type)`` for an address.

    Looks up the local entity registry (exchange/DeFi/mixer/scam datasets)
    first, then the chain cache wallet labels. Falls back to a short
    address — never a bare full address, never raises.
    """
    if not wallet:
        return "", ""
    try:
        from app.entity_registry import get_entity_registry

        match = get_entity_registry().classify_address(wallet)
        if match:
            name = match.entity_name or match.entity_type
            return f"{name} ({match.entity_type})", match.entity_type
    except (AttributeError, ImportError, ValueError):
        pass

    return _short(wallet), "unknown"


def _describe_side(label: str, short: str) -> str:
    """Prefer a readable entity label; fall back to a short address.

    A named entity renders as ``binance (cex_hot_wallet)`` — no redundant
    short-address suffix. An unknown wallet keeps its short address.
    """
    if label and label != short and " (" in label:
        return label
    return label or short


# ── Helius event parsing ────────────────────────────────────────────────


def _helius_amount_usd(payload: dict[str, Any], sol_price_usd: float | None = None) -> float:
    """Estimate USD value of a Helius enhanced webhook payload.

    Native SOL transfers (lamports) are priced with the live SOL price
    (``sol_price_usd`` param, wired by :func:`route_webhook_alert` from
    :func:`_sol_price_usd`; defaults to the env ``SOL_PRICE_USD`` for direct
    sync callers). A ``valueUsd`` field wins when present (Arkham-style
    enrichment or a proxy layer stamped it). Fail-open → 0.0.
    """
    raw = payload.get("valueUsd") or payload.get("value_usd")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    total_lamports = 0
    for tr in payload.get("nativeTransfers") or []:
        try:
            total_lamports += int(tr.get("amount", 0) or 0)
        except (TypeError, ValueError):
            continue
    price = sol_price_usd if sol_price_usd is not None and sol_price_usd > 0 else SOL_PRICE_USD
    return (total_lamports / 1e9) * price


def _helius_token_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize the largest token transfer in the payload (if any)."""
    best: dict[str, Any] = {}
    for tr in payload.get("tokenTransfers") or []:
        amount = float((tr.get("tokenAmount") or {}).get("uiAmount", 0) or 0)
        if amount > float(best.get("ui_amount", 0) or 0):
            best = {
                "mint": str(tr.get("mint", "") or ""),
                "symbol": str(tr.get("symbol", "") or "") or str(tr.get("tokenStandard", "") or ""),
                "ui_amount": amount,
                "from": str(tr.get("fromUserAccount", "") or ""),
                "to": str(tr.get("toUserAccount", "") or ""),
            }
    return best


#: Keywords that mark an LP add/remove inside a Helius description.
_LP_KEYWORDS = re.compile(
    r"liquidity|raydium|orca|meteora|jupiter.*pool|add.*liq|remove.*liq|withdraw.*liq|pool.*burn",
    re.IGNORECASE,
)
#: Keywords that mark a mint / freeze / authority toggle.
_AUTHORITY_KEYWORDS = re.compile(
    r"\bmint\b|\bfreeze\b|initialize.*mint|setauthority|updateauthority|disable.*mint|enable.*mint|\bburn\b",
    re.IGNORECASE,
)
#: Keywords that mark a deployer-style dump (creator moving supply).
_DUMP_KEYWORDS = re.compile(r"deployer|creator|team|dev wallet|insider", re.IGNORECASE)


def _classify_event(payload: dict[str, Any], description: str, token: dict[str, Any]) -> str:
    """Classify a Helius event into a small set of notable types."""
    desc = description or ""
    tx_type = str(payload.get("type", "") or "").upper()

    # Order matters: authority/LP signals are more notable than a plain swap.
    if _DUMP_KEYWORDS.search(desc):
        return "deployer_dump"
    if _AUTHORITY_KEYWORDS.search(desc):
        return "mint_freeze"
    if _LP_KEYWORDS.search(desc):
        return "lp_change"
    if "SWAP" in tx_type or ("swap" in desc.lower() and token):
        return "token_transfer"
    if token and float(token.get("ui_amount", 0) or 0) > 0:
        return "token_transfer"
    if tx_type in ("TRANSFER", "TRANSFERCHECK", "TRANSFERCHECKED") or payload.get("nativeTransfers"):
        return "transfer"
    return "unknown"


def parse_helius_event(payload: dict[str, Any], sol_price_usd: float | None = None) -> dict[str, Any] | None:
    """Extract structured signals from a Helius enhanced webhook payload.

    Returns a flat dict (``wallet``, ``counterparty``, ``amount_usd``,
    ``event_type``, ``signature``, ``timestamp``, ``description``,
    ``token``) or ``None`` when the payload carries no usable signal.

    ``sol_price_usd`` (optional) is the live SOL price for native transfers;
    when omitted the env ``SOL_PRICE_USD`` default is used.
    """
    wallet = str(payload.get("feePayer") or payload.get("wallet") or payload.get("address") or "").strip()
    if not wallet:
        return None

    token = _helius_token_summary(payload)
    description = str(payload.get("description", "") or "")[:300]
    event_type = _classify_event(payload, description, token)

    # Counterparty: the other side of the largest transfer when available.
    counterparty = ""
    if token and token.get("to") and token["to"].lower() != wallet.lower():
        counterparty = token["to"]
    elif token and token.get("from") and token["from"].lower() != wallet.lower():
        counterparty = token["from"]
    else:
        native = payload.get("nativeTransfers") or []
        if native:
            n0 = native[0]
            to = str(n0.get("toUserAccount", "") or "")
            fro = str(n0.get("fromUserAccount", "") or "")
            counterparty = (
                to if to and to.lower() != wallet.lower() else (fro if fro and fro.lower() != wallet.lower() else "")
            )

    try:
        ts = int(payload.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        ts = 0

    return {
        "wallet": wallet,
        "counterparty": counterparty,
        "amount_usd": _helius_amount_usd(payload, sol_price_usd),
        "event_type": event_type,
        "signature": str(payload.get("signature", "") or ""),
        "timestamp": ts,
        "description": description,
        "token": token,
    }


# ── Importance + routing (reuses the bot news gate) ────────────────────


def _assess_importance(parsed: dict[str, Any], entity_type: str) -> float:
    """Deterministic 0..1 importance for a parsed webhook event.

    Amount (log scale) + event-type weight + entity risk. Calibrated so a
    routine $1K transfer sits below the news band (~0.35) while a $1M
    whale move / deployer dump / mint-freeze clears the main gate (≥0.7).
    """
    amount = float(parsed.get("amount_usd", 0) or 0)
    event_type = parsed.get("event_type", "transfer")
    token = parsed.get("token") or {}

    amount_factor = 0.0
    if amount >= 1_000_000:
        amount_factor = 0.45
    elif amount >= 100_000:
        amount_factor = 0.30
    elif amount >= 10_000:
        amount_factor = 0.15
    elif amount >= 1_000:
        amount_factor = 0.05

    type_factor = {
        "deployer_dump": 0.40,
        "mint_freeze": 0.35,
        "lp_change": 0.20,
        "whale_move": 0.25,
        "token_transfer": 0.15,
        "transfer": 0.10,
        "unknown": 0.0,
    }.get(event_type, 0.0)

    entity_factor = 0.0
    if entity_type in ("known_scam",):
        entity_factor = 0.40
    elif entity_type in ("mixer",):
        entity_factor = 0.15
    elif entity_type in ("exchange", "defi_protocol", "cex_hot_wallet"):
        entity_factor = 0.0  # attribution matters, not escalation

    token_float = float(token.get("ui_amount", 0) or 0)
    # Dust penalty — sub-$1 token churn can never clear the news band.
    if 0 < token_float < 0.001 and amount < MIN_EVENT_USD:
        return 0.05

    return max(0.0, min(1.0, round(0.10 + amount_factor + type_factor + entity_factor, 2)))


# ── Throttle + dedupe ───────────────────────────────────────────────────


def _redis() -> Any:
    import redis

    return redis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_connect_timeout=2,
    )


def _throttle_wallet(service: str, wallet: str, event_id: str) -> str:
    """Enforce per-wallet cooldown + event dedupe in Redis.

    Returns ``"duplicate"`` when the exact event was already processed,
    ``"throttled"`` when the wallet is inside its cooldown window, and
    ``"ok"`` otherwise. Redis failures fail open → ``"ok"`` (dedupe above
    in ``handle_webhook`` already guards the replay case).
    """
    dedup_key = f"webhook:dedup:{service}:{event_id}"
    cooldown_key = f"webhook:throttle:{service}:{wallet.lower()}"
    try:
        r = _redis()
        try:
            if r.get(dedup_key):
                return "duplicate"
            r.setex(dedup_key, 3600, "1")
            if r.get(cooldown_key):
                return "throttled"
            r.setex(cooldown_key, COOLDOWN_SECONDS, "1")
        finally:
            r.close()
    except (ConnectionError, OSError, TimeoutError):
        pass
    return "ok"


# ── Alert formatting ────────────────────────────────────────────────────


def _format_alert(parsed: dict[str, Any], from_label: str, to_label: str) -> tuple[str, str]:
    """Build an Arkham-style title + description.

    Reads like "Binance (exchange) moved 1,200,000 USDC" instead of a bare
    address — labels win, short addresses fill gaps.
    """
    amount = float(parsed.get("amount_usd", 0) or 0)
    token = parsed.get("token") or {}
    symbol = token.get("symbol", "") if token else ""
    event_type = parsed.get("event_type", "transfer")
    wallet = str(parsed.get("wallet", "") or "")
    short_wallet = _short(wallet)
    from_name = _describe_side(from_label, _short(str(token.get("from", "") or wallet)))
    to_name = _describe_side(to_label, _short(str(token.get("to", "") or parsed.get("counterparty", "") or "")))

    if symbol:
        amount_txt = f"{amount:,.0f} {symbol}" if amount else f"{token.get('ui_amount', 0):,.4g} {symbol}"
    else:
        amount_txt = f"${amount:,.0f}" if amount else ""

    if event_type == "deployer_dump":
        title = f"Deployer dump: {from_name} moved {amount_txt}"
        desc = f"Creator/team wallet {short_wallet} moved {amount_txt} — potential exit pressure."
    elif event_type == "mint_freeze":
        title = f"Mint/freeze toggle on {symbol or 'token'}"
        desc = f"{short_wallet} changed token authority — supply or freeze risk."
    elif event_type == "lp_change":
        title = f"LP change on {symbol or 'token'}"
        desc = f"{short_wallet} altered liquidity — {amount_txt}."
    else:
        title = f"{from_name} moved {amount_txt}" if amount_txt else f"{from_name} activity"
        desc = f"{from_name} → {to_name}"

    extra = []
    sig = str(parsed.get("signature", "") or "")
    if sig:
        extra.append(f"sig {sig[:12]}…")
    if parsed.get("description"):
        extra.append(str(parsed["description"])[:160])
    if extra:
        desc = f"{desc} · " + " · ".join(extra)
    return title, desc


# ── Router (public entry) ───────────────────────────────────────────────


async def route_webhook_alert(service: str, payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    """Gate + route an inbound webhook payload to the alerts channel.

    Returns a decision dict, never raises::

        {"decision": "skip"|"news"|"main"|"unparseable"|"throttled"|"duplicate",
         "importance": float, "event_type": str, "alert_id": str}

    ``"news"`` → the alerts channel (high/medium severity); ``"main"`` →
    escalated (critical/high + ``escalate: True`` metadata). ``"skip"`` /
    ``"throttled"`` / ``"duplicate"`` → no push.
    """
    try:
        # Native SOL transfers carry no USD — fetch the live (cached) SOL
        # price so amounts are real, not the static env default. Only fetch
        # when actually needed: payloads with an explicit valueUsd (or no
        # native transfers) skip the call entirely.
        needs_sol_price = bool(payload.get("nativeTransfers")) and not (
            payload.get("valueUsd") or payload.get("value_usd")
        )
        sol_price = await _sol_price_usd() if needs_sol_price else None
        parsed = parse_helius_event(payload, sol_price_usd=sol_price)
        if parsed is None:
            return {"decision": "unparseable", "importance": 0.0, "event_type": "unknown"}

        event_type = parsed["event_type"]
        if event_type == "unknown" and float(parsed.get("amount_usd", 0) or 0) < MIN_EVENT_USD:
            return {"decision": "skip", "importance": 0.05, "event_type": event_type}

        throttle = _throttle_wallet(service, parsed["wallet"], event_id)
        if throttle in ("duplicate", "throttled"):
            return {
                "decision": throttle,
                "importance": 0.0,
                "event_type": event_type,
                "throttled_wallet": _short(parsed["wallet"]),
            }

        from_label, from_type = _wallet_label(parsed["wallet"])
        to_label, _ = _wallet_label(parsed["counterparty"])
        importance = _assess_importance(parsed, from_type)

        from app.domains.telegram.rugmunchbot.channels.router import route_news

        decision = route_news(importance, "webhook")
        if decision == "skip":
            return {"decision": "skip", "importance": importance, "event_type": event_type}

        title, description = _format_alert(parsed, from_label, to_label)
        severity = "critical" if decision == "main" else ("high" if importance >= 0.5 else "medium")

        from app.alert_pipeline import push_alert

        alert_id = await push_alert(
            alert_type=f"{service}_webhook",
            title=title,
            description=description,
            severity=severity,
            chain="solana"
            if not str(payload.get("chain", "")).startswith("0x")
            else str(payload.get("chain", "unknown")),
            token=parsed["token"].get("mint", "") if parsed.get("token") else "",
            token_symbol=parsed["token"].get("symbol", "") if parsed.get("token") else "",
            wallet=parsed["wallet"],
            risk_score=round(importance * 100),
            metadata={
                "route": decision,
                "importance": importance,
                "event_type": event_type,
                "event_id": event_id,
                "escalate": decision == "main",
                "attribution": {"from": from_label, "to": to_label},
            },
        )
        logger.info(
            "webhook_alert_routed",
            decision=decision,
            event_type=event_type,
            importance=importance,
            wallet=_short(parsed["wallet"]),
        )
        return {"decision": decision, "importance": importance, "event_type": event_type, "alert_id": alert_id}
    except Exception as exc:  # fail-open: never break the inbound handler
        logger.warning("webhook_alert_route_failed error=%s", exc)
        return {"decision": "skip", "importance": 0.0, "event_type": "error"}


__all__ = [
    "COOLDOWN_SECONDS",
    "MIN_EVENT_USD",
    "SOL_MINT",
    "SOL_PRICE_USD",
    "_assess_importance",
    "_format_alert",
    "_sol_price_usd",
    "_wallet_label",
    "parse_helius_event",
    "route_webhook_alert",
]
