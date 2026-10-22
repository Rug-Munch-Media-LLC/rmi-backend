"""Turn raw trade legs into per-wallet launch-window positions.

A ``dex_events`` row carries both legs of one swap. The mint leg tells the
direction without needing to know which address is the pool:

* mint on the **out** side  -> the recipient received tokens (a buy),
* mint on the **in** side   -> the sender delivered tokens (a sell).

Attribution uses the trading party, so router passthrough does not invent
wallets that never held the token.
"""

from __future__ import annotations

from typing import Any

from .models import WalletPosition


def _raw(value: Any) -> int:
    """Coerce a ClickHouse amount (string / int / None) to a non-negative int."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return 0


def _leg(row: dict[str, Any], mint: str) -> tuple[str, str, int, int]:
    """Return ``(party, side, tokens_raw, slot)`` for one event row."""
    is_token0 = row.get("token0") == mint
    mint_in = _raw(row.get("amount0_in") if is_token0 else row.get("amount1_in"))
    mint_out = _raw(row.get("amount0_out") if is_token0 else row.get("amount1_out"))
    slot = int(row.get("block_number") or 0)
    if mint_out > 0:
        return str(row.get("recipient") or ""), "buy", mint_out, slot
    if mint_in > 0:
        return str(row.get("sender") or ""), "sell", mint_in, slot
    return "", "", 0, slot


def build_positions(
    rows: list[dict[str, Any]],
    mint: str,
    *,
    creator: str | None = None,
) -> dict[str, WalletPosition]:
    """Accumulate buy/sell legs into one :class:`WalletPosition` per address."""
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        party, side, tokens, slot = _leg(row, mint)
        if not party or tokens <= 0:
            continue
        ts = float(row.get("ts") or 0)
        cur = acc.setdefault(
            party,
            {
                "first_slot": slot,
                "first_ts": ts,
                "buys": 0,
                "sells": 0,
                "bought": 0,
                "sold": 0,
                "usd_spent": 0.0,
                "usd_received": 0.0,
            },
        )
        if slot < cur["first_slot"]:
            cur["first_slot"] = slot
            cur["first_ts"] = ts
        if side == "buy":
            cur["buys"] += 1
            cur["bought"] += tokens
            cur["usd_spent"] += float(row.get("price_usd") or 0) * tokens
        else:
            cur["sells"] += 1
            cur["sold"] += tokens
            cur["usd_received"] += float(row.get("price_usd") or 0) * tokens

    return {
        addr: WalletPosition(
            address=addr,
            first_slot=data["first_slot"],
            first_ts=data["first_ts"],
            buy_count=data["buys"],
            sell_count=data["sells"],
            tokens_bought_raw=data["bought"],
            tokens_sold_raw=data["sold"],
            usd_spent=data["usd_spent"],
            usd_received=data["usd_received"],
            is_creator=bool(creator) and addr == creator,
        )
        for addr, data in acc.items()
    }


def first_minute_volume_usd(
    rows: list[dict[str, Any]], mint: str, window_seconds: int = 60
) -> float:
    """USD notional traded in the first ``window_seconds`` after the first leg."""
    stamps = [float(r.get("ts") or 0) for r in rows if r.get("ts")]
    if not stamps:
        return 0.0
    start = min(stamps)
    total = 0.0
    for row in rows:
        ts = float(row.get("ts") or 0)
        if not start <= ts <= start + window_seconds:
            continue
        _, _, tokens, _ = _leg(row, mint)
        total += float(row.get("price_usd") or 0) * tokens
    return total
