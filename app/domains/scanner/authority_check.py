"""
Authority / ownership checks for RugMunchBot token scans.

Flags mintable, freezable and LP-unlocked tokens by pulling authority
signals from injectable fetchers. Fail-open: any error yields ``None``
signals so the bot never crashes on a metadata outage.

Signal sources (default fetchers are best-effort and fail-open):
  - Solana mint/freeze authority  -> RPC ``getAccountInfo`` jsonParsed
  - EVM mintable / renounced      -> GoPlus ``token_security``
  - LP lock status + %            -> own_lp_lock engine (ClickHouse)

The lead wires real fetchers; tests inject stubs (no network / DB).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

#: Fetcher signature: ``async (address, chain) -> dict``.
AuthorityFetcher = Callable[[str, str], Awaitable[dict[str, Any]]]

#: Renounced / burned / null owner addresses (EVM).
_RENOUNCED_OWNERS = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000000000000000000000000",
}

#: Fail-open default result shape.
EMPTY_RESULT: dict[str, Any] = {
    "mintable": None,
    "freeze_authority": None,
    "lp_locked": None,
    "lp_lock_pct": None,
    "renounced": None,
    "evidence": None,
}


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def check_authority(
    address: str,
    chain: str,
    *,
    fetch_authority: AuthorityFetcher | None = None,
    fetch_lp_lock: AuthorityFetcher | None = None,
    fetch_renounced: AuthorityFetcher | None = None,
) -> dict[str, Any]:
    """Check mint / freeze / LP-lock / renounce authority signals.

    Args:
        address: Token contract / mint address.
        chain: Chain name (solana, ethereum, bsc, base, ...).
        fetch_authority: Inject mint/freeze fetcher. Defaults to a best-effort
            RPC/GoPlus lookup. Payload keys: ``mintable``, ``freeze_authority``,
            optionally ``renounced`` and ``source``.
        fetch_lp_lock: Inject LP lock fetcher. Defaults to own_lp_lock.
            Payload keys: ``lp_locked``, ``lp_lock_pct``, ``source``.
        fetch_renounced: Optional dedicated renounce fetcher. When omitted,
            ``renounced`` is taken from the authority payload.

    Returns:
        ``{mintable, freeze_authority, lp_locked, lp_lock_pct, renounced,
        evidence}`` — every field is ``None`` on failure (fail-open).
    """
    chain = chain.lower()
    fetch_authority = fetch_authority or _fetch_mint_freeze
    fetch_lp_lock = fetch_lp_lock or _fetch_lp_lock

    auth_payload, lp_payload = await asyncio.gather(
        _guard(fetch_authority(address, chain)),
        _guard(fetch_lp_lock(address, chain)),
    )

    if fetch_renounced is not None:
        ren_payload = await _guard(fetch_renounced(address, chain))
    else:
        ren_payload = _derive_renounced(auth_payload)

    return {
        "mintable": _as_bool_or_none(auth_payload.get("mintable")),
        "freeze_authority": _as_bool_or_none(auth_payload.get("freeze_authority")),
        "lp_locked": _as_bool_or_none(lp_payload.get("lp_locked")),
        "lp_lock_pct": _as_float_or_none(lp_payload.get("lp_lock_pct")),
        "renounced": _as_bool_or_none(ren_payload.get("renounced")),
        "evidence": _build_evidence(auth_payload, lp_payload, ren_payload),
    }


def authority_signal(result: dict[str, Any]) -> str | None:
    """Render a human flag for the bot; highest-priority risk first.

    Returns ``"mintable"`` / ``"freezable"`` / ``"LP not locked"`` /
    ``"LP 100% locked"`` (positive) or ``None`` when nothing is worth
    flagging (including all-``None`` / unknown data).
    """
    mintable = _as_bool_or_none(result.get("mintable"))
    freeze = _as_bool_or_none(result.get("freeze_authority"))
    lp_locked = _as_bool_or_none(result.get("lp_locked"))
    lp_pct = _as_float_or_none(result.get("lp_lock_pct"))

    if mintable is True:
        return "mintable"
    if freeze is True:
        return "freezable"
    if lp_locked is False:
        return "LP not locked"
    if lp_pct is not None and lp_pct >= 100:
        return "LP 100% locked"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# DEFAULT FETCHERS (best-effort, fail-open — never raise)
# ═══════════════════════════════════════════════════════════════════════════


async def _fetch_mint_freeze(address: str, chain: str) -> dict[str, Any]:
    """Best-effort mint / freeze / renounce lookup (returns {} on error)."""
    try:
        if chain in ("solana", "svm"):
            return await _solana_mint_freeze(address)
        return await _evm_goplus_flags(address, chain)
    except Exception as exc:
        logger.warning("authority_mint_freeze_failed", token=address, chain=chain, error=str(exc)[:100])
        return {}


async def _fetch_lp_lock(address: str, chain: str) -> dict[str, Any]:
    """Best-effort LP lock lookup via own ClickHouse engine (returns {} on error)."""
    try:
        from app.domains.scanner.engines.own_lp_lock import get_own_lp_lock_status

        lock = await get_own_lp_lock_status(address, chain)
        if not isinstance(lock, dict) or lock.get("source") == "unavailable":
            # No provable data — unknown, NOT "unlocked". Fail-open.
            return {}
        is_locked = lock.get("is_locked")
        pct = lock.get("locked_pct")
        return {
            "lp_locked": _as_bool_or_none(is_locked),
            "lp_lock_pct": _as_float_or_none(pct),
            "source": lock.get("source", "own_lp_lock"),
        }
    except Exception as exc:
        logger.debug("authority_lp_lock_failed", error=str(exc)[:100])
        return {}


async def _solana_mint_freeze(address: str) -> dict[str, Any]:
    """Solana: read mint/freeze authority from RPC jsonParsed account info."""
    from app.domains.scanner.models import SOLANA_RPC_ENDPOINTS

    async with httpx.AsyncClient(timeout=10.0) as client:
        for rpc_url in SOLANA_RPC_ENDPOINTS:
            try:
                resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAccountInfo",
                        "params": [address, {"encoding": "jsonParsed"}],
                    },
                )
                if resp.status_code != 200:
                    continue
                val = (resp.json().get("result") or {}).get("value")
                if not val:
                    continue
                parsed = (val.get("data") or {}).get("parsed", {})
                if parsed.get("type") != "mint":
                    continue
                info = parsed.get("info", {})
                mint_auth = info.get("mintAuthority")
                freeze_auth = info.get("freezeAuthority")
                return {
                    "mintable": bool(mint_auth),
                    "freeze_authority": bool(freeze_auth),
                    "renounced": not bool(mint_auth),
                    "source": "solana_rpc",
                }
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
                logger.debug("solana_rpc_mint_freeze_failed", url=rpc_url, error=str(exc)[:100])
    return {}


async def _evm_goplus_flags(address: str, chain: str) -> dict[str, Any]:
    """EVM: GoPlus token_security flags (mintable + renounced)."""
    from app.domains.scanner.modules._core import GOPLUS_CHAIN_IDS

    chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": address},
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        token_data = (data.get("result") or {}).get(address.lower()) or {}
        if not isinstance(token_data, dict) or not token_data:
            return {}
        return {
            "mintable": token_data.get("is_mintable") == "1",
            # GoPlus has no freeze flag — pausable is a different signal, so
            # leave freeze authority unknown rather than guessing.
            "freeze_authority": None,
            "renounced": _is_renounced_evm(token_data),
            "source": "goplus",
        }


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _guard(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Await a fetcher coroutine, failing open to {} on any error."""
    try:
        payload = await coro
        if not isinstance(payload, dict):
            return {}
        return payload
    except Exception as exc:
        logger.debug("authority_fetcher_failed", error=str(exc)[:100])
        return {}


def _derive_renounced(auth_payload: dict[str, Any]) -> dict[str, Any]:
    """Pull ``renounced`` from the authority payload when present."""
    if "renounced" in auth_payload:
        return {"renounced": auth_payload.get("renounced"), "source": auth_payload.get("source", "authority")}
    return {}


def _is_renounced_evm(token_data: dict[str, Any]) -> bool | None:
    """EVM renounce status from GoPlus fields (None = unknown).

    Only claims ``True`` when provable (owner == null/dead). An active
    owner (or ``can_take_back_ownership``) yields ``False``. Missing owner
    data yields ``None`` — do not guess.
    """
    if token_data.get("can_take_back_ownership") == "1":
        return False
    owner = (token_data.get("owner_address") or "").strip().lower()
    if not owner:
        return None
    return owner in _RENOUNCED_OWNERS


def _as_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        return bool(value)
    except (TypeError, ValueError):
        return None


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_evidence(
    auth_payload: dict[str, Any],
    lp_payload: dict[str, Any],
    ren_payload: dict[str, Any],
) -> str | None:
    """Short evidence string: which sources were queried."""
    bits: list[str] = []
    for label, payload in (
        ("authority", auth_payload),
        ("lp", lp_payload),
        ("renounced", ren_payload),
    ):
        src = payload.get("source") if isinstance(payload, dict) else None
        if src:
            bits.append(f"{label}={src}")
    return ";".join(bits) if bits else None
