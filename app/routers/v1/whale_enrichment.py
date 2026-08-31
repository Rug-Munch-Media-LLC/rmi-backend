"""Whale-feed enrichment — labels, context, smart-money, USD accuracy.

Turns the raw ``dex_events`` whale feed ("wallet X swapped Y for Z") into
alpha-grade intel. Every helper is fail-open and batched: the whole
enrichment pass costs at most 3 ClickHouse queries regardless of feed size.

Layers
------
1. **Wallet labels** — batch lookup in ``wallet_memory.wallet_labels``
   (Etherscan/GoPlus/ScamSniffer/OFAC-style sources already ingested by the
   lite-indexer). Classified into a coarse ``label_type``:
   ``exchange | flagged | smart_money | whale | labeled | unknown``.
2. **Context** — the wallet's recent swap history from ``dex_events``:
   trade count, first/last seen, average trade size, net stablecoin flow
   (accumulating vs distributing), and whether THIS trade is unusual
   (>= 3x the wallet's average size).
3. **Smart money** — a deterministic round-trip heuristic computed purely
   from ``dex_events``: a wallet that bought a token with stablecoins and
   later sold it back for more (FIFO matching per token). ``is_smart_money``
   requires >= 3 completed round trips with >= 60% win rate.
4. **USD accuracy** — ``dex_events`` amounts are RAW on-chain units (the
   lite-indexer write path stores raw log amounts; its own pricing module
   divides by ``10 ** decimals``). USD = stable-side amount / 10^decimals,
   decimals resolved from ``token_meta_ref`` with a known-stablecoin
   fallback map.

Direction semantics (heuristic, documented): for a pair (stable, token),
``amount0_in > 0`` means stablecoins flowed INTO the pair (a BUY of the
token); for a pair (token, stable), ``amount1_in > 0`` is likewise a BUY.
Stablecoins flowing OUT (``*_out`` legs) are SELLs. This is pair-level v2
semantics used as a wallet-behaviour proxy — good enough for a feed-level
accumulating/distributing signal, not an audit-grade attribution.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger

logger = get_logger("whale-enrichment")

# Known stablecoin decimals (mirror of lite-chain-indexer pricing.KNOWN_DECIMALS
# for the addresses in market._STABLECOINS). Fallback when token_meta_ref has
# no decimals for the stable side.
STABLECOIN_DECIMALS: dict[str, int] = {
    # USDC
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,  # ethereum
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,  # base
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": 6,  # arbitrum
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": 6,  # polygon
    "0x0b2c639c533813f4aa9d7837caf62653d097ff85": 6,  # optimism
    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": 6,  # avalanche
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # bsc
    # USDT
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,  # ethereum
    "0x55d398326f99059ff775485246999027b3197955": 18,  # bsc
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": 6,  # polygon
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": 6,  # arbitrum
    "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": 6,  # optimism
    "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": 6,  # avalanche
    # DAI
    "0x6b175474e89094c44da98b954eedeac495271d0f": 18,  # ethereum
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": 18,  # polygon
    # BUSD
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # bsc
}

_DEFAULT_STABLE_DECIMALS = 6

# CEX name fragments for label classification (lowercase substrings).
_CEX_FRAGMENTS = (
    "binance",
    "coinbase",
    "okx",
    "kraken",
    "bybit",
    "upbit",
    "bithumb",
    "bitfinex",
    "gemini",
    "kucoin",
    "huobi",
    "htx",
    "gate.io",
    "bitget",
    "mexc",
    "bitstamp",
    "crypto.com",
    "bithd",
    "robinhood",
)
_FLAGGED_CATEGORIES = ("phish", "scam", "sanction", "hack", "exploit", "heist")
_SMART_CATEGORIES = ("smart", "sniper", "insider", "fund", "trader")

_HISTORY_WINDOW_DAYS = 30
_HISTORY_ROW_CAP = 2000
_UNUSUAL_MULTIPLIER = 3.0
_SMART_MIN_TRIPS = 3
_SMART_MIN_WIN_RATE = 0.6


def stable_decimals(token: str, meta_decimals: Any = None) -> int:
    """Decimals for a stablecoin address: token_meta_ref value, else known map.

    ``meta_decimals`` is the raw value from ``token_meta_ref.decimals``
    (Nullable UInt8 — may be ``None``). Pure.
    """
    try:
        if meta_decimals is not None and int(meta_decimals) > 0:
            return int(meta_decimals)
    except (TypeError, ValueError):
        pass
    return STABLECOIN_DECIMALS.get((token or "").lower(), _DEFAULT_STABLE_DECIMALS_FALLBACK)


# Alias kept explicit (module-level so tests can monkeypatch).
_DEFAULT_STABLE_DECIMALS_FALLBACK = _DEFAULT_STABLE_DECIMALS


def stable_usd(amount: Any, token: str, meta_decimals: Any = None) -> float:
    """Raw stablecoin amount → human USD (divide by 10^decimals). Pure."""
    try:
        raw = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    if raw <= 0 or not math.isfinite(raw):
        return 0.0
    return raw / (10 ** stable_decimals(token, meta_decimals))


def classify_labels(labels: list[dict[str, Any]]) -> str:
    """Coarse wallet class from raw label rows. Pure.

    Priority: flagged > exchange > smart_money > whale > labeled > unknown.
    """
    if not labels:
        return "unknown"
    texts = " ".join(
        f"{lab.get('name', '')} {lab.get('category', '')} {lab.get('subtype', '')}".lower() for lab in labels
    )
    for cat in _FLAGGED_CATEGORIES:
        if cat in texts:
            return "flagged"
    if any(frag in texts for frag in _CEX_FRAGMENTS) or "exchange" in texts or "cex" in texts:
        return "exchange"
    if any(frag in texts for frag in _SMART_CATEGORIES):
        return "smart_money"
    if "whale" in texts:
        return "whale"
    return "labeled"


_SMART_CATEGORIES = _SMART_CATEGORIES  # re-export alias (kept for readability)


def label_wallets(client: Any, addresses: list[str]) -> dict[str, dict[str, Any]]:
    """Batch wallet-label lookup (one query). Fail-open to ``{}``.

    Returns ``{address: {"labels": [...], "label_type": str, "is_sanctioned": bool}}``.
    """
    out: dict[str, dict[str, Any]] = {}
    addrs = sorted({(a or "").lower() for a in addresses if a})
    if not addrs or client is None:
        return out
    try:
        rows = client.execute(
            """
            SELECT address, label_name, label_category, label_subtype, source, is_sanctioned
            FROM wallet_memory.wallet_labels
            WHERE address IN %(addrs)s
            ORDER BY loaded_at DESC
            LIMIT 500
            """,
            {"addrs": tuple(addrs)},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("whale labels query failed: %s", exc)
        return out
    for addr, name, category, subtype, source, sanctioned in rows:
        entry = out.setdefault(
            (addr or "").lower(),
            {"labels": [], "label_type": "unknown", "is_sanctioned": False},
        )
        if name or category:
            entry["labels"].append(
                {
                    "name": name or "",
                    "category": category or "",
                    "subtype": subtype or "",
                    "source": source or "",
                }
            )
        entry["is_sanctioned"] = bool(entry["is_sanctioned"] or sanctioned)
    for entry in out.values():
        entry["label_type"] = classify_labels(entry["labels"])
    return out


def wallet_histories(
    client: Any, senders: list[str], *, window_days: int = _HISTORY_WINDOW_DAYS
) -> dict[str, dict[str, Any]]:
    """Aggregate recent swap history per sender (one query). Fail-open.

    Returns ``{sender: {"swaps": [...], "context": {...}, "smart_money": {...}}}``.
    """
    out: dict[str, dict[str, Any]] = {}
    addrs = sorted({(s or "").lower() for s in senders if s})
    if not addrs or client is None:
        return out
    since = datetime.now(tz=UTC) - timedelta(days=window_days)
    try:
        rows = client.execute(
            """
            SELECT sender, chain, block_timestamp, token0, token1,
                   toFloat64OrZero(amount0), toFloat64OrZero(amount1),
                   toFloat64OrZero(amount0_out), toFloat64OrZero(amount1_out)
            FROM wallet_memory.dex_events
            WHERE sender IN %(senders)s
              AND block_timestamp >= %(since)s
            ORDER BY block_timestamp ASC
            LIMIT 2000
            """,
            {"senders": tuple(addrs), "since": since},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("whale history query failed: %s", exc)
        return out

    for sender, chain, ts, token0, token1, a0_in, a1_in, a0_out, a1_out in rows:
        entry = out.setdefault((sender or "").lower(), {"swaps": []})
        entry["swaps"].append(
            {
                "chain": chain,
                "ts": ts,
                "token0": (token0 or "").lower(),
                "token1": (token1 or "").lower(),
                "a0_in": float(a0_in or 0),
                "a1_in": float(a1_in or 0),
                "a0_out": float(a0_out or 0),
                "a1_out": float(a1_out or 0),
            }
        )
    for entry in out.values():
        entry["context"] = _build_context(entry["swaps"])
        entry["smart_money"] = _build_smart_money(entry["swaps"])
    return out


def _build_context(swaps: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-wallet behaviour summary from its recent swaps. Pure."""
    if not swaps:
        return {}
    stables = set(STABLECOIN_DECIMALS)
    sizes: list[float] = []
    net_stable = 0.0  # >0 = net stablecoin received (distributing)
    first_seen = swaps[0].get("ts")
    last_seen = swaps[-1].get("ts")
    for s in swaps:
        t0, t1 = s["token0"], s["token1"]
        if t0 in stables:
            usd = max(s["a0_in"], s["a0_out"]) / (10 ** STABLECOIN_DECIMALS[t0])
            # pair (stable, token): stable IN = bought token, stable OUT = sold
            net_stable += s["a0_out"] - s["a0_in"]
        elif t1 in stables:
            usd = max(s["a1_in"], s["a1_out"]) / (10 ** STABLECOIN_DECIMALS[t1])
            # pair (token, stable): stable IN = bought token, stable OUT = sold
            net_stable += s["a1_out"] - s["a1_in"]
        else:
            continue
        if usd > 0:
            sizes.append(usd)
    avg = sum(sizes) / len(sizes) if sizes else 0.0
    if net_stable > avg > 0:
        direction = "distributing"
    elif net_stable < -avg > 0:
        direction = "accumulating"
    else:
        direction = "mixed"
    return {
        "swap_count_30d": len(swaps),
        "avg_trade_usd": round(avg, 2),
        "net_stable_usd_30d": round(net_stable, 2),
        "direction": direction,
        "first_seen": _iso_or_empty(first_seen),
        "last_seen": _iso_or_empty(last_seen),
    }


def _iso_or_empty(ts: Any) -> str:
    """ISO string for a datetime-ish value ("" when absent/unparseable). Pure."""
    if not ts:
        return ""
    try:
        return ts.isoformat()
    except AttributeError:
        return str(ts)


def _close_trip(open_buys: dict[str, list[float]], token: str, proceeds: float) -> tuple[int, int, float]:
    """FIFO-close one round trip. Returns (trips, wins, pnl). Pure."""
    buys = open_buys.get(token)
    if not buys:
        return 0, 0, 0.0
    cost = buys.pop(0)
    pnl = proceeds - cost
    return 1, (1 if pnl > 0 else 0), pnl


def _build_smart_money(swaps: list[dict[str, Any]]) -> dict[str, Any]:
    """FIFO round-trip heuristic from raw swap history. Pure.

    A round trip = bought a token with stablecoins, later sold (some of) it
    back for stablecoins. Win = sell proceeds > buy cost.
    """
    stables = set(STABLECOIN_DECIMALS)
    open_buys: dict[str, list[float]] = {}
    trips = 0
    wins = 0
    realized_pnl = 0.0
    for s in swaps:
        t0, t1 = s["token0"], s["token1"]
        if t0 in stables:
            # pair (stable, token): stable IN = buy of t1, token IN = sell of t1
            if s["a0_in"] > 0:
                open_buys.setdefault(t1, []).append(s["a0_in"] / (10 ** STABLECOIN_DECIMALS[t0]))
            if s["a1_in"] > 0:
                trips_wins_pnl = _close_trip(open_buys, t1, s["a1_in"] / (10 ** STABLECOIN_DECIMALS[t0]))
                trips += trips_wins_pnl[0]
                wins += trips_wins_pnl[1]
                realized_pnl += trips_wins_pnl[2]
        elif t1 in stables:
            # pair (token, stable): stable IN = buy of t0, token IN = sell of t0
            if s["a1_in"] > 0:
                open_buys.setdefault(t0, []).append(s["a1_in"] / (10 ** STABLECOIN_DECIMALS[t1]))
            if s["a0_in"] > 0:
                trips_wins_pnl = _close_trip(open_buys, t0, s["a0_in"] / (10 ** STABLECOIN_DECIMALS[t1]))
                trips += trips_wins_pnl[0]
                wins += trips_wins_pnl[1]
                realized_pnl += trips_wins_pnl[2]
    win_rate = wins / trips if trips else 0.0
    return {
        "round_trips": trips,
        "wins": wins,
        "win_rate": round(win_rate, 3),
        "realized_pnl_usd": round(realized_pnl, 2),
        "is_smart_money": trips >= _SMART_MIN_TRIPS and win_rate >= _SMART_MIN_WIN_RATE,
    }


def build_alpha_signal(
    item: dict[str, Any],
    swaps: list[dict[str, Any]],
    context: dict[str, Any],
    smart: dict[str, Any],
) -> dict[str, Any] | None:
    """Structured alpha signal for one whale feed item (None = not notable).

    Signal types (the paid-product taxonomy):
      - ``large_swap``      — trade is >= _UNUSUAL_MULTIPLIER x the wallet's
        30d average size.
      - ``new_token_entry`` — the token being bought does not appear anywhere
        in the wallet's 30d swap history (first entry into a new position).
      - ``exit_after_pump`` — the wallet is selling (stablecoin side received)
        and this sale closes a profitable FIFO round trip on that token.

    Pure — never raises. Returns ``None`` when nothing notable happened:
    most whale swaps are routine and must NOT be flagged as alpha.
    """
    usd = float(item.get("usd_value") or 0)
    if usd <= 0:
        return None
    avg = float(context.get("avg_trade_usd") or 0)
    sender = str(item.get("sender") or "").lower()
    token0 = str(item.get("token0") or "").lower()
    token1 = str(item.get("token1") or "").lower()
    stables = set(STABLECOIN_DECIMALS)

    # Which asset side is the stablecoin leg, and which token is the asset?
    asset_addr, asset_sym = "", ""
    if token0 in stables:
        asset_addr, asset_sym = token1, str(item.get("symbol1") or "")
    elif token1 in stables:
        asset_addr, asset_sym = token0, str(item.get("symbol0") or "")
    else:
        asset_addr, asset_sym = token0, str(item.get("symbol0") or "")

    # Replay the wallet's recent swaps to learn its token universe.
    seen_tokens: set[str] = set()
    for s in swaps:
        t0, t1 = s["token0"], s["token1"]
        if t0 in stables:
            seen_tokens.add(t1)
        elif t1 in stables:
            seen_tokens.add(t0)
        else:
            seen_tokens.update((t0, t1))

    # Direction of THIS trade (sell = wallet received stables).
    sell = is_sell_direction(swaps, item)
    pnl = float(smart.get("realized_pnl_usd") or 0)

    signal: dict[str, Any] | None = None

    # 1) Large swap — size anomaly vs the wallet's own baseline.
    if avg > 0 and usd >= _UNUSUAL_MULTIPLIER * avg:
        signal = {
            "type": "large_swap",
            "wallet": sender,
            "usd_value": usd,
            "why_notable": (f"${usd:,.0f} swap is {usd / avg:.1f}x this wallet's 30d average trade size (${avg:,.0f})"),
            "track_record": {
                "round_trips": smart.get("round_trips", 0),
                "win_rate": smart.get("win_rate", 0),
                "realized_pnl_usd": smart.get("realized_pnl_usd", 0),
                "is_smart_money": bool(smart.get("is_smart_money")),
            },
        }

    # 2) New token entry — asset token absent from the wallet's 30d history.
    if asset_addr and swaps and asset_addr not in seen_tokens and not sell and signal is None:
        signal = {
            "type": "new_token_entry",
            "wallet": sender,
            "usd_value": usd,
            "token": asset_sym or _short_addr(asset_addr),
            "why_notable": (
                f"First position in {asset_sym or _short_addr(asset_addr)} in 30d — "
                f"new entry by a wallet with {smart.get('round_trips', 0)} tracked round trips "
                f"({win_rate_pct(smart)} win)"
            ),
            "track_record": {
                "round_trips": smart.get("round_trips", 0),
                "win_rate": smart.get("win_rate", 0),
                "realized_pnl_usd": smart.get("realized_pnl_usd", 0),
                "is_smart_money": bool(smart.get("is_smart_money")),
            },
        }

    # 3) Exit after pump — selling into strength with a profitable track record.
    if sell and pnl > 0 and smart.get("round_trips", 0) >= 1 and signal is None:
        signal = {
            "type": "exit_after_pump",
            "wallet": sender,
            "usd_value": usd,
            "token": asset_sym or _short_addr(asset_addr),
            "why_notable": (
                f"Selling {asset_sym or 'position'} after gains — wallet has banked "
                f"${pnl:,.0f} realized PnL at {win_rate_pct(smart)} win rate"
            ),
            "track_record": {
                "round_trips": smart.get("round_trips", 0),
                "win_rate": smart.get("win_rate", 0),
                "realized_pnl_usd": smart.get("realized_pnl_usd", 0),
                "is_smart_money": bool(smart.get("is_smart_money")),
            },
        }

    if signal:
        signal.setdefault("type", "large_swap")
        signal["wallet_context"] = str(context.get("direction") or "")
    return signal


def win_rate_pct(smart: dict[str, Any]) -> str:
    """Human win-rate string from a smart-money dict ('n/a' when unknown)."""
    wr = smart.get("win_rate")
    try:
        return f"{float(wr):.0%}"
    except (TypeError, ValueError):
        return "n/a"


def _short_addr(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 12 else addr


def is_sell_direction(swaps: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    """Best-effort buy/sell classification for THIS feed item.

    A sell = the wallet received stablecoins. We match the item's pair
    against the wallet's most recent swap on the same pair and check which
    side flowed in. Defaults to False (buy) when history is missing. Pure.
    """
    pair = str(item.get("pair_address") or "").lower()
    token0 = str(item.get("token0") or "").lower()
    token1 = str(item.get("token1") or "").lower()
    stables = set(STABLECOIN_DECIMALS)
    for s in reversed(swaps):
        s_pair = str(s.get("pair") or "").lower()
        if pair and s_pair and s_pair != pair:
            continue
        if {s["token0"], s["token1"]} != {token0, token1}:
            continue
        if token0 in stables:
            return s["a0_in"] > 0  # stable IN = wallet sold the asset
        if token1 in stables:
            return s["a1_in"] > 0
        return False
    return False


def enrich_feed_items(
    client: Any,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach labels + context + smart-money to whale feed items (fail-open).

    Mutates and returns ``items``. Never raises: any enrichment failure
    leaves the item un-enriched rather than breaking the feed.
    """
    if not items:
        return items
    senders = [str(i.get("sender") or "") for i in items]
    try:
        labels = label_wallets(client, senders)
    except Exception as exc:
        logger.debug("whale label enrichment failed: %s", exc)
        labels = {}
    try:
        histories = wallet_histories(client, senders)
    except Exception as exc:
        logger.debug("whale history enrichment failed: %s", exc)
        histories = {}

    for item in items:
        sender = str(item.get("sender") or "").lower()
        lab = labels.get(sender)
        if lab:
            item["wallet_label"] = lab["label_type"]
            item["wallet_labels"] = lab["labels"]
            item["is_sanctioned"] = lab["is_sanctioned"]
        else:
            item["wallet_label"] = "unknown"
            item["wallet_labels"] = []
            item["is_sanctioned"] = False

        hist = histories.get(sender) or {}
        context = dict(hist.get("context") or {})
        usd = float(item.get("usd_value") or 0)
        avg = float(context.get("avg_trade_usd") or 0)
        if avg > 0 and usd >= _UNUSUAL_MULTIPLIER * avg:
            context["unusual"] = True
            context["unusual_note"] = f"this trade is {usd / avg:.1f}x this wallet's 30d average size"
        else:
            context["unusual"] = False
        item["context"] = context

        smart = dict(hist.get("smart_money") or {})
        item["smart_money"] = smart
        item["is_smart_money"] = bool(smart.get("is_smart_money"))

        # Richer headline: label + direction + smart-money flag.
        if item["is_smart_money"]:
            wr = float(smart.get("win_rate") or 0)
            label_txt = f"smart-money wallet ({smart.get('round_trips', 0)} trips · {wr:.0%} win)"
        else:
            label_txt = f"{item['wallet_label'].replace('_', ' ')} wallet"
        bits = [label_txt]
        if context.get("direction") in ("accumulating", "distributing"):
            bits.append(str(context["direction"]))
        item["wallet_context_line"] = " · ".join(bits)

        # Alpha signal (paid-product taxonomy) — pure, fail-open.
        try:
            item["alpha_signal"] = build_alpha_signal(item, hist.get("swaps") or [], context, smart)
        except Exception as exc:
            logger.debug("alpha signal build failed: %s", exc)
            item["alpha_signal"] = None
    return items


__all__ = [
    "STABLECOIN_DECIMALS",
    "build_alpha_signal",
    "classify_labels",
    "enrich_feed_items",
    "label_wallets",
    "stable_decimals",
    "stable_usd",
    "wallet_histories",
]
