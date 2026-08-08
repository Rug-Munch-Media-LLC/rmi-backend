"""Holder/LP parsing helpers — extracted from ``modules/holders.py``.

Degodding wave 6 (2026-08-09): ``_get_holder_data`` (C-20) and
``_check_liquidity_lock_multi`` (D-23) in ``holders.py`` each contained a
nested RPC/API parsing block. The cohesive units extracted here are:

- ``_build_solana_top_holders`` — pure parser for the
  ``getTokenLargestAccounts`` RPC ``value`` list → ranked top-holder dicts.
- ``_build_blockscout_top_holders`` — pure parser for Blockscout
  ``/holders`` ``items`` → ranked top-holder dicts.
- ``_solana_lp_burn_ratio`` — async Solana LP burn check (getTokenSupply +
  getTokenAccountsByOwner against the dead address) → ``(locked_pct, burned)``.

``holders.py`` keeps the public ``_get_holder_data`` /
``_check_liquidity_lock_multi`` / ``_check_lp_concentration`` names (tests
patch them at module level).
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.modules._core import _solana_rpc_call


def _build_solana_top_holders(value: list[Any]) -> list[dict[str, Any]]:
    """Rank ``getTokenLargestAccounts`` RPC accounts into top-holder dicts.

    Each account's raw ``amount`` is normalized to a share of the total
    (percentage, 2dp) and tagged with a 1-based rank. Mirrors the legacy
    in-place loop from ``_get_holder_data``.
    """
    total = sum(int(a.get("amount", "0")) for a in value) or 1
    top_holders: list[dict[str, Any]] = []
    for i, acct in enumerate(value[:20]):
        pct = (int(acct.get("amount", "0")) / total) * 100
        top_holders.append(
            {
                "rank": i + 1,
                "address": acct.get("address", ""),
                "amount": acct.get("amount", "0"),
                "percentage": round(pct, 2),
            }
        )
    return top_holders


def _build_blockscout_top_holders(items: list[Any]) -> list[dict[str, Any]]:
    """Rank Blockscout ``/holders`` items into top-holder dicts.

    Each item carries ``percentage`` directly; the address is nested under
    ``address.hash``. Mirrors the legacy in-place loop from
    ``_get_holder_data``.
    """
    top_holders: list[dict[str, Any]] = []
    for i, h in enumerate(items):
        pct = float(h.get("percentage", 0))
        top_holders.append(
            {
                "rank": i + 1,
                "address": h.get("address", {}).get("hash", ""),
                "percentage": pct,
            }
        )
    return top_holders


async def _solana_lp_burn_ratio(pair_addr: str) -> tuple[float, bool] | None:
    """Check how much of an LP pair is held by the dead address on Solana.

    Fetches the pair's total supply (``getTokenSupply``) and the dead
    address's balance (``getTokenAccountsByOwner`` with the classic dead
    wallet), returning ``(locked_pct, burned)``. Returns ``None`` when the
    RPC data is unavailable so callers can fall back silently.
    """
    supply_data = await _solana_rpc_call("getTokenSupply", [pair_addr])
    if not supply_data:
        return None
    supply = int(supply_data.get("result", {}).get("value", {}).get("amount", "0"))
    dead_data = await _solana_rpc_call(
        "getTokenAccountsByOwner",
        [
            "dead111111111111111111111111111111111111111",
            {"mint": pair_addr},
            {"encoding": "jsonParsed"},
        ],
    )
    if not dead_data:
        return None
    dead_balance = 0
    for acct in dead_data.get("result", {}).get("value", []):
        dead_balance += int(
            acct.get("account", {})
            .get("data", {})
            .get("parsed", {})
            .get("info", {})
            .get("tokenAmount", {})
            .get("amount", "0")
        )
    if supply > 0:
        return round((dead_balance / supply) * 100, 1), dead_balance > 0
    return None
