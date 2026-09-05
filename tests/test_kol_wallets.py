"""Unit tests for KOL wallet attribution (app/databus/kol_wallets.py).

Covers the deterministic, offline parts: address extraction/validation,
ENS namehash, the self-disclosed scan, the corroboration/confidence gate,
and registry loading. No network calls.
"""

from __future__ import annotations

import json

from app.databus.kol_wallets import (
    AttributionMethod,
    Confidence,
    KOLWalletAttributor,
    WalletLink,
    ens_namehash,
    extract_addresses,
    extract_ens_names,
    is_valid_evm,
    is_valid_solana,
    load_accounts,
    load_verified_kol_wallets,
)

VITALIK = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
SOL_MINT = "So11111111111111111111111111111111111111112"
TX_HASH = "0x" + "ab" * 32  # 64 hex — not an address


def test_is_valid_evm() -> None:
    assert is_valid_evm(VITALIK)
    assert is_valid_evm(VITALIK.lower())
    assert not is_valid_evm(TX_HASH)  # 64 hex chars, not 40
    assert not is_valid_evm("0x1234")


def test_is_valid_solana() -> None:
    assert is_valid_solana(SOL_MINT)
    assert not is_valid_solana("0x" + "ab" * 20)  # EVM-style
    # Hex-shaped strings (tx hashes) are rejected even if base58-valid.
    assert not is_valid_solana("abcd" * 10)


def test_extract_addresses_multi_chain() -> None:
    text = f"eth {VITALIK} and sol {SOL_MINT} and hash {TX_HASH}"
    found = extract_addresses(text)
    assert (VITALIK.lower(), "ethereum") in found
    assert (SOL_MINT, "solana") in found
    assert all(pair[0] != TX_HASH for pair in found)


def test_extract_ens_names() -> None:
    assert extract_ens_names("check out vitalik.eth and VITALIK.ETH") == ["vitalik.eth"]
    assert extract_ens_names("no names here") == []


def test_ens_namehash_known_vector() -> None:
    # EIP-137: namehash("") == 0x0...; namehash("eth") known vector.
    assert ens_namehash("").hex() == "00" * 32
    assert ens_namehash("eth").hex() == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    assert ens_namehash("vitalik.eth").hex() == "ee6c4522aab0003e8d14cd40a6af439055fd2577951148c14b6cea9a53475835"


def test_scan_self_disclosed_deterministic() -> None:
    attributor = KOLWalletAttributor()
    links = attributor._scan_self_disclosed(
        [f"founder wallet: {VITALIK}"],
        ["CA: 0x1234567890abcdef1234567890abcdef12345678"],
        "founder_test",
    )
    assert len(links) == 2
    assert all(link.confidence is Confidence.DETERMINISTIC for link in links)
    assert all(link.method is AttributionMethod.SELF_DISCLOSED for link in links)
    assert links[0].address == VITALIK.lower()
    assert "bio" in links[0].evidence
    assert "pinned post" in links[1].evidence


def test_finalize_probabilistic_needs_two_signals() -> None:
    attributor = KOLWalletAttributor()
    # One signal only → dropped (uncorroborated probabilistic).
    weak = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.AIRDROP_POAP_ENS,
        confidence=Confidence.PROBABILISTIC,
        evidence="ens:some.eth",
        signals=["ens_name_matches_handle"],
    )
    finalized = attributor._finalize([weak], "some")
    assert finalized == []

    # Second signal → corroborated → strong_heuristic (never deterministic).
    strong = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.PUBLIC_TAGGING,
        confidence=Confidence.STRONG_HEURISTIC,
        evidence="etherscan contains: Some",
        signals=["label_substring_handle_match"],
    )
    finalized = attributor._finalize([weak, strong], "some")
    assert len(finalized) == 1
    link = finalized[0]
    assert link.confidence is Confidence.STRONG_HEURISTIC
    assert link.confidence.value != "deterministic"
    assert "label_substring_handle_match" in link.signals


def test_finalize_deterministic_never_downgraded() -> None:
    attributor = KOLWalletAttributor()
    det = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.SELF_DISCLOSED,
        confidence=Confidence.DETERMINISTIC,
        evidence="bio",
        signals=["self_disclosed_in_profile"],
    )
    prob = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.AIRDROP_POAP_ENS,
        confidence=Confidence.PROBABILISTIC,
        evidence="ens:other.eth",
        signals=["farcaster_verified"],
    )
    finalized = attributor._finalize([det, prob], "vitalik")
    assert len(finalized) == 1
    assert finalized[0].confidence is Confidence.DETERMINISTIC


def test_load_accounts_registry() -> None:
    accounts = load_accounts()
    assert len(accounts) > 200
    handles = {a["handle"] for a in accounts}
    assert "VitalikButerin" in handles
    vitalik = next(a for a in accounts if a["handle"] == "VitalikButerin")
    assert vitalik["name"] == "Vitalik Buterin"
    assert vitalik["category"] == "founder"


def test_wallet_link_to_dict() -> None:
    link = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.SELF_DISCLOSED,
        confidence=Confidence.DETERMINISTIC,
        evidence="bio",
        signals=["self_disclosed_in_profile"],
    )
    payload = link.to_dict()
    assert set(payload) == {"address", "chain", "method", "confidence", "evidence", "signals"}
    assert json.dumps(payload)  # JSON-serializable


# ── Method 5: verified KOL list ────────────────────────────────────

VERIFIED_ENTRIES = [
    {
        "handle": "VitalikButerin",
        "address": VITALIK,
        "chain": "ethereum",
        "confidence": "deterministic",
        "source": "manual_review",
        "evidence_url": "https://example.com/vitalik-verified",
    },
    {
        "handle": "vitalikbuterin",
        "address": SOL_MINT,
        "chain": "solana",
        "confidence": "strong_heuristic",
        "source": "x_profile",
        "evidence_url": "https://example.com/vitalik-sol",
    },
    {
        "handle": "OtherKol",
        "address": "0x1111111111111111111111111111111111111111",
        "chain": "ethereum",
        "confidence": "deterministic",
        "source": "manual_review",
        "evidence_url": "https://example.com/other",
    },
]


def test_load_verified_kol_wallets(tmp_path) -> None:
    # Missing file → [] (the bundled verified_kol_wallets.json now exists,
    # so exercise the missing-file path with an explicit nonexistent path).
    assert load_verified_kol_wallets(str(tmp_path / "does-not-exist.json")) == []

    p = tmp_path / "verified_kol_wallets.json"
    p.write_text(json.dumps({"wallets": VERIFIED_ENTRIES}))
    loaded = load_verified_kol_wallets(str(p))
    assert len(loaded) == 3
    assert all(e["handle"] and e["address"] for e in loaded)

    # Bare-list top level also accepted.
    p2 = tmp_path / "bare.json"
    p2.write_text(json.dumps(VERIFIED_ENTRIES))
    assert len(load_verified_kol_wallets(str(p2))) == 3


def test_verified_list_maps_entries_to_links(tmp_path) -> None:
    p = tmp_path / "verified_kol_wallets.json"
    p.write_text(json.dumps(VERIFIED_ENTRIES))
    attributor = KOLWalletAttributor()
    attributor._verified_list_path = str(p)

    links = attributor._verified_list("VitalikButerin")
    by_chain = {link.chain: link for link in links}
    assert set(by_chain) == {"ethereum", "solana"}
    assert by_chain["ethereum"].address == VITALIK.lower()
    assert by_chain["ethereum"].confidence is Confidence.DETERMINISTIC
    assert by_chain["ethereum"].method is AttributionMethod.VERIFIED_LIST
    assert "example.com/vitalik-verified" in by_chain["ethereum"].evidence
    assert by_chain["solana"].confidence is Confidence.STRONG_HEURISTIC
    # Non-matching handle excluded.
    assert len(attributor._verified_list("SomeoneElse")) == 0


def test_verified_list_deterministic_passes_immediately() -> None:
    attributor = KOLWalletAttributor()
    link = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.VERIFIED_LIST,
        confidence=Confidence.DETERMINISTIC,
        evidence="verified_list[manual_review] https://example.com/v",
        signals=["verified_list_entry"],
    )
    finalized = attributor._finalize([link], "vitalik")
    assert len(finalized) == 1
    assert finalized[0].confidence is Confidence.DETERMINISTIC


def test_verified_list_strong_passes_immediately() -> None:
    attributor = KOLWalletAttributor()
    link = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.VERIFIED_LIST,
        confidence=Confidence.STRONG_HEURISTIC,
        evidence="verified_list[x_profile] https://example.com/v",
        signals=["verified_list_entry"],
    )
    finalized = attributor._finalize([link], "vitalik")
    assert len(finalized) == 1
    assert finalized[0].confidence is Confidence.STRONG_HEURISTIC


def test_verified_list_probabilistic_still_needs_two_signals() -> None:
    attributor = KOLWalletAttributor()
    weak = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.VERIFIED_LIST,
        confidence=Confidence.PROBABILISTIC,
        evidence="verified_list[heuristic] https://example.com/v",
        signals=["verified_list_entry"],
    )
    # Single verified-list signal alone → dropped (uncorroborated).
    assert attributor._finalize([weak], "vitalik") == []

    # Corroborated by another probabilistic signal → strong_heuristic.
    other = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.AIRDROP_POAP_ENS,
        confidence=Confidence.PROBABILISTIC,
        evidence="ens:some.eth",
        signals=["farcaster_verified"],
    )
    finalized = attributor._finalize([weak, other], "vitalik")
    assert len(finalized) == 1
    assert finalized[0].confidence is Confidence.STRONG_HEURISTIC
    assert "verified_list_entry" in finalized[0].signals


def test_verified_list_entry_corroborates_other_probabilistic() -> None:
    attributor = KOLWalletAttributor()
    ens_only = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.AIRDROP_POAP_ENS,
        confidence=Confidence.PROBABILISTIC,
        evidence="ens:vitalik.eth",
        signals=["ens_name_matches_handle"],
    )
    verified = WalletLink(
        address=VITALIK.lower(),
        chain="ethereum",
        method=AttributionMethod.VERIFIED_LIST,
        confidence=Confidence.PROBABILISTIC,
        evidence="verified_list[heuristic] https://example.com/v",
        signals=["verified_list_entry"],
    )
    finalized = attributor._finalize([ens_only, verified], "vitalik")
    assert len(finalized) == 1
    assert finalized[0].confidence is Confidence.STRONG_HEURISTIC
    assert "verified_list_entry" in finalized[0].signals
