"""Pure unit tests for the L1 self-disclosure attribution layer
(app/databus/kol_attribution_l1.py).

No network, no DB: EIP-137 namehash vector, fake-resolver ownership
proofs, structural signed-message validation, bio extraction with chain
inference, and L1 merge/dedupe. The live RPC paths are never exercised —
every resolver is injected as a mapping.
"""

from __future__ import annotations

from typing import Any

from app.databus.kol_attribution_l1 import (
    attribution_from_bio,
    merge_l1,
    prove_ownership,
    resolve_cns_owner,
    resolve_ens_owner,
)
from app.databus.kol_wallets import ens_namehash

VITALIK = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
SOL_MINT = "So11111111111111111111111111111111111111112"

# Fake resolver: {name: owner_address} — no network needed.
FAKE_RESOLVER = {
    "vitalik.eth": VITALIK,
    "alice.bnb": ALICE,
}

# 65-byte ECDSA (r||s||v) and 64-byte (r||s) hex signatures.
SIG_65 = "0x" + "ab" * 64 + "1b"
SIG_64 = "0x" + "cd" * 64


# ── Namehash (EIP-137) vector ──────────────────────────────────────


def test_ens_namehash_published_vector() -> None:
    # EIP-137 published vector: namehash("eth")
    assert ens_namehash("eth").hex() == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    assert ens_namehash("").hex() == "00" * 32
    # Deterministic, order-insensitive to trailing dot.
    assert ens_namehash("vitalik.eth") == ens_namehash("vitalik.eth.")
    # EIP-137 hashes raw label bytes: the primitive is case-sensitive;
    # lowercase normalization is the caller's job (done in the module).
    assert ens_namehash("VITALIK.ETH") != ens_namehash("vitalik.eth")


# ── Fake-resolver ownership resolution ─────────────────────────────


async def test_resolve_ens_owner_fake_resolver() -> None:
    assert await resolve_ens_owner("vitalik.eth", resolver=FAKE_RESOLVER) == VITALIK.lower()
    # Case-insensitive lookup on the injected mapping.
    assert await resolve_ens_owner("VITALIK.ETH", resolver=FAKE_RESOLVER) == VITALIK.lower()
    assert await resolve_ens_owner("nobody.eth", resolver=FAKE_RESOLVER) is None


async def test_resolve_cns_owner_fake_resolver() -> None:
    assert await resolve_cns_owner("alice.bnb", resolver=FAKE_RESOLVER) == ALICE.lower()
    assert await resolve_cns_owner("nobody.bnb", resolver=FAKE_RESOLVER) is None


# ── Ownership proofs ───────────────────────────────────────────────


async def test_prove_ownership_ens_match() -> None:
    result = await prove_ownership("vitalik", VITALIK, proof_type="ens", resolver=FAKE_RESOLVER)
    assert result["owned"] is True
    assert result["confidence"] == 0.9
    assert result["method"] == "ens_ownership"
    assert "vitalik.eth" in result["evidence"]
    assert result["evidence"].endswith(VITALIK.lower())


async def test_prove_ownership_ens_mismatch() -> None:
    result = await prove_ownership("vitalik", BOB, proof_type="ens", resolver=FAKE_RESOLVER)
    assert result["owned"] is False
    assert result["confidence"] == 0.0


async def test_prove_ownership_cns_match() -> None:
    result = await prove_ownership("alice", ALICE, proof_type="cns", resolver=FAKE_RESOLVER)
    assert result["owned"] is True
    assert result["confidence"] == 0.9
    assert result["method"] == "cns_ownership"
    assert "alice.bnb" in result["evidence"]


async def test_prove_ownership_handle_normalization() -> None:
    # '@' prefix and case are normalized before building the ENS name.
    result = await prove_ownership("@VITALIK", VITALIK, proof_type="ens", resolver=FAKE_RESOLVER)
    assert result["owned"] is True


async def test_prove_ownership_signed_message() -> None:
    result = await prove_ownership(
        "vitalik",
        VITALIK,
        proof_type="signed_message",
        signature=SIG_65,
        message="I am @vitalik",
    )
    assert result["owned"] is True
    assert result["confidence"] == 1.0
    assert result["method"] == "signed_message"


async def test_prove_ownership_signed_message_64byte() -> None:
    result = await prove_ownership(
        "vitalik",
        VITALIK,
        proof_type="signed_message",
        signature=SIG_64,
        message="gm",
    )
    assert result["owned"] is True
    assert result["confidence"] == 1.0


async def test_prove_ownership_signed_message_invalid() -> None:
    assert (
        await prove_ownership("vitalik", VITALIK, proof_type="signed_message", signature="not-a-sig", message="hi")
    )["owned"] is False
    assert (await prove_ownership("vitalik", VITALIK, proof_type="signed_message", signature=None, message="hi"))[
        "owned"
    ] is False
    assert (await prove_ownership("vitalik", VITALIK, proof_type="signed_message", signature=SIG_65, message="   "))[
        "owned"
    ] is False
    # Missing 0x prefix / wrong length.
    assert (await prove_ownership("vitalik", VITALIK, proof_type="signed_message", signature=SIG_65[2:], message="hi"))[
        "owned"
    ] is False


async def test_prove_ownership_unknown_type() -> None:
    import pytest

    with pytest.raises(ValueError, match="nope"):
        await prove_ownership("vitalik", VITALIK, proof_type="nope")


# ── Bio extraction + chain inference ───────────────────────────────


async def test_attribution_from_bio_direct_addresses() -> None:
    bio = f"Founder. Wallet {VITALIK} · {SOL_MINT}"
    links = await attribution_from_bio("vitalik", bio, resolver=FAKE_RESOLVER)
    by_key = {(link["chain"], link["address"]): link for link in links}
    # EVM → ethereum, Solana → solana chain inference.
    assert ("ethereum", VITALIK.lower()) in by_key
    assert ("solana", SOL_MINT) in by_key
    for link in links:
        assert link["kol_id"] == "vitalik"
        assert link["attribution_method"] == "self_disclosed"
        assert link["status"] == "active"


async def test_attribution_from_bio_ens_ownership() -> None:
    bio = "builder. vitalik.eth"
    links = await attribution_from_bio("vitalik", bio, resolver=FAKE_RESOLVER)
    assert any(
        link["chain"] == "ethereum" and link["address"] == VITALIK.lower() and "vitalik.eth" in link["evidence"]
        for link in links
    )
    # The ownership-proven link carries 0.9 confidence.
    ens_links = [link for link in links if "vitalik.eth" in link["evidence"]]
    assert ens_links
    assert ens_links[0]["confidence"] == 0.9


async def test_attribution_from_bio_cns_chain() -> None:
    bio = "web3 native. alice.bnb"
    links = await attribution_from_bio("alice", bio, resolver=FAKE_RESOLVER)
    assert any(link["chain"] == "bsc" and link["address"] == ALICE.lower() for link in links)


async def test_attribution_handle_derived_domain() -> None:
    # Bio has no ENS name, but handle == vitalik → {handle}.eth is probed
    # and the ownership proof attributes the owner.
    links = await attribution_from_bio("vitalik", "just vibes", resolver=FAKE_RESOLVER)
    assert any(link["address"] == VITALIK.lower() and "vitalik.eth" in link["evidence"] for link in links)


async def test_attribution_from_bio_no_matches() -> None:
    links = await attribution_from_bio("nobody", "hello world", resolver=FAKE_RESOLVER)
    assert links == []


# ── Merge / dedupe ─────────────────────────────────────────────────


def _l1(
    kol_id: str,
    chain: str,
    address: str,
    confidence: float,
    evidence: str,
) -> dict[str, Any]:
    return {
        "kol_id": kol_id,
        "chain": chain,
        "address": address,
        "attribution_method": "self_disclosed",
        "confidence": confidence,
        "evidence": evidence,
        "status": "active",
    }


def test_merge_l1_dedupe_keeps_highest() -> None:
    existing = [_l1("vitalik", "ethereum", VITALIK, 0.5, "old")]
    new = [
        _l1("vitalik", "ethereum", VITALIK, 1.0, "new"),
        _l1("vitalik", "solana", SOL_MINT, 0.9, "sol"),
    ]
    merged = merge_l1(new, existing)
    assert len(merged) == 2
    by_chain = {link["chain"]: link for link in merged}
    assert by_chain["ethereum"]["confidence"] == 1.0
    assert by_chain["ethereum"]["evidence"] == "new"
    assert by_chain["solana"]["address"] == SOL_MINT


def test_merge_l1_evm_case_insensitive() -> None:
    upper = _l1("vitalik", "ethereum", VITALIK, 0.9, "upper")
    lower = _l1("vitalik", "ethereum", VITALIK.lower(), 0.95, "lower")
    merged = merge_l1([upper, lower], [])
    assert len(merged) == 1
    assert merged[0]["confidence"] == 0.95


def test_merge_l1_tie_keeps_first() -> None:
    first = _l1("vitalik", "ethereum", VITALIK, 0.9, "first")
    second = _l1("vitalik", "ethereum", VITALIK, 0.9, "second")
    merged = merge_l1([second], [first])
    assert len(merged) == 1
    assert merged[0]["evidence"] == "first"


def test_merge_l1_solana_case_sensitive() -> None:
    # Solana base58 is case-sensitive — same letters, different case = distinct.
    lower_mint = SOL_MINT.lower() if SOL_MINT.lower() != SOL_MINT else SOL_MINT.upper()
    a = _l1("vitalik", "solana", SOL_MINT, 0.9, "a")
    b = _l1("vitalik", "solana", lower_mint, 0.9, "b")
    merged = merge_l1([a, b], [])
    assert len(merged) == 2
