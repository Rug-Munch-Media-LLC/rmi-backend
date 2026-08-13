"""L1 (self-disclosure) wallet attribution layer with ownership proof.

Implements rung 1 of the Wallet-Attribution Evidence Ladder
(docs/SHILL-TRACKING-METHODOLOGY.md §3): a CA/address self-disclosed in
a pinned tweet or bio, an ENS/CNS domain owned by the handle, or a
signed message - confidence 0.9-1.0.

Ownership proofs resolve through an **injectable resolver** so the module
is fully testable offline:

- ``Mapping[str, str]`` — fake resolver ``{name: owner_address}`` (tests
  use this; no network)
- ``Callable[[str], ...]`` — custom resolver (sync or async)
- ``httpx.AsyncClient`` / ``None`` — live resolution: EIP-137 namehash +
  ``owner(bytes32)`` registry call via RPC URLs from chain_registry (no
  hardcoded RPC endpoints)

The live ENS path uses the canonical ENS registry
(0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e). The CNS (.bnb) registry
address is env-configurable via ``CNS_REGISTRY_ADDRESS``; without it the
live CNS path degrades to ``None`` (the injected-resolver path still
works). [REVIEW: verify CNS registry address before enabling live use.]

All helpers from :mod:`app.databus.kol_wallets` are reused, not
re-implemented: ``extract_addresses``, ``extract_ens_names``,
``ens_namehash``, ``is_valid_evm``, ``is_valid_solana``, plus
``normalize_handle`` (from :mod:`app.databus.kol_wallet_linker`, where it
is defined).
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from app.chain_registry import get_rpc_urls
from app.core.logging import get_logger
from app.databus.kol_wallet_linker import normalize_handle
from app.databus.kol_wallets import (
    AttributionMethod,
    Confidence,
    ens_namehash,
    extract_addresses,
    extract_ens_names,
    is_valid_evm,
)

logger = get_logger("databus.kol_attribution_l1")

# ── Constants ──────────────────────────────────────────────────────

#: Canonical ENS registry (public, well-known address).
_ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
#: CNS (.bnb) registry — env-configurable; live path no-ops when unset.
_CNS_REGISTRY = os.getenv("CNS_REGISTRY_ADDRESS", "").strip().lower()

#: L1 confidence band (methodology §3): 0.9 domain-ownership, 1.0 signed.
CONF_ENS_CNS = 0.9
CONF_SIGNED_MESSAGE = 1.0

#: CNS names are resolved like ENS (EIP-137 namehash + owner(bytes32)).
_CNS_RE = re.compile(r"\b[a-z0-9][a-z0-9-]{0,62}\.bnb\b")

#: Injectable resolver: fake mapping, custom callable, or httpx client.
Resolver = Mapping[str, str] | Callable[[str], Any] | httpx.AsyncClient | None


# ── Low-level JSON-RPC (mirrors the private helper in kol_wallets) ──


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


def _owner_to_address(raw: Any) -> str | None:
    """Normalize a resolver/owner result to a lowercase EVM address."""
    if isinstance(raw, str) and is_valid_evm(raw):
        return raw.lower()
    return None


async def _resolve_owner_rpc(
    client: httpx.AsyncClient,
    rpc_urls: list[str],
    registry: str,
    name: str,
) -> str | None:
    """EIP-137 owner(bytes32) lookup: namehash → registry → owner."""
    from eth_abi import encode  # type: ignore[attr-defined]  # no stubs
    from eth_utils import keccak  # type: ignore[attr-defined]  # re-export, no stubs

    node = ens_namehash(name)
    owner_call = keccak(b"owner(bytes32)")[:4].hex() + encode(["bytes32"], [node]).hex()
    for rpc_url in rpc_urls:
        result_hex = await _rpc_call(client, rpc_url, registry, "0x" + owner_call)
        if not result_hex or result_hex == "0x" + "00" * 32:
            continue
        return ("0x" + result_hex[-40:]).lower()
    return None


async def _resolve_owner(
    name: str,
    *,
    resolver: Resolver,
    rpc_urls: list[str],
    registry: str,
) -> str | None:
    """Resolve the owner of ``name`` through the injected resolver."""
    name = name.lower().strip()
    if resolver is None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await _resolve_owner_rpc(client, rpc_urls, registry, name)
    if isinstance(resolver, httpx.AsyncClient):
        return await _resolve_owner_rpc(resolver, rpc_urls, registry, name)
    if isinstance(resolver, Mapping):
        return _owner_to_address(resolver.get(name))
    # Custom callable resolver (sync or async).
    try:
        out = resolver(name)
        if isinstance(out, Awaitable):
            out = await out
        return _owner_to_address(out)
    except Exception:
        # Pluggable boundary — a broken custom resolver degrades to None.
        logger.debug("custom resolver failed for %s", name)
        return None


# ── Public ownership resolution ────────────────────────────────────


async def resolve_ens_owner(ens_name: str, *, resolver: Resolver = None) -> str | None:
    """Resolve the owner of an ENS (.eth) name to a lowercase EVM address.

    Live path: EIP-137 namehash + ``owner(bytes32)`` on the ENS registry
    via RPC URLs from chain_registry. For tests pass ``resolver`` as a
    ``{name: owner}`` mapping (no network).
    """
    return await _resolve_owner(
        ens_name,
        resolver=resolver,
        rpc_urls=get_rpc_urls("ethereum"),
        registry=_ENS_REGISTRY,
    )


async def resolve_cns_owner(cns_name: str, *, resolver: Resolver = None) -> str | None:
    """Resolve the owner of a CNS (.bnb) name to a lowercase EVM address.

    CNS follows the EIP-137 registry layout on BSC. Live path requires
    ``CNS_REGISTRY_ADDRESS`` to be set; otherwise degrades to None. Tests
    pass a fake resolver mapping and need no network.
    """
    if not _CNS_REGISTRY and resolver is None:
        logger.debug("CNS live resolution requires CNS_REGISTRY_ADDRESS; skipping %s", cns_name)
        return None
    return await _resolve_owner(
        cns_name,
        resolver=resolver,
        rpc_urls=get_rpc_urls("bsc"),
        registry=_CNS_REGISTRY,
    )


# ── Ownership proof ────────────────────────────────────────────────


def _structurally_valid_signature(signature: str | None, message: str | None) -> bool:
    """Structural ECDSA signature validation (no cryptographic recovery).

    Accepts 0x-prefixed r||s (64 bytes) or r||s||v (65 bytes) hex and a
    non-empty message. [REVIEW: verifies shape only — not that the
    signature was produced by the address's private key.]
    """
    if not message or not str(message).strip():
        return False
    if not isinstance(signature, str):
        return False
    sig = signature.strip()
    if not sig.lower().startswith("0x"):
        return False
    body = sig[2:]
    if len(body) not in (128, 130):
        return False
    try:
        raw = bytes.fromhex(body)
    except ValueError:
        return False
    return len(raw) in (64, 65)


async def prove_ownership(
    handle: str,
    address: str,
    *,
    proof_type: str,
    resolver: Resolver = None,
    signature: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Prove the handle owns ``address`` via L1 ownership evidence.

    Returns ``{owned, confidence, method, evidence}``.

    proof_type:
      - ``"ens"`` — the ENS name ``{handle}.eth`` must resolve to
        ``address`` (confidence 0.9).
      - ``"cns"`` — the CNS name ``{handle}.bnb`` must resolve to
        ``address`` (confidence 0.9).
      - ``"signed_message"`` — accept a structurally valid ``signature``
        (0x r||s[||v]) plus a non-empty ``message`` (confidence 1.0).
        Validation is structural, not cryptographic.
    """
    handle_norm = normalize_handle(handle)
    address_l = str(address or "").strip().lower()

    if proof_type == "ens":
        name = f"{handle_norm}.eth"
        owner = await resolve_ens_owner(name, resolver=resolver)
        owned = bool(owner and owner == address_l)
        return {
            "owned": owned,
            "confidence": CONF_ENS_CNS if owned else 0.0,
            "method": "ens_ownership",
            "evidence": f"ens:{name} owner={owner or 'unresolved'}",
        }

    if proof_type == "cns":
        name = f"{handle_norm}.bnb"
        owner = await resolve_cns_owner(name, resolver=resolver)
        owned = bool(owner and owner == address_l)
        return {
            "owned": owned,
            "confidence": CONF_ENS_CNS if owned else 0.0,
            "method": "cns_ownership",
            "evidence": f"cns:{name} owner={owner or 'unresolved'}",
        }

    if proof_type == "signed_message":
        owned = _structurally_valid_signature(signature, message)
        return {
            "owned": owned,
            "confidence": CONF_SIGNED_MESSAGE if owned else 0.0,
            "method": "signed_message",
            "evidence": f"signed message by @{handle_norm}" if owned else "invalid signature/message",
        }

    raise ValueError(f"unknown proof_type: {proof_type!r}")


# ── Bio self-disclosure scan ───────────────────────────────────────


def _link(
    kol_id: str,
    chain: str,
    address: str,
    confidence: float,
    evidence: str,
) -> dict[str, Any]:
    """Build one L1 link dict (active by default)."""
    return {
        "kol_id": kol_id,
        "chain": chain,
        "address": address,
        "attribution_method": AttributionMethod.SELF_DISCLOSED.value,
        "confidence": confidence,
        "confidence_tier": Confidence.DETERMINISTIC.value,
        "evidence": evidence,
        "status": "active",
    }


async def attribution_from_bio(
    handle: str,
    bio_text: str,
    *,
    resolver: Resolver = None,
) -> list[dict[str, Any]]:
    """Extract L1 links from a KOL bio.

    Sources, in order:
      1. Direct self-disclosed addresses (chain inferred via
         :func:`extract_addresses`: EVM → ethereum, Solana → solana).
      2. ENS/CNS names disclosed in the bio — the resolved owner becomes
         the attributed address (ownership proof, confidence 0.9).
      3. Handle-derived domains ``{handle}.eth`` / ``{handle}.bnb`` (the
         methodology's "domain owned by the handle" form).

    All links are deduped via :func:`merge_l1` on (kol_id, chain,
    address), keeping the highest confidence.
    """
    handle_norm = normalize_handle(handle)
    text = bio_text or ""
    links: list[dict[str, Any]] = []

    # 1. Direct addresses.
    for address, chain in extract_addresses(text):
        links.append(
            _link(
                handle_norm,
                chain,
                address,
                CONF_ENS_CNS,
                f"self-disclosed in bio of @{handle_norm} ({chain})",
            )
        )

    # 2. ENS/CNS names disclosed in the bio (extract_ens_names handles
    #    .eth; a small local regex handles .bnb).
    names: list[tuple[str, str, str]] = []
    for name in extract_ens_names(text):
        names.append((name, "eth", "ethereum"))
    for m in _CNS_RE.finditer(text.lower()):
        names.append((m.group(0), "bnb", "bsc"))

    # 3. Handle-derived domains (deduped below against bio-disclosed).
    names.append((f"{handle_norm}.eth", "eth", "ethereum"))
    names.append((f"{handle_norm}.bnb", "bnb", "bsc"))

    seen: set[str] = set()
    for name, tld, chain in names:
        if name in seen:
            continue
        seen.add(name)
        owner = (
            await resolve_ens_owner(name, resolver=resolver)
            if tld == "eth"
            else await resolve_cns_owner(name, resolver=resolver)
        )
        if not owner:
            continue
        links.append(
            _link(
                handle_norm,
                chain,
                owner,
                CONF_ENS_CNS,
                f"{tld}:{name} owner={owner} (bio of @{handle_norm})",
            )
        )

    return merge_l1(links, [])


# ── Merge / dedupe ─────────────────────────────────────────────────


def _l1_key(link: dict[str, Any]) -> tuple[str, str, str]:
    """Dedupe key: (kol_id, chain, address).

    EVM addresses are lowercase-normalized (case-insensitive); Solana
    base58 addresses are kept as-is (case-sensitive).
    """
    address = str(link.get("address", "")).strip()
    if is_valid_evm(address):
        address = address.lower()
    return (
        str(link.get("kol_id", "")).lower(),
        str(link.get("chain", "")).lower(),
        address,
    )


def merge_l1(links: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe L1 links by (kol_id, chain, address), keeping highest confidence.

    Existing entries keep their position; on a confidence tie the first
    occurrence wins (stable).
    """
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []

    for link in existing:
        key = _l1_key(link)
        if key not in merged:
            order.append(key)
        merged[key] = link

    for link in links:
        key = _l1_key(link)
        if key not in merged:
            order.append(key)
            merged[key] = link
            continue
        cur = merged[key]
        if float(link.get("confidence", 0.0)) > float(cur.get("confidence", 0.0)):
            merged[key] = link

    return [merged[key] for key in order]
