"""
Entity Extraction - Pure Function Extractor
============================================
Regex-based crypto entity extraction. No I/O, runs in microseconds.
"""

import re
from typing import Any

from app.entity_extraction.models import EntityExtractionResult
from app.entity_extraction.patterns import (
    CHAIN_NAMES,
    ENS_DOMAIN_RE,
    EVM_ADDRESS_RE,
    PROTOCOL_NAMES,
    SCAM_KEYWORDS,
    SOLANA_ADDRESS_RE,
    TOKEN_SYMBOL_RE,
    TX_HASH_RE,
)


def extract_entities(text: str) -> EntityExtractionResult:
    """
    Extract crypto-specific entities from *text* using regex only.
    No external API or ML model required.

    Matching strategy:
      - Transaction hashes matched first (64 hex); their spans are recorded
        so EVM addresses (40 hex) that overlap a tx hash are excluded.
      - Solana address regex is broad (base58); candidates that look like
        plain English words are filtered out.
    """
    if not text:
        return EntityExtractionResult()

    # --- Transaction hashes (64 hex chars) ---
    tx_hashes: list[str] = []
    tx_hash_set: set[str] = set()
    tx_spans: list[tuple[int, int]] = []
    for m in TX_HASH_RE.finditer(text):
        val = m.group(0)
        if val not in tx_hash_set:
            tx_hash_set.add(val)
            tx_hashes.append(val)
        tx_spans.append((m.start(), m.end()))

    def _overlaps_tx(start: int, end: int) -> bool:
        return any(start >= ts and end <= te for ts, te in tx_spans)

    # --- EVM addresses (40 hex chars, not inside a tx hash) ---
    evm_addresses: list[str] = []
    evm_set: set[str] = set()
    for m in EVM_ADDRESS_RE.finditer(text):
        if _overlaps_tx(m.start(), m.end()):
            continue
        val = m.group(0)
        if val not in evm_set:
            evm_set.add(val)
            evm_addresses.append(val)

    # --- Solana addresses (base58, 32-44 chars) ---
    solana_addresses: list[str] = []
    solana_set: set[str] = set()
    for m in SOLANA_ADDRESS_RE.finditer(text):
        val = m.group(0)
        if len(val) <= 10 and val.isalpha():
            continue
        if val.endswith(".eth"):
            continue
        has_digit = any(c.isdigit() for c in val)
        has_upper = any(c.isupper() for c in val)
        has_lower = any(c.islower() for c in val)
        if not has_digit and not (has_upper and has_lower) and len(val) < 32:
            continue
        if val not in solana_set:
            solana_set.add(val)
            solana_addresses.append(val)

    # --- Token symbols ($ETH, $SOL, ...) ---
    token_symbols: list[str] = []
    token_set: set[str] = set()
    for m in TOKEN_SYMBOL_RE.finditer(text):
        val = m.group(0)
        if val not in token_set:
            token_set.add(val)
            token_symbols.append(val)

    # --- ENS domains ---
    ens_domains: list[str] = []
    ens_set: set[str] = set()
    for m in ENS_DOMAIN_RE.finditer(text):
        val = m.group(0)
        if val not in ens_set:
            ens_set.add(val)
            ens_domains.append(val)

    # --- Chain names ---
    chain_names: list[str] = []
    chain_set: set[str] = set()
    text_lower = text.lower()
    for chain in CHAIN_NAMES:
        pattern = re.compile(r"\b" + re.escape(chain) + r"\b")
        if pattern.search(text_lower) and chain not in chain_set:
            chain_set.add(chain)
            chain_names.append(chain)

    # --- Protocol names ---
    protocol_names: list[str] = []
    protocol_set: set[str] = set()
    for proto in PROTOCOL_NAMES:
        pattern = re.compile(r"\b" + re.escape(proto) + r"\b")
        if pattern.search(text_lower) and proto not in protocol_set:
            protocol_set.add(proto)
            protocol_names.append(proto)

    # --- Scam keywords ---
    scam_keywords: list[str] = []
    scam_set: set[str] = set()
    for kw in SCAM_KEYWORDS:
        pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        if pattern.search(text) and kw not in scam_set:
            scam_set.add(kw)
            scam_keywords.append(kw)

    return EntityExtractionResult(
        evm_addresses=evm_addresses, solana_addresses=solana_addresses,
        token_symbols=token_symbols, chain_names=chain_names,
        protocol_names=protocol_names, scam_keywords=scam_keywords,
        tx_hashes=tx_hashes, ens_domains=ens_domains,
    )
