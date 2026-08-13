"""Unit tests for KOL wallet cross-chain linking + funding-source detection.

Offline/deterministic only — no network calls. Covers:
- cross-chain linkage via registry both-chains, shared ENS/SNS handle,
  and shared funding source
- funding-source signal detection + confidence escalation
- registry consolidation/dedupe (same address under multiple names)
"""

from __future__ import annotations

import pytest

from app.databus.kol_wallet_linker import (
    ConsolidationRecord,
    CrossChainLink,
    FundingSignal,
    KolWalletLinker,
    WalletRecord,
    consolidate_registry,
    link_all_cross_chain,
    load_known_funders,
    normalize_handle,
)
from app.databus.kol_wallets import AttributionMethod, Confidence, WalletLink

VITALIK_EVM = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
SOL_MINT = "So11111111111111111111111111111111111111112"
RUG_FUNDER_EVM = "0x1111111111111111111111111111111111111111"
RUG_FUNDER_SOL = "Deployer1111111111111111111111111111111111111"


def _record(
    handle: str,
    chain: str,
    address: str,
    name: str = "",
    confidence: Confidence = Confidence.STRONG_HEURISTIC,
    source: str = "test",
    verified: bool = False,
    funded_by: str | None = None,
) -> WalletRecord:
    return WalletRecord(
        handle=normalize_handle(handle),
        name=name,
        chain=chain,
        address=address.lower(),
        confidence=confidence,
        source=source,
        verified=verified,
        funded_by=funded_by,
    )


# ── Cross-chain linkage ────────────────────────────────────────────


def test_registry_both_chains_links_dual_chain_kol() -> None:
    # Known dual-chain KOL: both wallets listed in the verified registry.
    records = [
        _record("assasin", "solana", SOL_MINT, name="Assasin.eth", verified=True),
        _record("assasin", "ethereum", VITALIK_EVM, name="Assasin.eth", verified=True),
    ]
    linker = KolWalletLinker(records=records)
    link = linker.link_cross_chain("assasin")

    assert link is not None
    assert link.kol_id == "assasin"
    assert link.confidence is Confidence.DETERMINISTIC
    assert "registry_both_chains" in link.methods
    assert len(link.addresses) == 2
    chains = {a["chain"] for a in link.addresses}
    assert chains == {"solana", "ethereum"}


def test_shared_ens_sns_handle_links_across_chains() -> None:
    # Solana record carries "Assasin.eth" name; EVM record uses handle
    # `assasin.eth` — both share the strong token `assasin`.
    records = [
        _record("assasin", "solana", SOL_MINT, name="Assasin.eth"),
        _record("assasin.eth", "ethereum", VITALIK_EVM, name="Assasin"),
    ]
    linker = KolWalletLinker(records=records)
    link = linker.link_cross_chain("assasin")

    assert link is not None
    assert "shared_handle" in link.methods
    assert link.confidence is Confidence.STRONG_HEURISTIC
    assert len(link.addresses) == 2


def test_shared_funding_links_across_chains() -> None:
    # Different handles, no ENS/SNS token — only the shared funder links
    # the solana + ethereum wallets to the same identity.
    records = [
        _record("dualkol_sol", "solana", SOL_MINT, funded_by=RUG_FUNDER_SOL),
        _record("dualkol_eth", "ethereum", VITALIK_EVM, funded_by=RUG_FUNDER_SOL),
    ]
    linker = KolWalletLinker(records=records)
    link = linker.link_cross_chain("dualkol_sol")

    assert link is not None
    assert "shared_funding" in link.methods
    assert "both chains funded by" in link.evidence[0]
    assert len(link.addresses) == 2


def test_unrelated_names_do_not_false_merge() -> None:
    # Plain-name words are NOT strong tokens: two KOLs both named
    # "Crypto King" must not be merged.
    records = [
        _record("kolA", "solana", SOL_MINT, name="Crypto King"),
        _record("kolB", "ethereum", VITALIK_EVM, name="Crypto King"),
    ]
    linker = KolWalletLinker(records=records)
    assert linker.link_cross_chain("kolA") is not None
    link_a = linker.link_cross_chain("kolA")
    assert link_a is not None
    assert len(link_a.addresses) == 1  # only kolA's solana wallet


def test_single_chain_link_is_trivial() -> None:
    records = [_record("solo", "solana", SOL_MINT)]
    linker = KolWalletLinker(records=records)
    link = linker.link_cross_chain("solo")
    assert link is not None
    assert link.methods == ["single_chain"]
    assert len(link.addresses) == 1


def test_link_all_cross_chain_dedupes() -> None:
    records = [
        _record("assasin", "solana", SOL_MINT),
        _record("assasin", "ethereum", VITALIK_EVM),
        _record("solo", "solana", SOL_MINT),
    ]
    links = link_all_cross_chain(records)
    by_id = {link.kol_id: link for link in links}
    assert "assasin" in by_id
    assert "solo" in by_id
    assert len(by_id["assasin"].addresses) == 2


# ── Funding-source signal ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_funding_signal_registry_evidence() -> None:
    records = [
        _record("shill", "ethereum", VITALIK_EVM, funded_by=RUG_FUNDER_EVM),
    ]
    linker = KolWalletLinker(records=records, known_funders={RUG_FUNDER_EVM: "rug_puller deployer"})
    signal = await linker.detect_funding_signal("shill", VITALIK_EVM, "ethereum")

    assert signal is not None
    assert signal.funder_address == RUG_FUNDER_EVM
    assert signal.funder_label == "rug_puller deployer"
    assert signal.method == "funded_by_registry"
    assert signal.escalated_confidence > signal.shill_confidence


@pytest.mark.asyncio
async def test_funding_signal_provider_lookup() -> None:
    async def fake_provider(address: str, chain: str) -> list[str]:
        return [RUG_FUNDER_SOL]

    records = [_record("shill", "solana", SOL_MINT)]
    linker = KolWalletLinker(
        records=records, known_funders={RUG_FUNDER_SOL: "known deployer"}, funding_provider=fake_provider
    )
    signal = await linker.detect_funding_signal("shill", SOL_MINT, "solana")

    assert signal is not None
    assert signal.method == "funded_by_provider"
    assert signal.funder_label == "known deployer"


@pytest.mark.asyncio
async def test_funding_signal_none_when_no_known_funder() -> None:
    linker = KolWalletLinker(records=[_record("clean", "solana", SOL_MINT)], known_funders={})
    signal = await linker.detect_funding_signal("clean", SOL_MINT, "solana")
    assert signal is None


def test_funding_signal_escalate_caps() -> None:
    signal = FundingSignal(
        kol_id="shill",
        wallet_address=VITALIK_EVM,
        chain="ethereum",
        funder_address=RUG_FUNDER_EVM,
        funder_label="rug",
        method="funded_by_registry",
        shill_confidence=0.95,
        escalated_confidence=0.95,
    )
    signal.escalate(amount=0.15, cap=0.99)
    assert signal.escalated_confidence == 0.99


def test_escalate_wallet_confidence() -> None:
    linker = KolWalletLinker()
    weak = WalletLink(
        address=VITALIK_EVM,
        chain="ethereum",
        method=AttributionMethod.AIRDROP_POAP_ENS,
        confidence=Confidence.PROBABILISTIC,
        evidence="ens:shill.eth",
        signals=["ens_name_matches_handle"],
    )
    signal = FundingSignal(
        kol_id="shill",
        wallet_address=VITALIK_EVM,
        chain="ethereum",
        funder_address=RUG_FUNDER_EVM,
        funder_label="rug_puller deployer",
        method="funded_by_registry",
    )
    upgraded = linker.escalate_wallet_confidence(weak, signal)
    assert upgraded.confidence is Confidence.STRONG_HEURISTIC
    assert "funded_by_known_deployer" in upgraded.signals


def test_escalate_wallet_confidence_never_downgrades() -> None:
    linker = KolWalletLinker()
    det = WalletLink(
        address=VITALIK_EVM,
        chain="ethereum",
        method=AttributionMethod.AIRDROP_POAP_ENS,
        confidence=Confidence.DETERMINISTIC,
        evidence="bio",
        signals=["self_disclosed_in_profile"],
    )
    signal = FundingSignal(
        kol_id="shill",
        wallet_address=VITALIK_EVM,
        chain="ethereum",
        funder_address=RUG_FUNDER_EVM,
        funder_label="rug",
        method="funded_by_registry",
    )
    upgraded = linker.escalate_wallet_confidence(det, signal)
    assert upgraded.confidence is Confidence.DETERMINISTIC


# ── Consolidation / dedupe ─────────────────────────────────────────


def test_consolidate_same_address_multiple_names() -> None:
    records = [
        _record("dupe1", "ethereum", VITALIK_EVM, name="Alias One", confidence=Confidence.PROBABILISTIC),
        _record("dupe2", "ethereum", VITALIK_EVM, name="Canonical", confidence=Confidence.DETERMINISTIC, verified=True),
        _record("dupe3", "ethereum", VITALIK_EVM, name="Alias Three", confidence=Confidence.STRONG_HEURISTIC),
        _record("solo", "solana", SOL_MINT),
    ]
    linker = KolWalletLinker(records=records)
    consolidations = linker.consolidate()

    assert len(consolidations) == 1
    rec = consolidations[0]
    assert rec.canonical_handle == "dupe2"
    assert rec.address == VITALIK_EVM.lower()
    assert rec.total_entries == 3
    assert len(rec.aliases) == 2
    assert {a["handle"] for a in rec.aliases} == {"dupe1", "dupe3"}


def test_consolidate_registry_no_duplicates() -> None:
    records = [
        _record("a", "solana", SOL_MINT),
        _record("b", "ethereum", VITALIK_EVM),
    ]
    assert consolidate_registry(records) == []


def test_consolidation_record_to_dict() -> None:
    rec = ConsolidationRecord(
        canonical_handle="dupe2",
        canonical_name="Canonical",
        chain="ethereum",
        address=VITALIK_EVM.lower(),
        aliases=[{"handle": "dupe1", "name": "Alias One", "source": "test", "confidence": "probabilistic"}],
        total_entries=2,
    )
    payload = rec.to_dict()
    assert payload["canonical_handle"] == "dupe2"
    assert payload["total_entries"] == 2


def test_cross_chain_link_to_dict() -> None:
    link = CrossChainLink(
        kol_id="assasin",
        addresses=[{"address": SOL_MINT, "chain": "solana", "handle": "assasin"}],
        methods=["single_chain"],
        confidence=Confidence.STRONG_HEURISTIC,
        evidence=["x"],
    )
    payload = link.to_dict()
    assert payload["kol_id"] == "assasin"
    assert payload["methods"] == ["single_chain"]


# ── Known funders loader / normalization ───────────────────────────


def test_load_known_funders_missing_file(tmp_path) -> None:
    assert load_known_funders(str(tmp_path / "nope.json")) == {}


def test_load_known_funders_shape(tmp_path) -> None:
    p = tmp_path / "known_funders.json"
    p.write_text('{"funders": {"0xABC": "rug_puller deployer"}}')
    loaded = load_known_funders(str(p))
    assert loaded == {"0xabc": "rug_puller deployer"}


def test_normalize_handle() -> None:
    assert normalize_handle("@VitalikButerin") == "vitalikbuterin"
    assert normalize_handle("  assasin  ") == "assasin"
    assert normalize_handle(None) == ""
