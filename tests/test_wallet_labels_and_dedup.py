"""Pure unit tests for wallet known-entity labels + RAG dedup improvements.

Covers (no network / no DB — everything is in-memory):
  1. ``label_known_entities`` — known EVM + Solana addresses resolve to the
     right entity tag; unknown/empty/None input returns ``[]``.
  2. EIP-55 checksum normalization — idempotent; mixed-case input maps to the
     canonical checksummed address.
  3. Solana base58 case preservation in ``normalize_address``.
  4. ``content_hash`` — identical (modulo whitespace/case) blocks collapse to
     one hash; distinct content produces a distinct hash.
  5. ``near_duplicate`` — token-set Jaccard threshold behavior.
  6. Extensibility — a disk-loaded registry file merges with the bundled seed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.domains.wallet import labels
from app.domains.wallet.labels import (
    KNOWN_ENTITIES_SEED,
    KnownEntity,
    label_known_entities,
)
from app.rag.dedup import (
    content_hash,
    doc_id_hash,
    is_valid_evm_address,
    near_duplicate,
    normalize_address,
    normalize_text,
    to_checksum_address,
)

# Public addresses from the bundled seed (Etherscan public labels).
BINANCE_ETH = "0x28C6c06298d514Db089934071355E5743bf21d60"
COINBASE_ETH = "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3"
KRAKEN_ETH = "0x267be1C1d684F78cb4F6a176C4911b741E4Ffdc0"
BINANCE_SOL = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
UNKNOWN_ETH = "0x1111111111111111111111111111111111111111"
UNKNOWN_SOL = "G9X9Jq7Yy6Y7n9QmQqXxWwVvUuTtSsRrPpOoNnMmLlKk"


# ── Goal 1: known-entity labels ─────────────────────────────────────────


def test_label_known_entities_known_evm() -> None:
    tags = label_known_entities(BINANCE_ETH, "eth")
    assert "binance" in tags
    assert "cex" in tags


def test_label_known_entities_case_insensitive_evm() -> None:
    lower = BINANCE_ETH.lower()
    upper = BINANCE_ETH.upper()
    assert label_known_entities(lower, "eth") == label_known_entities(BINANCE_ETH, "eth")
    assert label_known_entities(upper, "eth") == label_known_entities(BINANCE_ETH, "eth")


def test_label_known_entities_chain_alias() -> None:
    assert label_known_entities(BINANCE_ETH, "ethereum") == label_known_entities(BINANCE_ETH, "eth")


def test_label_known_entities_known_solana() -> None:
    tags = label_known_entities(BINANCE_SOL, "sol")
    assert "binance" in tags
    assert "cex" in tags


def test_label_known_entities_solana_case_sensitive() -> None:
    # Solana base58 is case-sensitive — a different-cased address must NOT match.
    assert label_known_entities(BINANCE_SOL.lower(), "sol") == []
    assert label_known_entities(BINANCE_SOL, "sol") != []


def test_label_known_entities_unknown_returns_empty() -> None:
    assert label_known_entities(UNKNOWN_ETH, "eth") == []
    assert label_known_entities(UNKNOWN_SOL, "sol") == []


def test_label_known_entities_empty_or_none_returns_empty() -> None:
    assert label_known_entities("", "eth") == []
    assert label_known_entities(None, "eth") == []  # type: ignore[arg-type]
    assert label_known_entities("   ", "sol") == []


def test_label_known_entities_wrong_chain_no_match() -> None:
    # Known EVM address queried on Solana must not match.
    assert label_known_entities(BINANCE_ETH, "sol") == []


def test_seed_entities_have_source_and_confidence() -> None:
    assert len(KNOWN_ENTITIES_SEED) >= 12
    for e in KNOWN_ENTITIES_SEED:
        assert e.source
        assert 0.0 <= e.confidence <= 1.0
        assert e.chains


def test_disk_registry_merges_with_seed(tmp_path) -> None:
    registry_file = tmp_path / "known_entities.json"
    registry_file.write_text(
        json.dumps(
            [
                {
                    "address": UNKNOWN_ETH,
                    "name": "OKX",
                    "entity_type": "cex",
                    "chains": ["eth"],
                    "source": "test-fixture",
                    "confidence": 0.9,
                }
            ]
        )
    )
    labels.reset_known_entities_cache()
    with patch.dict("os.environ", {labels.KNOWN_ENTITIES_PATH_ENV: str(registry_file)}, clear=False):
        merged = labels.get_known_entities()
    okx = next(e for e in merged if e.name.lower() == "okx")
    assert okx.address == UNKNOWN_ETH
    assert okx.source == "test-fixture"
    assert okx.confidence == 0.9
    # Bundled seed still present after merge.
    assert any(e.address.lower() == BINANCE_ETH.lower() for e in merged)
    # Loaded entities resolve through label_known_entities too.
    assert "okx" in label_known_entities(UNKNOWN_ETH, "eth")
    labels.reset_known_entities_cache()


def test_label_known_entities_returns_list_of_str() -> None:
    for tags in (label_known_entities(BINANCE_ETH, "eth"), label_known_entities(UNKNOWN_ETH, "eth")):
        assert isinstance(tags, list)
        assert all(isinstance(t, str) for t in tags)


# ── Goal 2: dedup improvements ──────────────────────────────────────────


def test_to_checksum_address_idempotent() -> None:
    c1 = to_checksum_address(BINANCE_ETH)
    c2 = to_checksum_address(c1)
    assert c1 == c2
    # Mixed-case input resolves to the same canonical checksum.
    assert to_checksum_address(BINANCE_ETH.lower()) == c1


def test_normalize_address_idempotent_evm() -> None:
    norm = normalize_address(BINANCE_ETH, "eth")
    assert normalize_address(norm, "eth") == norm
    assert normalize_address(BINANCE_ETH.lower(), "eth") == norm


def test_normalize_address_preserves_solana_case() -> None:
    assert normalize_address(BINANCE_SOL, "sol") == BINANCE_SOL
    assert normalize_address(BINANCE_SOL.lower(), "sol") == BINANCE_SOL.lower()
    assert normalize_address(" sol-addr-trim ", "sol") == "sol-addr-trim"


def test_normalize_address_empty_and_unknown_chain() -> None:
    assert normalize_address("", "eth") == ""
    assert normalize_address(BINANCE_ETH, "unknown-chain") == BINANCE_ETH


def test_is_valid_evm_address() -> None:
    assert is_valid_evm_address(BINANCE_ETH)
    assert not is_valid_evm_address("0x123")
    assert not is_valid_evm_address("")
    assert not is_valid_evm_address(BINANCE_SOL)


def test_content_hash_collapses_identical_blocks() -> None:
    text_a = "Binance announces new listing.  Users can deposit now.\n\nFollow us on X!"
    text_b = "   BINANCE ANNOUNCES new listing. Users can deposit now.  "
    # Same body, different whitespace/case/boilerplate → same hash.
    assert content_hash(text_a) == content_hash(text_b)
    # Distinct content → distinct hash.
    assert content_hash("Binance announces new listing.") != content_hash("Coinbase announces new listing.")


def test_content_hash_empty() -> None:
    assert content_hash("") == content_hash(None)  # type: ignore[arg-type]


def test_normalize_text_canonical_form() -> None:
    assert normalize_text("  Hello   WORLD\nsubscribe to our newsletter\n") == "hello world"


def test_near_duplicate_threshold_behavior() -> None:
    a = "Binance lists PEPE after record volume"
    b = "Binance lists PEPE after record volume"  # identical
    c = "Binance lists SHIB after record volume"  # one token differs
    d = "Coinbase announces quarterly earnings call"  # disjoint
    assert near_duplicate(a, b) is True
    assert near_duplicate(a, b, threshold=0.99) is True
    # a vs c: 5/7 tokens shared → Jaccard ≈ 0.714
    assert near_duplicate(a, c, threshold=0.7) is True
    assert near_duplicate(a, c, threshold=0.9) is False
    assert near_duplicate(a, c, threshold=0.5) is True
    assert near_duplicate(a, d, threshold=0.9) is False


def test_near_duplicate_empty_input_fail_open() -> None:
    assert near_duplicate("", "some content") is False
    assert near_duplicate("some content", "") is False
    assert near_duplicate("", "") is False


def test_doc_id_hash_stable() -> None:
    assert doc_id_hash("doc-123") == doc_id_hash("doc-123")
    assert doc_id_hash("doc-123") != doc_id_hash("doc-124")
    assert doc_id_hash("") == doc_id_hash("")
    assert len(doc_id_hash("x")) == 32


def test_registry_types() -> None:
    # KnownEntity contract: chains is a tuple, tags optional.
    e = KNOWN_ENTITIES_SEED[0]
    assert isinstance(e, KnownEntity)
    assert isinstance(e.chains, tuple)
