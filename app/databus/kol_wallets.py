"""KOL wallet attribution — docs/KOL-TRACKING-DESIGN.md §2.

Finds wallet addresses attributable to a KOL (X account) using, in order
of expected strength:

  1. self_disclosed      — addresses in the account's bio / pinned posts
                           (deterministic when the author == handle)
  2. ens_lens_farcaster  — ENS text records (com.twitter == handle),
                           Lens / Farcaster profile resolution
  3. public_tagging      — authoritative labels (Etherscan, RolodETH,
                           walletLabels.xyz, bluepages, eth-labels-db,
                           Arkham / Lookonchain when keyed) matching the
                           handle / name
  4. airdrop_poap_ens    — probabilistic evidence; NEVER auto-escalated,
                          requires >= 2 corroborating signals to be
                          stored as active (else status=unverified).
  5. verified_list       — pre-verified wallet links from
                          verified_kol_wallets.json (compiled out-of-band).
                          Entries carry an authoritative confidence tier
                          (deterministic / strong_heuristic pass the
                          corroboration gate immediately; probabilistic
                          still requires >= 2 signals) plus source and
                          evidence_url for auditability.

Each link carries {address, chain, method, evidence, confidence} and is
persisted to the `kol_wallet` Postgres table plus mirrored into the local
label store (source="kol_attribution") so downstream wallet_memory /
scanner code can consume the attribution without a new dependency.

Confidence tiers map 1:1 to the label store:
    deterministic > strong_heuristic > probabilistic > crowdsourced_unverified
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus._kol_wallets_resolvers import (
    _arkham_labels,
    _lookonchain_labels,
    ens_namehash,
    ens_text_record,
    extract_addresses,
    extract_ens_names,
    is_valid_evm,
    is_valid_solana,
    resolve_ens_name,
    resolve_farcaster,
    resolve_lens,
)

logger = get_logger("databus.kol_wallets")

# Re-export the resolver helpers (mypy strict requires explicit __all__).
__all__ = [
    "ens_namehash",
    "extract_addresses",
    "extract_ens_names",
    "is_valid_evm",
    "resolve_ens_name",
    "resolve_farcaster",
    "resolve_lens",
]

# ── Enums / data model ─────────────────────────────────────────────


class Confidence(StrEnum):
    DETERMINISTIC = "deterministic"
    STRONG_HEURISTIC = "strong_heuristic"
    PROBABILISTIC = "probabilistic"
    CROWDSOURCED_UNVERIFIED = "crowdsourced_unverified"


class AttributionMethod(StrEnum):
    SELF_DISCLOSED = "self_disclosed"
    PUBLIC_TAGGING = "public_tagging"
    AIRDROP_POAP_ENS = "airdrop_poap_ens"
    ONCHAIN_ACTIVITY = "onchain_activity"
    MANUAL = "manual"
    VERIFIED_LIST = "verified_list"


@dataclass(slots=True)
class WalletLink:
    """A single KOL -> wallet attribution link."""

    address: str
    chain: str
    method: AttributionMethod
    confidence: Confidence
    evidence: str
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "chain": self.chain,
            "method": self.method.value,
            "confidence": self.confidence.value,
            "evidence": self.evidence,
            "signals": list(self.signals),
        }


# ── The attributor ─────────────────────────────────────────────────

#: Signal names used by the corroboration gate (>=2 for probabilistic).
_SIG_SELF_DISCLOSED = "self_disclosed_in_profile"
_SIG_ENS_HANDLE = "ens_name_matches_handle"
_SIG_ENS_TEXT = "ens_text_matches_handle"
_SIG_LABEL_EXACT = "label_exact_name_match"
_SIG_LABEL_SUBSTR = "label_substring_handle_match"
_SIG_FARCASTER = "farcaster_verified"
_SIG_LENS = "lens_owned"
_SIG_AIRDROP = "airdrop_poap_claim_label"
_SIG_VERIFIED_LIST = "verified_list_entry"


class KOLWalletAttributor:
    """Attribute wallet addresses to a KOL across all §2 methods."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=15.0)
        # Overridable for tests; None = default app/databus/verified_kol_wallets.json.
        self._verified_list_path: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    # ── Public entry point ────────────────────────────────────────

    async def attribute(
        self,
        handle: str,
        name: str,
        bio: str | None = None,
        pinned_posts: list[str] | None = None,
        extra_text: list[str] | None = None,
    ) -> list[WalletLink]:
        """Run all attribution methods for one KOL and return links.

        Args:
            handle: X handle without '@'.
            name: Display name (used for label matching).
            bio: X profile description text (self-disclosure source).
            pinned_posts: Pinned post texts (self-disclosure source).
            extra_text: Optional extra sources (website / Linktree text).
        """
        handle = handle.lstrip("@")
        name = name.strip()
        texts = [bio] if bio else []
        pinned = pinned_posts or []
        extras = extra_text or []
        texts.extend(extras)

        links: list[WalletLink] = []

        # Method 1: self-disclosed addresses in bio / pinned posts.
        links.extend(self._scan_self_disclosed(texts, pinned, handle))

        # Method 2: ENS / Lens / Farcaster.
        links.extend(await self._ens_lens_farcaster(texts, handle, name))

        # Method 3: public-tag lookup on every candidate address.
        candidates = {link.address for link in links}
        links.extend(await self._public_tagging(candidates, handle, name))

        # Method 4: airdrop / POAP / ENS probabilistic evidence.
        links.extend(await self._airdrop_poap_evidence(candidates, handle, name))

        # Method 5: pre-verified KOL list (compiled concurrently; no-op if absent).
        links.extend(self._verified_list(handle))

        # Corroboration gate: merge + rank; probabilistic needs 2 signals.
        return self._finalize(links, handle)

    # ── Method 1: self-disclosed ──────────────────────────────────

    def _scan_self_disclosed(
        self,
        bios: list[str],
        pinned_posts: list[str],
        handle: str,
    ) -> list[WalletLink]:
        links: list[WalletLink] = []
        for bio in bios:
            for address, chain in extract_addresses(bio):
                links.append(
                    WalletLink(
                        address=address,
                        chain=chain,
                        method=AttributionMethod.SELF_DISCLOSED,
                        confidence=Confidence.DETERMINISTIC,
                        evidence=f"bio of @{handle} ({chain})",
                        signals=[_SIG_SELF_DISCLOSED],
                    )
                )
        for post in pinned_posts:
            for address, chain in extract_addresses(post):
                links.append(
                    WalletLink(
                        address=address,
                        chain=chain,
                        method=AttributionMethod.SELF_DISCLOSED,
                        confidence=Confidence.DETERMINISTIC,
                        evidence=f"pinned post of @{handle} ({chain})",
                        signals=[_SIG_SELF_DISCLOSED],
                    )
                )
        return links

    # ── Method 2: ENS / Lens / Farcaster ──────────────────────────

    async def _ens_lens_farcaster(
        self,
        texts: list[str],
        handle: str,
        name: str,
    ) -> list[WalletLink]:
        links: list[WalletLink] = []
        ens_names: list[str] = []
        for text in texts:
            ens_names.extend(extract_ens_names(text))
        # Handle-as-ENS is also a candidate (many crypto handles == ENS).
        ens_names.append(f"{handle.lower()}.eth")
        ens_names = list(dict.fromkeys(ens_names))

        name_tokens = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}

        for ens_name in ens_names:
            address = await resolve_ens_name(self._client, ens_name)
            if not address:
                continue
            signals: list[str] = []
            evidence = f"ens:{ens_name}"
            if ens_name.rstrip(".eth") == handle.lower():
                signals.append(_SIG_ENS_HANDLE)
            if name_tokens and ens_name.rstrip(".eth") in name_tokens:
                signals.append(_SIG_ENS_HANDLE)
            twitter = await ens_text_record(self._client, ens_name, "com.twitter")
            if twitter and twitter.lower() == handle.lower():
                signals.append(_SIG_ENS_TEXT)
                evidence += f" com.twitter={twitter}"
            confidence = Confidence.STRONG_HEURISTIC
            if not signals:
                # ENS name unrelated to handle/name — self-asserted but
                # unverified → probabilistic until corroborated.
                confidence = Confidence.PROBABILISTIC
            links.append(
                WalletLink(
                    address=address,
                    chain="ethereum",
                    method=AttributionMethod.AIRDROP_POAP_ENS,
                    confidence=confidence,
                    evidence=evidence,
                    signals=signals,
                )
            )

        # Farcaster verifications + Lens (best-effort public APIs).
        for fc in await resolve_farcaster(self._client, handle):
            links.append(
                WalletLink(
                    address=fc["address"],
                    chain="ethereum",
                    method=AttributionMethod.AIRDROP_POAP_ENS,
                    confidence=Confidence.PROBABILISTIC,
                    evidence=fc["evidence"],
                    signals=[_SIG_FARCASTER],
                )
            )
        for lens in await resolve_lens(self._client, handle):
            links.append(
                WalletLink(
                    address=lens["address"],
                    chain="ethereum",
                    method=AttributionMethod.AIRDROP_POAP_ENS,
                    confidence=Confidence.PROBABILISTIC,
                    evidence=lens["evidence"],
                    signals=[_SIG_LENS],
                )
            )
        return links

    # ── Method 3: public tagging ──────────────────────────────────

    async def _public_tagging(
        self,
        candidates: set[str],
        handle: str,
        name: str,
    ) -> list[WalletLink]:
        links: list[WalletLink] = []
        if not candidates:
            return links
        handle_l = handle.lower()
        name_l = name.lower()
        name_tokens = {t for t in re.split(r"[^a-z0-9]+", name_l) if t and len(t) > 2}

        for address in sorted(candidates):
            labels: list[dict[str, Any]] = []
            try:
                from app.wallet_memory.labels import get_label_service

                resolved = await get_label_service().resolve(address, "ethereum")
                if resolved:
                    labels.extend(resolved.get("labels", []) or [])
            except (ImportError, RuntimeError):
                pass
            labels.extend(await _arkham_labels(self._client, address))
            labels.extend(await _lookonchain_labels(self._client, address))

            for lbl in labels:
                label = str(lbl.get("label", "")).strip()
                if not label:
                    continue
                label_l = label.lower()
                if label_l == name_l or (name_tokens and label_l in name_tokens):
                    links.append(
                        WalletLink(
                            address=address,
                            chain="ethereum",
                            method=AttributionMethod.PUBLIC_TAGGING,
                            confidence=Confidence.DETERMINISTIC,
                            evidence=f"{lbl.get('source', 'label')} exact: {label}",
                            signals=[_SIG_LABEL_EXACT],
                        )
                    )
                elif handle_l in label_l or any(tok in label_l for tok in name_tokens):
                    links.append(
                        WalletLink(
                            address=address,
                            chain="ethereum",
                            method=AttributionMethod.PUBLIC_TAGGING,
                            confidence=Confidence.STRONG_HEURISTIC,
                            evidence=f"{lbl.get('source', 'label')} contains: {label}",
                            signals=[_SIG_LABEL_SUBSTR],
                        )
                    )
        return links

    # ── Method 4: airdrop / POAP / ENS probabilistic evidence ─────

    async def _airdrop_poap_evidence(
        self,
        candidates: set[str],
        handle: str,
        name: str,
    ) -> list[WalletLink]:
        """Corroborating signals from airdrop / POAP / claim labels.

        Probabilistic on its own; combined with another signal the link
        passes the 2-signal gate in _finalize().
        """
        links: list[WalletLink] = []
        if not candidates:
            return links
        for address in sorted(candidates):
            try:
                from app.wallet_memory.labels import get_label_service

                resolved = await get_label_service().resolve(address, "ethereum")
                if resolved:
                    for lbl in resolved.get("labels", []) or []:
                        cat = str(lbl.get("category", "")).lower()
                        if any(k in cat for k in ("airdrop", "poap", "claim")):
                            links.append(
                                WalletLink(
                                    address=address,
                                    chain="ethereum",
                                    method=AttributionMethod.AIRDROP_POAP_ENS,
                                    confidence=Confidence.PROBABILISTIC,
                                    evidence=f"{lbl.get('source', 'label')} airdrop/claim: {lbl.get('label', '')}",
                                    signals=[_SIG_AIRDROP],
                                )
                            )
            except (ImportError, RuntimeError):
                pass
        return links

    # ── Method 5: verified KOL list ───────────────────────────────

    def _verified_list(self, handle: str) -> list[WalletLink]:
        """Method 5: pre-verified wallet links from verified_kol_wallets.json.

        Each entry ({handle, address, chain, confidence, source,
        evidence_url}) becomes a single-signal link whose authoritative
        confidence tier drives the corroboration gate in _finalize():

          - deterministic / strong_heuristic → passes immediately (the
            entry is already corroborated by the out-of-band verification).
          - probabilistic → still requires >= 2 signals; the entry's
            verified_list_entry signal can corroborate another
            probabilistic link on the same (address, chain).

        Missing file (list still being compiled) → no-op.
        """
        handle_l = handle.lstrip("@").lower()
        links: list[WalletLink] = []
        for entry in load_verified_kol_wallets(self._verified_list_path):
            if str(entry.get("handle", "")).lstrip("@").lower() != handle_l:
                continue
            raw_chain = str(entry.get("chain", "")).strip().lower()
            address = str(entry.get("address", "")).strip()
            if not raw_chain:
                logger.warning("verified list entry missing chain for %s", entry.get("handle"))
                continue
            if "solana" in raw_chain:
                if not is_valid_solana(address):
                    logger.warning("verified list entry invalid solana address: %s", address)
                    continue
            else:
                if not is_valid_evm(address):
                    logger.warning("verified list entry invalid EVM address: %s", address)
                    continue
                address = address.lower()
            try:
                confidence = Confidence(str(entry.get("confidence", "probabilistic")).lower())
            except ValueError:
                logger.warning(
                    "verified list entry unknown confidence %r for %s; skipping",
                    entry.get("confidence"),
                    entry.get("handle"),
                )
                continue
            source = str(entry.get("source", "verified_list")).strip()
            evidence_url = str(entry.get("evidence_url", "")).strip()
            evidence = f"verified_list[{source}]"
            if evidence_url:
                evidence += f" {evidence_url}"
            links.append(
                WalletLink(
                    address=address,
                    chain=raw_chain,
                    method=AttributionMethod.VERIFIED_LIST,
                    confidence=confidence,
                    evidence=evidence,
                    signals=[_SIG_VERIFIED_LIST],
                )
            )
        return links

    # ── Corroboration / dedupe / ranking ──────────────────────────

    def _finalize(self, links: list[WalletLink], handle: str) -> list[WalletLink]:
        """Merge signals per (address, chain), apply confidence rules.

        Rules (KOL-TRACKING-DESIGN.md §2.4):
          - probabilistic links need >= 2 corroborating signals, else the
            link is dropped from active attribution (caller may store as
            status=unverified).
          - probabilistic never auto-escalates to deterministic; with 2+
            corroborating signals it may rise to strong_heuristic.
          - verified_list links carry an authoritative tier: deterministic
            and strong_heuristic pass immediately (single signal is
            enough); probabilistic still needs >= 2 signals. Signal
            merging across (address, chain) lets a verified_list_entry
            corroborate other probabilistic links on the same address.
        """
        merged: dict[tuple[str, str], WalletLink] = {}
        for link in links:
            key = (link.address, link.chain)
            existing = merged.get(key)
            if existing is None:
                merged[key] = link
                continue
            existing.signals = list(dict.fromkeys(existing.signals + link.signals))
            # Best evidence wins; never downgrade a deterministic link.
            if _confidence_rank(link.confidence) > _confidence_rank(existing.confidence):
                existing.confidence = link.confidence
                existing.method = link.method
                existing.evidence = f"{existing.evidence} | {link.evidence}"

        finalized: list[WalletLink] = []
        for link in merged.values():
            if link.confidence is Confidence.PROBABILISTIC:
                if len(link.signals) >= 2:
                    link.confidence = Confidence.STRONG_HEURISTIC
                else:
                    # Uncorroborated probabilistic — persist only as
                    # unverified (caller decides); drop from active.
                    continue
            finalized.append(link)
        finalized.sort(key=lambda link: (_confidence_rank(link.confidence), link.address))
        return finalized

    # ── Persistence ───────────────────────────────────────────────

    async def persist(
        self,
        session: Any,
        links: list[WalletLink],
        kol_id: str,
        status_override: str | None = None,
    ) -> dict[str, Any]:
        """Upsert links into `kol_wallet` + mirror into local label store.

        Returns {"written": int, "label_mirrored": int}.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.models.kol import KOLWallet
        from app.models.label import Label

        now = datetime.now(UTC)
        written = 0
        for link in links:
            status = status_override or ("active" if link.confidence is not Confidence.PROBABILISTIC else "unverified")
            stmt = pg_insert(KOLWallet).values(
                kol_id=kol_id,
                chain=link.chain,
                address=link.address,
                attribution_method=link.method.value,
                confidence=link.confidence.value,
                evidence=link.evidence,
                status=status,
                first_seen_at=now,
                last_verified_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_kol_wallet_kol_chain_addr",
                set_={
                    "attribution_method": stmt.excluded.attribution_method,
                    "confidence": stmt.excluded.confidence,
                    "evidence": stmt.excluded.evidence,
                    "status": stmt.excluded.status,
                    "last_verified_at": now,
                },
            )
            await session.execute(stmt)
            written += 1

        # Mirror into local label store (Postgres labels table) —
        # idempotent via the labels unique constraint.
        label_mirrored = 0
        for link in links:
            lstmt = pg_insert(Label).values(
                address=link.address,
                chain=link.chain,
                label_type="person",
                label_value=f"@{kol_id}",
                source="kol_attribution",
                confidence=_confidence_float(link.confidence),
                attributes={
                    "attribution_method": link.method.value,
                    "evidence": link.evidence,
                    "signals": link.signals,
                },
                verified_at=now,
            )
            lstmt = lstmt.on_conflict_do_nothing(constraint="uq_label")
            result = await session.execute(lstmt)
            if result.rowcount and result.rowcount > 0:
                label_mirrored += 1

        return {"written": written, "label_mirrored": label_mirrored}


def _confidence_rank(confidence: Confidence) -> int:
    """Rank confidence tiers for ordering/merging (higher = stronger)."""
    return {"deterministic": 4, "strong_heuristic": 3, "probabilistic": 2, "crowdsourced_unverified": 1}[
        confidence.value
    ]


def _confidence_float(confidence: Confidence) -> float:
    """Map a confidence tier to a 0..1 float for the label store."""
    return {"deterministic": 0.95, "strong_heuristic": 0.8, "probabilistic": 0.5, "crowdsourced_unverified": 0.3}[
        confidence.value
    ]


# ── Convenience loader for the KOL registry ────────────────────────


def load_accounts(path: str | None = None) -> list[dict[str, Any]]:
    """Load the KOL registry from app/databus/influential_accounts.json."""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "influential_accounts.json")
    with open(path) as f:
        data = json.load(f)
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    return [a for a in accounts if isinstance(a, dict) and a.get("handle")]


def load_verified_kol_wallets(path: str | None = None) -> list[dict[str, Any]]:
    """Load the verified KOL wallet list (app/databus/verified_kol_wallets.json).

    Expected entry shape: {handle, address, chain, confidence, source,
    evidence_url}. The file is compiled concurrently with this code, so a
    missing file is tolerated and returns []. The top level may be a bare
    list or a dict with a "wallets" / "verified" list.
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "verified_kol_wallets.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("wallets", data.get("verified", []))
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("handle") and e.get("address")]


# ── Dry run / CLI ──────────────────────────────────────────────────


async def dry_run(
    handle: str, name: str, bio: str | None = None, pinned_posts: list[str] | None = None
) -> list[WalletLink]:
    """Attribution-only run (no persistence) for one account."""
    attributor = KOLWalletAttributor()
    try:
        return await attributor.attribute(handle=handle, name=name, bio=bio, pinned_posts=pinned_posts)
    finally:
        await attributor.close()


async def _main() -> None:
    """CLI dry run. Example:

        python -m app.databus.kol_wallets VitalikButerin "Vitalik Buterin" \
            "Ethereum creator. vitalik.eth"
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="KOL wallet attribution dry run")
    parser.add_argument("handle", help="X handle without @")
    parser.add_argument("name", help="Display name")
    parser.add_argument("--bio", default=None, help="Bio text (else default fixture for VitalikButerin)")
    parser.add_argument("--pinned", action="append", default=[], help="Pinned post text (repeatable)")
    args = parser.parse_args()

    bio = args.bio
    if bio is None and args.handle.lower() == "vitalikbuterin":
        bio = "Ethereum creator. vitalik.eth"
    links = await dry_run(args.handle, args.name, bio=bio, pinned_posts=args.pinned)
    if not links:
        print("No attribution links found.")
        return
    for link in links:
        print(json.dumps(link.to_dict(), indent=2))
    sys.exit(0)


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
