"""Tests for app/domains/scanner/social_crossref.py.

Pure unit tests: real JSON fixture files (mirroring the production shapes)
are written to tmp_path and loaded through the public loaders — no network,
no DB, no dependency on the live dataset files.
"""

from __future__ import annotations

import json

import pytest

from app.domains.scanner.social_crossref import (
    crossref_signal,
    crossref_wallet,
    load_known_scammers,
    load_kol_wallets,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures (real JSON shapes, minimal slices)
# ═══════════════════════════════════════════════════════════════════════

SCAMMERS_FIXTURE = {
    "accounts": [
        {
            "handle": "ScamLord",
            "name": "Scam Lord",
            "category": "rug_puller",
            "severity": "high",
            "verified": True,
        },
        {
            "handle": "blknoiz06",
            "name": "Ansem",
            "category": "scam_shiller",
            "severity": "medium",
            "verified": False,
            "aliases": ["ansem_x"],
        },
        {
            "handle": "WalletScammer",
            "name": "Wallet Scammer",
            "category": "ponzi",
            "severity": "high",
            "verified": True,
            # Production file has no address fields today ([REVIEW]); this
            # fixture exercises the address-matching path the API supports.
            "address": "0x1234567890aBcdef1234567890AbCdEf12345678",
        },
    ]
}

KOL_WALLETS_FIXTURE = {
    "updated": "2026-08-13",
    "wallets": [
        {
            "handle": "profit",
            "name": "Profit",
            "chain": "solana",
            "address": "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E",
            "confidence": "strong_heuristic",
            "source": "github",
            "verified": True,
        },
        {
            "handle": "vitalik",
            "name": "Vitalik Buterin",
            "chain": "ethereum",
            "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
            "confidence": "verified",
            "source": "manual",
            "verified": True,
        },
        {
            "handle": "trader_eth",
            "name": "Trader Eth",
            "chain": "bsc",
            "address": "0xD8DA6BF26964AF9D7EED9E03E53415D37AA96045",
            "confidence": "verified",
            "source": "manual",
            "verified": True,
        },
    ],
}


@pytest.fixture
def datasets(tmp_path):
    scam_path = tmp_path / "known_scammers.json"
    kol_path = tmp_path / "verified_kol_wallets.json"
    scam_path.write_text(json.dumps(SCAMMERS_FIXTURE), encoding="utf-8")
    kol_path.write_text(json.dumps(KOL_WALLETS_FIXTURE), encoding="utf-8")
    return {
        "scammers": load_known_scammers(str(scam_path)),
        "kol_wallets": load_kol_wallets(str(kol_path)),
    }


def crossref(address: str, chain: str = "", datasets=None) -> dict:
    """Shorthand: cross-ref against fixture datasets."""
    datasets = datasets or {}
    return crossref_wallet(
        address,
        chain,
        known_scammers=datasets.get("scammers"),
        kol_wallets=datasets.get("kol_wallets"),
    )


# ═══════════════════════════════════════════════════════════════════════
# Loaders
# ═══════════════════════════════════════════════════════════════════════


def test_loaders_parse_real_shapes(datasets):
    assert len(datasets["scammers"]) == 3
    assert len(datasets["kol_wallets"]) == 3


def test_loaders_fail_open_on_missing_file(tmp_path):
    missing = str(tmp_path / "nope.json")
    assert load_known_scammers(missing) == []
    assert load_kol_wallets(missing) == []


# ═══════════════════════════════════════════════════════════════════════
# crossref_wallet — address matching
# ═══════════════════════════════════════════════════════════════════════


def test_crossref_matches_scammer_address(datasets):
    result = crossref("0x1234567890aBcdef1234567890AbCdEf12345678", "ethereum", datasets)
    assert result["known_scammer"] is True
    assert result["scammer_handle"] == "WalletScammer"
    assert result["scammer_category"] == "ponzi"
    assert result["kol_handle"] is None


def test_crossref_matches_kol_solana_address(datasets):
    result = crossref("G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E", "solana", datasets)
    assert result["known_scammer"] is False
    assert result["kol_handle"] == "profit"
    assert result["kol_name"] == "Profit"


def test_crossref_evm_checksum_normalized_case_insensitive(datasets):
    # Uppercase/lowercase-mixed 0x input must match the checksummed fixture.
    result = crossref("0XD8DA6BF26964AF9D7EED9E03E53415D37AA96045", "ethereum", datasets)
    assert result["kol_handle"] == "vitalik"


def test_crossref_solana_case_sensitive_no_match(datasets):
    # base58 is case-sensitive: flipped-case Solana address must not match.
    flipped = "g5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E"
    assert crossref(flipped, "solana", datasets) == {}


def test_crossref_unknown_address_empty(datasets):
    assert crossref("0x0000000000000000000000000000000000000000", "ethereum", datasets) == {}


# ═══════════════════════════════════════════════════════════════════════
# crossref_wallet — handle matching
# ═══════════════════════════════════════════════════════════════════════


def test_crossref_matches_scammer_handle_case_insensitive(datasets):
    result = crossref("@scamlord", "solana", datasets)
    assert result["known_scammer"] is True
    assert result["scammer_handle"] == "ScamLord"
    assert result["scammer_category"] == "rug_puller"


def test_crossref_matches_scammer_alias(datasets):
    result = crossref("ANSEM_X", "solana", datasets)
    assert result["known_scammer"] is True
    assert result["scammer_handle"] == "blknoiz06"


def test_crossref_matches_kol_handle(datasets):
    result = crossref("@Profit", "solana", datasets)
    assert result["known_scammer"] is False
    assert result["kol_handle"] == "profit"
    assert result["kol_name"] == "Profit"


def test_crossref_unknown_handle_empty(datasets):
    assert crossref("@nobody_important", "solana", datasets) == {}


def test_crossref_scammer_dominates_kol(datasets):
    # Same handle present in both fixture sets → scammer wins.
    result = crossref("profit", "solana", datasets)
    assert result["known_scammer"] is False  # profit is only a KOL here
    # Force collision: cross-ref a scammer handle against the KOL file too.
    result = crossref_wallet(
        "scamlord",
        "solana",
        known_scammers=datasets["scammers"],
        kol_wallets=[{"handle": "scamlord", "name": "KOL ScamLord", "chain": "solana", "address": "x"}],
    )
    assert result["known_scammer"] is True
    assert result["kol_handle"] is None


def test_crossref_empty_input(datasets):
    assert crossref("", "solana", datasets) == {}
    assert crossref(None, "solana", datasets) == {}


# ═══════════════════════════════════════════════════════════════════════
# crossref_signal
# ═══════════════════════════════════════════════════════════════════════


def test_signal_scammer_string():
    result = {
        "known_scammer": True,
        "scammer_handle": "ScamLord",
        "scammer_category": "rug_puller",
        "kol_handle": None,
        "kol_name": None,
    }
    assert crossref_signal(result) == "deployer is known scammer @ScamLord (rug_puller)"


def test_signal_scammer_without_category():
    result = {"known_scammer": True, "scammer_handle": "ScamLord", "scammer_category": None}
    assert crossref_signal(result) == "deployer is known scammer @ScamLord"


def test_signal_kol_string():
    result = {
        "known_scammer": False,
        "scammer_handle": None,
        "scammer_category": None,
        "kol_handle": "profit",
        "kol_name": "Profit",
    }
    assert crossref_signal(result) == "wallet belongs to KOL @profit"


def test_signal_clean_none():
    assert crossref_signal({}) is None
    assert crossref_signal({"known_scammer": False, "kol_handle": None}) is None


# ═══════════════════════════════════════════════════════════════════════
# End-to-end: crossref + signal round-trips
# ═══════════════════════════════════════════════════════════════════════


def test_full_roundtrip_scammer(datasets):
    result = crossref("0x1234567890aBcdef1234567890AbCdEf12345678", "ethereum", datasets)
    assert crossref_signal(result) == "deployer is known scammer @WalletScammer (ponzi)"


def test_full_roundtrip_kol(datasets):
    result = crossref("@profit", "solana", datasets)
    assert crossref_signal(result) == "wallet belongs to KOL @profit"


def test_full_roundtrip_clean(datasets):
    assert crossref_signal(crossref("0xabcdef1234567890abcdef1234567890abcdef12", "ethereum", datasets)) is None
