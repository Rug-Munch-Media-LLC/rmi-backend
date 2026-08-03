"""Tests for crypto-aware RAG chunking strategies (app.rag.chunking)."""

from __future__ import annotations

import pytest

from app.rag.chunking import (
    STRATEGIES,
    Chunk,
    chunk_text,
    estimate_tokens,
    map_source_type_to_content_type,
)

SOLIDITY_SAMPLE = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract RugToken is ERC20 {
    uint256 public constant MAX_SUPPLY = 1_000_000_000e18;

    constructor(string memory name_, string memory symbol_) ERC20(name_, symbol_) {}

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        return super.transfer(to, amount);
    }

    event Minted(address indexed to, uint256 amount);
}
"""

WHITEPAPER_SAMPLE = """\
# RugMunch Protocol Whitepaper

Abstract: A protocol for detecting rug pulls before they happen. It combines on-chain
forensics, social sentiment analysis, and liquidity monitoring to flag high-risk tokens
in real time. The system scores tokens on a 0-100 risk scale.

## Introduction

Rug pulls are the most common exit scam in crypto. This protocol detects them by
monitoring deployer behavior, liquidity lock status, and honeypot characteristics.
The detection engine processes over 10,000 tokens per day across five chains and
fuses signals from more than thirty data providers.

## Tokenomics

| Component | Allocation |
|-----------|------------|
| Liquidity | 40%        |
| Treasury  | 30%        |
| Team      | 30%        |

## Security

The protocol uses multi-sig custody and timelocks. All smart contracts are audited
by independent firms, and a bug bounty program covers critical vulnerabilities.
"""


def test_chunk_text_returns_chunks() -> None:
    chunks = chunk_text("Hello world. " * 60, content_type="general")
    assert isinstance(chunks, list)
    assert all(isinstance(c, Chunk) for c in chunks)
    assert len(chunks) >= 1


def test_chunk_text_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_doc_is_single_chunk() -> None:
    chunks = chunk_text("Short doc.", content_type="whitepaper")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].content_type == "whitepaper"


def test_solidity_preserves_function_boundaries() -> None:
    chunks = chunk_text(SOLIDITY_SAMPLE, content_type="solidity")
    assert len(chunks) >= 4

    # Header chunk preserves pragma + imports + contract declaration.
    header = chunks[0].text
    assert "pragma solidity" in header
    assert "import" in header
    assert "contract RugToken" in header

    # Functions are split into their own chunks.
    mint = next(c.text for c in chunks if "function mint" in c.text)
    transfer = next(c.text for c in chunks if "function transfer" in c.text)
    assert mint != transfer
    assert "_mint(to, amount)" in mint
    assert "super.transfer" in transfer
    assert "function transfer" not in mint
    assert "function mint" not in transfer

    # Events are preserved.
    assert any("event Minted" in c.text for c in chunks)

    # Every chunk is within the token cap.
    assert all(c.token_count <= 512 for c in chunks)


def test_whitepaper_splits_at_headers() -> None:
    chunks = chunk_text(WHITEPAPER_SAMPLE, content_type="whitepaper")
    assert len(chunks) >= 3

    # Title + abstract preserved as the first chunk.
    assert "# RugMunch Protocol Whitepaper" in chunks[0].text
    assert "Abstract" in chunks[0].text

    tokenomics = next(c.text for c in chunks if "## Tokenomics" in c.text)
    security = next(c.text for c in chunks if "## Security" in c.text)

    # Sections are separated.
    assert "## Introduction" not in tokenomics
    assert "## Tokenomics" not in security

    # Markdown table stays in one chunk.
    assert "| Component | Allocation |" in tokenomics
    assert "| Liquidity | 40%        |" in tokenomics
    assert "| Treasury  | 30%        |" in tokenomics


def test_audit_preserves_severity_per_finding() -> None:
    audit = """# Audit Report
## Summary
We reviewed the withdrawal path, access control, and fund flow safety.

## Finding 1 [Critical]
Reentrancy in withdraw allows draining funds. The function makes an external call
before updating internal state, enabling a malicious recipient to re-enter and withdraw.
Severity: Critical

## Finding 2 [High]
Missing access control on mint. Anyone can mint tokens without authorization,
inflating supply and diluting holders.
Severity: High
"""
    chunks = chunk_text(audit, content_type="audit", chunk_size=400)
    finding1 = next(c.text for c in chunks if "Finding 1" in c.text)
    finding2 = next(c.text for c in chunks if "Finding 2" in c.text)
    assert finding1 != finding2
    assert "Severity: Critical" in finding1
    assert "Severity: High" in finding2


def test_news_preserves_first_sentence() -> None:
    text = "BREAKING: Rug detector catches honeypot. " * 2 + "More analysis follows. " * 40
    chunks = chunk_text(text, content_type="news", chunk_size=200)
    assert any(c.text.startswith("BREAKING") for c in chunks)


def test_social_single_post_single_chunk() -> None:
    chunks = chunk_text("gm frens, $RUG is mooning 🚀", content_type="social")
    assert len(chunks) == 1
    assert "$RUG" in chunks[0].text


def test_transaction_preserves_fields() -> None:
    tx = "\n".join(
        [
            "Hash: 0xabc123",
            "From: 0x1111111111111111111111111111111111111111",
            "To: 0x2222222222222222222222222222222222222222",
            "Value: 1.5 ETH",
            "Block: 12345678",
        ]
    )
    chunks = chunk_text(tx, content_type="transaction", chunk_size=100)
    joined = "\n".join(c.text for c in chunks)
    assert "From: 0x1111111111111111111111111111111111111111" in joined
    assert "Value: 1.5 ETH" in joined


def test_unknown_content_type_falls_back_to_general() -> None:
    chunks = chunk_text("Some long text. " * 60, content_type="not_a_real_type")
    assert len(chunks) > 1


def test_strategies_registry_has_required_entries() -> None:
    for name in ("solidity", "rust", "whitepaper", "audit", "news", "social", "transaction", "general"):
        assert name in STRATEGIES


def test_chunk_metadata() -> None:
    chunks = chunk_text(
        "Metadata test. " * 40,
        content_type="news",
        source_url="https://example.com/article",
        timestamp="2026-08-03T00:00:00Z",
    )
    assert len(chunks) >= 1
    c = chunks[0]
    assert c.content_type == "news"
    assert c.source_url == "https://example.com/article"
    assert c.timestamp == "2026-08-03T00:00:00Z"
    assert c.token_count > 0
    assert c.index >= 0
    assert c.content_hash  # non-empty


def test_token_limit_enforced() -> None:
    huge = "word " * 10000  # ~10k words >> 512 tokens
    chunks = chunk_text(huge, content_type="general")
    assert all(c.token_count <= 512 for c in chunks)
    assert all(estimate_tokens(c.text) <= 512 for c in chunks)


def test_map_source_type_to_content_type() -> None:
    assert map_source_type_to_content_type("news") == "news"
    assert map_source_type_to_content_type("scanner") == "transaction"
    assert map_source_type_to_content_type("scam") == "audit"
    assert map_source_type_to_content_type("twitter") == "social"
    assert map_source_type_to_content_type("research") == "whitepaper"
    assert map_source_type_to_content_type("unknown") == "general"
    assert map_source_type_to_content_type("") == "general"


@pytest.mark.parametrize("content_type", list(STRATEGIES))
def test_all_strategies_return_chunks(content_type: str) -> None:
    text = "Intro paragraph with enough detail to be useful.\n\n" * 8
    chunks = chunk_text(text, content_type=content_type, chunk_size=128)
    assert len(chunks) >= 1
    assert all(isinstance(c, Chunk) for c in chunks)
