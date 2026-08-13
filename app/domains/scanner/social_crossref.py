"""Social cross-reference for the token scanner.

Flags ``deployer is a known scammer`` / ``wallet belongs to a tracked KOL``
by cross-referencing a wallet address **or** a social handle against two
static datasets:

- ``app/databus/known_scammers.json`` — known scammer accounts
  (``accounts``: ``handle``/``name``/``category``, optional ``aliases``).
  NOTE: as of 2026-08-13 none of the 31 accounts carry an address field
  ([REVIEW] — address cross-ref only matches if entries gain one), so
  handle/alias matching is the primary scammer signal today.
- ``app/databus/verified_kol_wallets.json`` — verified KOL wallets
  (``wallets``: ``address``/``chain``/``handle``/``name``; 945 wallets across
  solana/ethereum/bsc).

Self-contained: this module does not touch scan-report code — the scanner
lead wires the result dict into the report.

Matching rules
--------------
- EVM addresses (``0x``-prefixed): matched case-insensitively, checksum-
  normalized by lowercasing (mirrors ``shill_detector._normalize_address``).
- Solana/base58 addresses: matched exact-case (base58 is case-sensitive).
- Handles: matched case-insensitively, ``@`` prefix stripped; scammer
  ``aliases`` are indexed too.
- A scammer hit dominates a KOL hit for the same input.
- Fail-open: missing/corrupt JSON, bad input, or no match → ``{}``.
"""

from __future__ import annotations

import json
import os
from typing import Any

KNOWN_SCAMMERS_PATH = os.getenv(
    "KNOWN_SCAMMERS_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "databus", "known_scammers.json"),
)
VERIFIED_KOL_WALLETS_PATH = os.getenv(
    "VERIFIED_KOL_WALLETS_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "databus", "verified_kol_wallets.json"),
)

_BASE58_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


# ═══════════════════════════════════════════════════════════════════════
# Loaders (fail-open, accept explicit paths for tests)
# ═══════════════════════════════════════════════════════════════════════


def load_known_scammers(path: str | None = None) -> list[dict[str, Any]]:
    """Load scammer accounts from JSON. Never raises — empty list on error.

    Accepts the real file shape (``{"accounts": [...]}``) or a bare list.
    """
    path = path or KNOWN_SCAMMERS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(raw, dict):
        raw = raw.get("accounts") or raw.get("entries") or []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def load_kol_wallets(path: str | None = None) -> list[dict[str, Any]]:
    """Load KOL wallet rows from JSON. Never raises — empty list on error.

    Accepts the real file shape (``{"wallets": [...]}``) or a bare list.
    """
    path = path or VERIFIED_KOL_WALLETS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(raw, dict):
        raw = raw.get("wallets") or raw.get("entries") or []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


# ═══════════════════════════════════════════════════════════════════════
# Normalization + index builders
# ═══════════════════════════════════════════════════════════════════════


def _norm_addr(addr: Any) -> str:
    """EVM → lowercase (checksum-normalized for comparison); base58 → exact."""
    s = str(addr or "").strip()
    if not s:
        return ""
    if s[:2].lower() == "0x":
        return s.lower()
    return s


def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _looks_like_address(value: Any) -> bool:
    """True for EVM (0x...) or Solana/base58-style (32-44 chars) inputs."""
    s = str(value or "").strip()
    if s[:2].lower() == "0x":
        return True
    if "@" in s or "/" in s or " " in s:
        return False
    if not (32 <= len(s) <= 44):
        return False
    return all(c in _BASE58_ALPHABET for c in s)


def _index_scammer_handles(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_handle: dict[str, dict[str, Any]] = {}
    for entry in entries:
        account = dict(entry)
        handle = _norm_handle(account.get("handle"))
        if not handle:
            continue
        by_handle[handle] = account
        for alias in account.get("aliases") or []:
            norm = _norm_handle(alias)
            if norm:
                by_handle.setdefault(norm, account)
    return by_handle


def _index_scammer_addresses(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index account ``address``/``addresses``/``wallets`` fields, if any.

    The production file currently has none ([REVIEW]) — this exists so the
    cross-ref is correct the moment entries gain address fields.
    """
    by_addr: dict[str, dict[str, Any]] = {}
    for entry in entries:
        account = dict(entry)
        for key in ("address", "addresses", "wallets"):
            value = account.get(key)
            if isinstance(value, str):
                items: list[Any] = [value]
            elif isinstance(value, list):
                items = value
            else:
                continue
            for item in items:
                norm = _norm_addr(item)
                if norm:
                    by_addr.setdefault(norm, account)
    return by_addr


def _index_kol_addresses(entries: list[dict[str, Any]], chain: str = "") -> dict[str, dict[str, Any]]:
    by_addr: dict[str, dict[str, Any]] = {}
    target = str(chain or "").lower()
    for entry in entries:
        wallet = dict(entry)
        norm = _norm_addr(wallet.get("address"))
        if not norm:
            continue
        existing = by_addr.get(norm)
        if existing is None:
            by_addr[norm] = wallet
        elif (
            target
            and str(wallet.get("chain") or "").lower() == target
            and str(existing.get("chain") or "").lower() != target
        ):
            by_addr[norm] = wallet  # prefer a same-chain attribution
    return by_addr


def _index_kol_handles(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_handle: dict[str, dict[str, Any]] = {}
    for entry in entries:
        wallet = dict(entry)
        handle = _norm_handle(wallet.get("handle"))
        if handle:
            by_handle.setdefault(handle, wallet)
    return by_handle


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def crossref_wallet(
    address: str,
    chain: str = "",
    *,
    known_scammers: list[dict[str, Any]] | None = None,
    kol_wallets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cross-reference a wallet address or social handle.

    Args:
        address: wallet address (EVM ``0x...`` or Solana base58) **or** a
            handle (``@name`` accepted).
        chain: chain hint (``solana``/``ethereum``/``bsc``...) used to prefer
            same-chain KOL attributions; not required for matching.
        known_scammers: override list of scammer account dicts (tests);
            None → load from ``known_scammers.json``.
        kol_wallets: override list of KOL wallet dicts (tests);
            None → load from ``verified_kol_wallets.json``.

    Returns (fail-open → ``{}`` on error or no match):
    ``{known_scammer, scammer_handle, scammer_category, kol_handle, kol_name}``.
    A scammer hit dominates a KOL hit.
    """
    try:
        scammers = list(known_scammers) if known_scammers is not None else list(KNOWN_SCAMMERS)
        wallets = list(kol_wallets) if kol_wallets is not None else list(KOL_WALLETS)

        raw = str(address or "").strip()
        if not raw:
            return {}

        scam_by_handle = _index_scammer_handles(scammers)
        scam_by_addr = _index_scammer_addresses(scammers)
        kol_by_handle = _index_kol_handles(wallets)
        kol_by_addr = _index_kol_addresses(wallets, chain)

        if _looks_like_address(raw):
            norm = _norm_addr(raw)
            scam = scam_by_addr.get(norm)
            kol = None if scam else kol_by_addr.get(norm)
        else:
            norm = _norm_handle(raw)
            scam = scam_by_handle.get(norm)
            kol = None if scam else kol_by_handle.get(norm)
        return _build_result(scam, kol)
    except Exception:  # fail-open: never raise into the scan
        return {}


def _build_result(scam: dict[str, Any] | None, kol: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "known_scammer": False,
        "scammer_handle": None,
        "scammer_category": None,
        "kol_handle": None,
        "kol_name": None,
    }
    if scam:
        result["known_scammer"] = True
        result["scammer_handle"] = scam.get("handle")
        result["scammer_category"] = scam.get("category")
        return result
    if kol:
        result["kol_handle"] = kol.get("handle")
        result["kol_name"] = kol.get("name")
        return result
    return {}


def crossref_signal(result: dict[str, Any]) -> str | None:
    """Render a human-readable signal string, or None when clean."""
    if result.get("known_scammer"):
        handle = str(result.get("scammer_handle") or "unknown")
        category = result.get("scammer_category")
        if category:
            return f"deployer is known scammer @{handle} ({category})"
        return f"deployer is known scammer @{handle}"
    if result.get("kol_handle"):
        return f"wallet belongs to KOL @{result.get('kol_handle')}"
    return None


KNOWN_SCAMMERS: list[dict[str, Any]] = load_known_scammers()
KOL_WALLETS: list[dict[str, Any]] = load_kol_wallets()
