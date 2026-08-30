"""KOL wallet resolvers — ENS / Lens / Farcaster / label lookup leaf helpers.

Extracted from ``app.databus.kol_wallets`` during the de-godding split
(2026-08-16). Pure module-level async helpers — no class state, no I/O beyond
the HTTP calls they make. Re-exported by ``kol_wallets`` so the external
import surface (``ens_namehash``, ``extract_addresses``, ``extract_ens_names``,
``is_valid_evm``) is unchanged.
"""

from __future__ import annotations

import os
import re
from typing import Any

import base58
import httpx

from app.chain_registry import get_rpc_urls
from app.core.logging import get_logger
from app.databus.vault import resolve_key

logger = get_logger("databus.kol_wallets")

# ── Address detection / validation ─────────────────────────────────

_EVM_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_SOLANA_CANDIDATE_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_HEX_ONLY_RE = re.compile(r"^[0-9a-fA-F]{38,66}$")  # tx-hash-shaped → not a Solana key


def is_valid_evm(address: str) -> bool:
    """True for canonical 0x + 40 hex EVM addresses (lowercase-normalized)."""
    return bool(_EVM_RE.fullmatch(address.strip()))


def is_valid_solana(address: str) -> bool:
    """True for base58 Solana pubkeys (32 bytes after decoding).

    Excludes hex-looking strings (tx hashes / hex blobs) to avoid false
    positives when scanning free-form text.
    """
    candidate = address.strip()
    if not _SOLANA_CANDIDATE_RE.fullmatch(candidate):
        return False
    if _HEX_ONLY_RE.fullmatch(candidate):
        return False
    try:
        return len(base58.b58decode(candidate)) == 32
    except (ValueError, TypeError):
        return False


def extract_addresses(text: str) -> list[tuple[str, str]]:
    """Extract (address, chain) pairs from free-form text.

    Multi-chain: EVM 0x… plus Solana base58 keys.
    """
    found: list[tuple[str, str]] = []
    for m in _EVM_RE.finditer(text):
        found.append((m.group(0).lower(), "ethereum"))
    for m in _SOLANA_CANDIDATE_RE.finditer(text):
        candidate = m.group(0)
        if is_valid_solana(candidate):
            found.append((candidate, "solana"))
    # Deduplicate, keep first occurrence order.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in found:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def extract_ens_names(text: str) -> list[str]:
    """Extract .eth ENS names from text (handle-agnostic)."""
    return sorted(set(re.findall(r"\b[a-z0-9][a-z0-9-]{0,62}\.eth\b", text.lower())))


# ── ENS resolution (public API + RPC fallback) ─────────────────────

_ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
_ENS_RESOLVER_URL = os.getenv("ENS_RESOLVER_URL", "https://api.ensideas.com/ens/resolve/{name}")


def ens_namehash(name: str) -> bytes:
    """ENS namehash (EIP-137). Empty name → the zero root node."""
    from eth_utils import keccak  # type: ignore[attr-defined]  # re-export, no stubs

    node = b"\x00" * 32
    name = name.strip().rstrip(".")
    if not name:
        return node
    for label in reversed(name.split(".")):
        node = keccak(node + keccak(label.encode("utf-8")))
    return node


async def _rpc_call(
    client: httpx.AsyncClient,
    rpc_url: str,
    to: str,
    data: str,
) -> str | None:
    """Raw JSON-RPC eth_call returning the hex result, or None."""
    try:
        resp = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]},
            timeout=10.0,
        )
        payload = resp.json()
        result = payload.get("result")
        return result if isinstance(result, str) and result.startswith("0x") else None
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def resolve_ens_name(client: httpx.AsyncClient, name: str) -> str | None:
    """Resolve an ENS name to a lowercase EVM address.

    Primary: public ENS resolver API (no key). Fallback: raw eth_call
    against RPC URLs from chain_registry (no hardcoded RPC endpoints).
    """
    name = name.lower().strip()
    try:
        resp = await client.get(_ENS_RESOLVER_URL.format(name=name), timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            addr = data.get("address") if isinstance(data, dict) else None
            if isinstance(addr, str) and is_valid_evm(addr):
                return addr.lower()
    except (httpx.HTTPError, ValueError):
        logger.debug("ENS API resolve failed for %s", name)

    # Fallback: eth_call against chain_registry RPC URLs.
    from eth_abi import encode  # type: ignore[attr-defined]  # no stubs
    from eth_utils import keccak  # type: ignore[attr-defined]  # re-export, no stubs

    for rpc_url in get_rpc_urls("ethereum"):
        node = ens_namehash(name)
        resolver_call = keccak(b"resolver(bytes32)")[:4].hex() + encode(["bytes32"], [node]).hex()
        resolver_hex = await _rpc_call(client, rpc_url, _ENS_REGISTRY, "0x" + resolver_call)
        if not resolver_hex or resolver_hex == "0x" + "00" * 32:
            continue
        resolver = "0x" + resolver_hex[-40:]
        addr_call = keccak(b"addr(bytes32)")[:4].hex() + encode(["bytes32"], [node]).hex()
        addr_hex = await _rpc_call(client, rpc_url, resolver, "0x" + addr_call)
        if addr_hex and addr_hex != "0x" + "00" * 32:
            return ("0x" + addr_hex[-40:]).lower()
    return None


async def ens_text_record(
    client: httpx.AsyncClient,
    name: str,
    key: str,
) -> str | None:
    """Fetch an ENS text record (e.g. com.twitter) via RPC fallback."""
    from eth_abi import decode, encode  # type: ignore[attr-defined]  # no stubs
    from eth_utils import keccak  # type: ignore[attr-defined]  # re-export, no stubs

    node = ens_namehash(name.lower().strip())
    for rpc_url in get_rpc_urls("ethereum"):
        resolver_call = keccak(b"resolver(bytes32)")[:4].hex() + encode(["bytes32"], [node]).hex()
        resolver_hex = await _rpc_call(client, rpc_url, _ENS_REGISTRY, "0x" + resolver_call)
        if not resolver_hex or resolver_hex == "0x" + "00" * 32:
            continue
        resolver = "0x" + resolver_hex[-40:]
        text_call = keccak(b"text(bytes32,string)")[:4].hex() + encode(["bytes32", "string"], [node, key]).hex()
        text_hex = await _rpc_call(client, rpc_url, resolver, "0x" + text_call)
        if text_hex and text_hex != "0x" + "00" * 32:
            try:
                decoded = decode(["string"], bytes.fromhex(text_hex[2:]))
                return decoded[0] if decoded else None
            except (ValueError, OverflowError):
                continue
    return None


# ── Lens / Farcaster resolution (best-effort, public APIs) ─────────

_FARCASTER_USER_URL = "https://api.warpcast.com/v2/user-by-username?username={handle}"
_FARCASTER_VERIFICATIONS_URL = "https://api.warpcast.com/v2/verifications?fid={fid}"
_LENS_GRAPHQL_URL = "https://api-v2.lens.xyz/graphql"
_LENS_QUERY = """query ($handle: ProfileHandle!) {
  profile(byHandle: $handle) { ownedBy { address } }
}"""


async def resolve_farcaster(client: httpx.AsyncClient, handle: str) -> list[dict[str, Any]]:
    """Return verified ETH wallets for a Farcaster username (public API).

    Returns [{"address": str, "evidence": str}] — empty on any failure.
    """
    results: list[dict[str, Any]] = []
    try:
        resp = await client.get(_FARCASTER_USER_URL.format(handle=handle.lstrip("@")), timeout=10.0)
        if resp.status_code != 200:
            return results
        user = resp.json().get("result", {}).get("user", {})
        fid = user.get("fid")
        if not fid:
            return results
        ver = await client.get(_FARCASTER_VERIFICATIONS_URL.format(fid=fid), timeout=10.0)
        if ver.status_code != 200:
            return results
        for item in ver.json().get("result", {}).get("verifications", []):
            addr = item.get("address", "")
            if isinstance(addr, str) and is_valid_evm(addr):
                results.append(
                    {
                        "address": addr.lower(),
                        "evidence": f"farcaster_fid:{fid} verification",
                    }
                )
    except (httpx.HTTPError, ValueError):
        logger.debug("Farcaster resolution failed for @%s", handle)
    return results


async def resolve_lens(client: httpx.AsyncClient, handle: str) -> list[dict[str, Any]]:
    """Return owned EVM wallets for a Lens handle (public GraphQL).

    Disabled unless LENS_API_KEY is set (API may require auth).
    """
    key = resolve_key("LENS_API_KEY")
    if not key:
        return []
    try:
        resp = await client.post(
            _LENS_GRAPHQL_URL,
            json={"query": _LENS_QUERY, "variables": {"handle": handle.lstrip("@")}},
            headers={"x-api-key": key, "Origin": "https://rugmunch.io"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        profile = resp.json().get("data", {}).get("profile", {})
        owned = profile.get("ownedBy", {}).get("address", "")
        if isinstance(owned, str) and is_valid_evm(owned):
            return [{"address": owned.lower(), "evidence": f"lens_handle:{handle}"}]
    except (httpx.HTTPError, ValueError):
        logger.debug("Lens resolution failed for @%s", handle)
    return []


# ── Public tag lookup (Arkham / Lookonchain when keyed) ────────────

_ARKHAM_LABEL_URL = "https://api.arkhamintelligence.com/intelligence/address/{address}/labels"


async def _arkham_labels(client: httpx.AsyncClient, address: str) -> list[dict[str, Any]]:
    """Arkham entity labels (requires ARKHAM_API_KEY; else [])."""
    key = resolve_key("ARKHAM_API_KEY")
    if not key:
        return []
    try:
        resp = await client.get(
            _ARKHAM_LABEL_URL.format(address=address),
            headers={"API-Key": key},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        labels: list[dict[str, Any]] = []
        for item in data if isinstance(data, list) else [data]:
            label = item.get("label", "") if isinstance(item, dict) else ""
            if label:
                labels.append({"source": "arkham", "label": label, "category": "entity"})
        return labels
    except (httpx.HTTPError, ValueError):
        logger.debug("Arkham labels failed for %s", address)
        return []


async def _lookonchain_labels(client: httpx.AsyncClient, address: str) -> list[dict[str, Any]]:
    """Lookonchain public reports (requires LOOKONCHAIN_API_KEY; else []).

    Lookonchain does not ship an official public REST API; this hook is a
    placeholder for a configured proxy/keyed endpoint. Returns [] unless
    LOOKONCHAIN_API_URL + LOOKONCHAIN_API_KEY are both set.
    """
    base = os.getenv("LOOKONCHAIN_API_URL", "")
    key = resolve_key("LOOKONCHAIN_API_KEY")
    if not base or not key:
        return []
    try:
        resp = await client.get(
            f"{base.rstrip('/')}/address/{address}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        label = data.get("label", "") if isinstance(data, dict) else ""
        return [{"source": "lookonchain", "label": label, "category": "entity"}] if label else []
    except (httpx.HTTPError, ValueError):
        logger.debug("Lookonchain labels failed for %s", address)
        return []
