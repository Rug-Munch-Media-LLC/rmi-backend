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
