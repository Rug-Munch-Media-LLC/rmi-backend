"""KOL wallet cross-chain linking + funding-source detection.

Builds on :mod:`app.databus.kol_wallets` attribution. Where that module
attaches addresses to a KOL *per chain*, this module:

1. **Cross-chain linkage** — group a KOL's EVM + Solana wallets under the
   same ``kol_id`` when any of these hold:

   - shared handle — the registry lists both chains for the same handle
     (``registry_both_chains``), or a ``.eth`` / ``.sol`` handle token
     extracted from ``name``/``handle`` matches across chains
     (``shared_handle``)
   - shared funding source — both wallets were funded by the same source
     address (``shared_funding``; requires a funding provider or
     registry-level ``funded_by`` evidence)

2. **Funding-source pattern** — if a KOL wallet was funded BY a known
   deployer / rug-puller address, that is a strong paid-shill signal.
   The match is recorded as a :class:`FundingSignal` and the shill
   confidence is escalated (``escalate()``). The wallet-link confidence
   can also be raised via :meth:`KolWalletLinker.escalate_wallet_confidence`
   — receiving funds from a known KOL payer corroborates that the wallet
   actually belongs to the KOL.

3. **Consolidation** — dedupe the wallet registry: the same address under
   multiple names resolves to one canonical ``kol_id`` (prefers verified
   + deterministic/strong entries).

All core logic is offline/deterministic; the live funding lookup is an
injected async provider so dry runs need no network.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

from .kol_wallets import (
    Confidence,
    WalletLink,
    _confidence_rank,
    load_verified_kol_wallets,
)

logger = get_logger("databus.kol_wallet_linker")

# ── Handle / address normalization ─────────────────────────────────

_HANDLE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
#: ENS / SNS handle suffixes that carry strong identity signal.
_ENS_SNS_RE = re.compile(r"\b([a-z0-9][a-z0-9_-]{0,62})\.(?:eth|sol)\b")


def normalize_handle(handle: Any) -> str:
    """Lower-case, strip '@' — the canonical kol_id form."""
    return str(handle or "").lstrip("@").strip().lower()


def _ens_sns_tokens(text: str) -> set[str]:
    """Strong identity tokens — only from ``*.eth`` / ``*.sol`` substrings.

    ``"Assasin.eth"`` → {"assasin"}; ``"vitalik.sol"`` → {"vitalik"}.
    Plain-name words (``"Crypto King"``) are NOT strong tokens: they are
    too generic to link two unrelated KOLs.
    """
    return {m.group(1) for m in _ENS_SNS_RE.finditer(text.lower())}


# ── Data model ─────────────────────────────────────────────────────


@dataclass(slots=True)
class WalletRecord:
    """Normalized registry entry (one wallet per record)."""

    handle: str
    name: str
    chain: str
    address: str
    confidence: Confidence
    source: str
    evidence_url: str = ""
    verified: bool = False
    funded_by: str | None = None  # registry-level funding evidence

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> WalletRecord:
        confidence = Confidence.PROBABILISTIC
        try:
            confidence = Confidence(str(entry.get("confidence") or "probabilistic").lower())
        except ValueError:
            logger.warning("unknown confidence %r; defaulting probabilistic", entry.get("confidence"))
        return cls(
            handle=normalize_handle(entry.get("handle")),
            name=str(entry.get("name") or "").strip(),
            chain=str(entry.get("chain") or "").strip().lower(),
            address=str(entry.get("address") or "").strip().lower(),
            confidence=confidence,
            source=str(entry.get("source") or "unknown").strip(),
            evidence_url=str(entry.get("evidence_url") or "").strip(),
            verified=bool(entry.get("verified")),
            funded_by=str(entry.get("funded_by") or "").strip() or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "name": self.name,
            "chain": self.chain,
            "address": self.address,
            "confidence": self.confidence.value,
            "source": self.source,
            "evidence_url": self.evidence_url,
            "verified": self.verified,
            "funded_by": self.funded_by,
        }


@dataclass(slots=True)
class CrossChainLink:
    """A set of wallets grouped under one kol_id with linkage evidence."""

    kol_id: str
    addresses: list[dict[str, str]] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.PROBABILISTIC
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kol_id": self.kol_id,
            "addresses": list(self.addresses),
            "methods": list(self.methods),
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
        }


@dataclass(slots=True)
class FundingSignal:
    """A KOL wallet funded by a known deployer / rug address.

    ``shill_confidence`` is the pre-escalation strength of the paid-shill
    signal; ``escalated_confidence`` is after :meth:`escalate`.
    """

    kol_id: str
    wallet_address: str
    chain: str
    funder_address: str
    funder_label: str
    method: str
    shill_confidence: float = 0.8
    escalated_confidence: float = 0.8
    evidence: str = ""

    def escalate(self, amount: float = 0.15, cap: float = 0.99) -> None:
        self.escalated_confidence = min(cap, self.shill_confidence + amount)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kol_id": self.kol_id,
            "wallet_address": self.wallet_address,
            "chain": self.chain,
            "funder_address": self.funder_address,
            "funder_label": self.funder_label,
            "method": self.method,
            "shill_confidence": self.shill_confidence,
            "escalated_confidence": self.escalated_confidence,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class ConsolidationRecord:
    """Result of consolidating duplicate addresses under one canonical kol_id."""

    canonical_handle: str
    canonical_name: str
    chain: str
    address: str
    aliases: list[dict[str, Any]] = field(default_factory=list)
    total_entries: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_handle": self.canonical_handle,
            "canonical_name": self.canonical_name,
            "chain": self.chain,
            "address": self.address,
            "aliases": list(self.aliases),
            "total_entries": self.total_entries,
        }


# ── Known funders registry ─────────────────────────────────────────

#: Type of a live funding lookup: (address, chain) -> list of funder
#: addresses. Injected so dry runs stay offline.
FundingProvider = Callable[[str, str], Awaitable[list[str]]]


def load_known_funders(path: str | None = None) -> dict[str, str]:
    """Load ``address -> label`` map of known deployer/rug funders.

    Tolerates a missing file (returns {}). Expected shape — a dict of
    ``{"<address>": "<label>"}`` or ``{"funders": {...}}``.
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "known_funders.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        logger.debug("known_funders file %s unreadable; continuing empty", path)
        return {}
    if isinstance(raw, dict) and "funders" in raw:
        raw = raw["funders"]
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip().lower(): str(v) for k, v in raw.items() if str(k).strip()}


# ── The linker ─────────────────────────────────────────────────────


class KolWalletLinker:
    """Cross-chain linking, funding-source detection, registry dedupe."""

    def __init__(
        self,
        records: list[WalletRecord] | None = None,
        known_funders: dict[str, str] | None = None,
        funding_provider: FundingProvider | None = None,
        verified_list_path: str | None = None,
    ) -> None:
        if records is not None:
            self.records = records
        else:
            self.records = [WalletRecord.from_entry(entry) for entry in load_verified_kol_wallets(verified_list_path)]
        self.known_funders = (
            {str(k).strip().lower(): v for k, v in known_funders.items()}
            if known_funders is not None
            else load_known_funders()
        )
        self.funding_provider = funding_provider

    # ── Cross-chain linkage ───────────────────────────────────────

    def records_for(self, kol_id: str) -> list[WalletRecord]:
        """All registry records whose handle matches ``kol_id``."""
        target = normalize_handle(kol_id)
        return [r for r in self.records if r.handle == target]

    def _records_for_token(self, token: str) -> list[WalletRecord]:
        """Records whose handle or ENS/SNS handle token matches ``token``."""
        token = token.lower()
        matches: list[WalletRecord] = []
        for r in self.records:
            if r.handle == token or token in _ens_sns_tokens(r.handle) or token in _ens_sns_tokens(r.name):
                matches.append(r)
        return matches

    def link_cross_chain(self, kol_id: str) -> CrossChainLink | None:
        """Group all chains for one KOL; return None if no records exist.

        Linkage evidence, in strength order:
        1. ``registry_both_chains`` — the registry lists EVM + Solana
           wallets for the same handle.
        2. ``shared_handle`` — a ``.eth`` / ``.sol`` handle token matches
           across chains (ENS/SNS handles share the same base name).
        3. ``shared_funding`` — registry-level ``funded_by`` evidence
           shows both wallets funded by the same address.
        """
        kol_id = normalize_handle(kol_id)
        direct = self.records_for(kol_id)
        if not direct:
            return None

        # Records linked by identity: exact handle match, or a shared
        # strong ENS/SNS handle token (e.g. `assasin` from `Assasin.eth`).
        strong_tokens = {kol_id}
        for r in direct:
            strong_tokens |= _ens_sns_tokens(r.name)
        linked_keys: set[str] = set()
        for token in strong_tokens:
            for r in self._records_for_token(token):
                linked_keys.add(f"{r.chain}:{r.address}")

        # Evidence 3 seed: expand with any records sharing a funder with
        # an already-linked record (distinct handles, same payer → same KOL).
        funded_by_map: dict[str, list[tuple[str, str]]] = {}
        for r in self.records:
            if r.funded_by and f"{r.chain}:{r.address}" in linked_keys:
                funded_by_map.setdefault(r.funded_by, []).append((r.chain, r.address))
        for funder, addrs in funded_by_map.items():
            for r in self.records:
                if r.funded_by == funder:
                    linked_keys.add(f"{r.chain}:{r.address}")
                    if (r.chain, r.address) not in addrs:
                        addrs.append((r.chain, r.address))

        addresses: list[dict[str, str]] = []
        methods: list[str] = []
        evidence: list[str] = []
        chains: set[str] = set()
        for r in self.records:
            if f"{r.chain}:{r.address}" not in linked_keys:
                continue
            addresses.append({"address": r.address, "chain": r.chain, "handle": r.handle})
            chains.add(r.chain)

        # Evidence 1: registry lists multiple chains for the same handle.
        if len({r.handle for r in direct}) == 1 and len({r.chain for r in direct}) > 1:
            methods.append("registry_both_chains")
            evidence.append(f"verified registry lists {sorted({r.chain for r in direct})} for @{kol_id}")

        # Evidence 2: shared ENS/SNS handle token across chains.
        if len(chains) > 1:
            for token in sorted(strong_tokens):
                token_records = self._records_for_token(token)
                token_chains = {r.chain for r in token_records}
                if len(token_chains) > 1:
                    methods.append("shared_handle")
                    evidence.append(f"handle token '{token}' resolves on {sorted(token_chains)}")
                    break

        # Evidence 3: shared funding source (registry-level funded_by).
        for funder, addrs in funded_by_map.items():
            addr_chains = {c for c, _ in addrs}
            if len(addr_chains) > 1:
                methods.append("shared_funding")
                evidence.append(f"both chains funded by {funder}")
                break

        if not methods:
            # Single-chain record only — still a valid (trivial) link.
            methods.append("single_chain")
            confidence = max((r.confidence for r in direct), key=_confidence_rank, default=Confidence.PROBABILISTIC)
        else:
            confidence = Confidence.DETERMINISTIC if "registry_both_chains" in methods else Confidence.STRONG_HEURISTIC

        # Dedupe addresses, keep first occurrence order.
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, str]] = []
        for a in addresses:
            key = (a["address"], a["chain"])
            if key not in seen:
                seen.add(key)
                unique.append(a)

        return CrossChainLink(
            kol_id=kol_id,
            addresses=unique,
            methods=methods,
            confidence=confidence,
            evidence=evidence,
        )

    # ── Funding-source detection ──────────────────────────────────

    async def detect_funding_signal(
        self,
        kol_id: str,
        wallet_address: str,
        chain: str,
    ) -> FundingSignal | None:
        """Check whether ``wallet_address`` was funded by a known rug/deployer.

        Uses the injected ``funding_provider`` for live lookups; falls
        back to registry-level ``funded_by`` evidence. Returns None when
        no known funder is found or lookups are unavailable.
        """
        kol_id = normalize_handle(kol_id)
        wallet_address = wallet_address.strip().lower()
        chain = chain.strip().lower()

        # 1. Registry-level evidence (offline, deterministic).
        for r in self.records:
            if r.address == wallet_address and r.chain == chain and r.funded_by:
                label = self.known_funders.get(r.funded_by, "known deployer/rug address")
                if label or r.funded_by in self.known_funders:
                    signal = FundingSignal(
                        kol_id=kol_id,
                        wallet_address=wallet_address,
                        chain=chain,
                        funder_address=r.funded_by,
                        funder_label=label,
                        method="funded_by_registry",
                        evidence=f"registry funded_by={r.funded_by} (source={r.source})",
                    )
                    signal.escalate()
                    return signal

        # 2. Live lookup via injected provider.
        if self.funding_provider is None:
            return None
        try:
            funders = await self.funding_provider(wallet_address, chain)
        except Exception as e:  # provider is best-effort
            logger.debug("funding provider failed for %s/%s: %s", chain, wallet_address, e)
            return None
        for funder in funders:
            key = funder.strip().lower()
            funder_label = self.known_funders.get(key)
            if funder_label is not None:
                signal = FundingSignal(
                    kol_id=kol_id,
                    wallet_address=wallet_address,
                    chain=chain,
                    funder_address=key,
                    funder_label=funder_label,
                    method="funded_by_provider",
                    evidence=f"funding provider reported funder {key}",
                )
                signal.escalate()
                return signal
        return None

    def escalate_wallet_confidence(self, link: WalletLink, signal: FundingSignal) -> WalletLink:
        """Raise a wallet-link confidence when a known funder paid for it.

        Funding by a known deployer/rug address both flags a paid shill
        AND corroborates that the wallet genuinely belongs to the KOL —
        the funder only pays wallets they control or know. Returns a new
        WalletLink with the bumped confidence (never above
        ``strong_heuristic`` from this path alone).
        """
        rank = _confidence_rank(link.confidence)
        if rank >= _confidence_rank(Confidence.STRONG_HEURISTIC):
            return link
        upgraded = WalletLink(
            address=link.address,
            chain=link.chain,
            method=link.method,
            confidence=Confidence.STRONG_HEURISTIC,
            evidence=f"{link.evidence} | funded_by {signal.funder_address} ({signal.funder_label})",
            signals=[*list(link.signals), "funded_by_known_deployer"],
        )
        return upgraded

    # ── Consolidation / dedupe ────────────────────────────────────

    def consolidate(self) -> list[ConsolidationRecord]:
        """Resolve duplicate (address, chain) entries to one canonical KOL.

        Canonical selection, in order: verified entry > higher confidence
        rank > non-empty name > lexicographically smaller handle. Records
        the dropped aliases for auditability.
        """
        groups: dict[tuple[str, str], list[WalletRecord]] = {}
        for r in self.records:
            key = (r.address, r.chain)
            groups.setdefault(key, []).append(r)

        result: list[ConsolidationRecord] = []
        for (address, chain), members in groups.items():
            if len(members) == 1:
                continue
            canonical = max(
                members,
                key=lambda r: (
                    r.verified,
                    _confidence_rank(r.confidence),
                    bool(r.name.strip()),
                    -ord(r.handle[0]) if r.handle else 0,
                ),
            )
            aliases = [
                {"handle": r.handle, "name": r.name, "source": r.source, "confidence": r.confidence.value}
                for r in members
                if r is not canonical
            ]
            result.append(
                ConsolidationRecord(
                    canonical_handle=canonical.handle,
                    canonical_name=canonical.name,
                    chain=chain,
                    address=address,
                    aliases=aliases,
                    total_entries=len(members),
                )
            )
        return result


# ── Convenience entry points ───────────────────────────────────────


def link_all_cross_chain(records: list[WalletRecord] | None = None) -> list[CrossChainLink]:
    """Link every distinct KOL in the registry across chains."""
    linker = KolWalletLinker(records=records)
    handles = sorted({r.handle for r in linker.records if r.handle})
    links = [linker.link_cross_chain(h) for h in handles]
    return [link for link in links if link is not None]


def consolidate_registry(records: list[WalletRecord] | None = None) -> list[ConsolidationRecord]:
    """Consolidate duplicates across the registry (see :meth:`KolWalletLinker.consolidate`)."""
    return KolWalletLinker(records=records).consolidate()
