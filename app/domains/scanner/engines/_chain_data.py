"""Chain Data Access Layer — THE single source of truth for chain→table→column mapping.

Every scanner engine MUST import ``get_chain_spec`` and use
``spec.transfers_table``, ``spec.addr_col``, ``spec.from_col``, etc. instead of
hardcoding ClickHouse table names or column mappings inline.

WHY THIS EXISTS
===============
Three fix waves of scanner bugs traced back to the same root cause: engines
hardcoded table names and column mappings inline (``if chain == "solana":
...solana_spl_balance_changes... else: ...evm_transfers...`` branches scattered
across 6+ functions in ``_funding_queries.py`` alone). When a new chain is
added or a table schema changes, every engine needs manual updates — and bugs
like "query evm_transfers for Solana" or "don't lowercase EVM addresses" keep
recurring because there is no single source of truth.

This module IS that source of truth. The contract:

1. Which ClickHouse table holds transfers/balance-changes for a given chain
   (``spec.transfers_table``).
2. Which table holds dex_events (swaps) for a given chain
   (``spec.dex_table`` — same table for all chains, filtered by ``chain=``).
3. The column names for that table: address columns (``addr_col``,
   ``from_col``, ``to_col``), amount expression (``amount_expr``), timestamp
   (``ts_col``), tx hash (``tx_hash_col``), block/slot (``block_col``).
4. Whether addresses are case-sensitive (``spec.is_solana`` — Solana base58
   is case-sensitive and must be preserved; EVM hex is lowercased).
5. The normalized chain name to pass to ClickHouse ``WHERE`` clauses
   (``spec.chain`` / ``spec.chain_filter``).

USAGE
=====
::

    from app.domains.scanner.engines._chain_data import get_chain_spec, normalize_addr

    spec = get_chain_spec(chain)          # raises ValueError for unknown chains
    query = f'''
        SELECT {spec.to_col}, {spec.amount_expr} AS amount
        FROM {spec.transfers_table}
        WHERE {spec.addr_col} = %(token)s
        {spec.chain_filter}
    '''
    params = {"token": normalize_addr(token, chain)}
    if not spec.is_solana:
        params["chain"] = spec.chain

MIGRATION
=========
Engines are migrated incrementally; until then they keep their inline
branches. New code MUST NOT add new hardcoded table names — always go through
this module. ``normalize_addr`` re-exports the existing ``_common._norm_addr``
logic so engines can switch imports without behavior change.

TRON NOTE
=========
Tron transfer data is served by the TronGrid provider
(``app.core.tron_provider``), not a ClickHouse transfers table, so
``transfers_table`` is empty for tron and ``has_transfers_table`` is False.
Engines handling tron must branch on that (or use the provider directly).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "EVM_CHAINS",
    "SOLANA_CHAINS",
    "TRON_CHAINS",
    "ChainDataSpec",
    "get_chain_spec",
    "normalize_addr",
]


@dataclass(frozen=True)
class ChainDataSpec:
    """Immutable per-chain data-access specification.

    Attributes:
        chain: Normalized chain name to pass to ClickHouse ``WHERE`` clauses
            (``"solana"``, ``"ethereum"``, ``"base"``, ...).
        is_solana: True for Solana-family chains (case-sensitive base58
            addresses — NEVER lowercase them).
        transfers_table: Fully-qualified ClickHouse table holding
            transfers/balance-changes (``wallet_memory.evm_transfers`` or
            ``wallet_memory.solana_spl_balance_changes``). Empty string for
            chains without a ClickHouse transfers table (tron — TronGrid
            provider instead).
        dex_table: Fully-qualified ClickHouse table holding DEX swap events
            (``wallet_memory.dex_events`` — same for all chains, filtered by
            ``chain =``).
        addr_col: Column holding the token address (``token_address`` EVM,
            ``mint`` Solana).
        from_col: Sender column (``from_address`` EVM; Solana: ``owner`` with
            ``delta < 0``).
        to_col: Recipient column (``to_address`` EVM; Solana: ``owner`` with
            ``delta > 0``).
        amount_expr: SQL expression for the transfer amount
            (``toFloat64OrZero(value)`` EVM, ``toFloat64OrZero(delta)``
            Solana).
        ts_col: Timestamp column (``block_timestamp`` — both families).
        tx_hash_col: Transaction hash column (``tx_hash`` — both).
        chain_filter: Extra WHERE fragment scoping the query to this chain
            (``AND chain = %(chain)s`` for EVM; empty for Solana — the
            ``solana_spl_balance_changes`` table is chain-specific).
        block_col: Block/slot column (``block_number`` EVM, ``slot`` Solana).
    """

    chain: str
    is_solana: bool
    transfers_table: str
    dex_table: str
    addr_col: str
    from_col: str
    to_col: str
    amount_expr: str
    ts_col: str
    tx_hash_col: str
    chain_filter: str
    block_col: str

    @property
    def has_transfers_table(self) -> bool:
        """True when this chain has a ClickHouse transfers/balance table."""
        return bool(self.transfers_table)


# Chains whose transfer data lives in ``wallet_memory.evm_transfers``.
EVM_CHAINS = {
    "ethereum",
    "base",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "linea",
    "scroll",
    "zksync",
    "mantle",
    "gnosis",
    "celo",
    "fantom",
    "blast",
    "fraxtal",
    "metis",
    "robinhood",
}

# Chains whose balance data lives in ``wallet_memory.solana_spl_balance_changes``.
SOLANA_CHAINS = {"solana", "svm"}

# Chains served by the TronGrid provider (no ClickHouse transfers table yet).
TRON_CHAINS = {"tron"}

_DEX_TABLE = "wallet_memory.dex_events"


def _solana_spec() -> ChainDataSpec:
    return ChainDataSpec(
        chain="solana",
        is_solana=True,
        transfers_table="wallet_memory.solana_spl_balance_changes",
        dex_table=_DEX_TABLE,
        addr_col="mint",
        from_col="owner",  # sender = owner row with delta < 0
        to_col="owner",  # recipient = owner row with delta > 0
        amount_expr="toFloat64OrZero(delta)",
        ts_col="block_timestamp",
        tx_hash_col="tx_hash",
        chain_filter="",  # table is chain-specific
        block_col="slot",
    )


def _tron_spec() -> ChainDataSpec:
    return ChainDataSpec(
        chain="tron",
        is_solana=False,
        transfers_table="",  # TronGrid provider (app.core.tron_provider), not ClickHouse
        dex_table=_DEX_TABLE,
        addr_col="token_address",
        from_col="from_address",
        to_col="to_address",
        amount_expr="toFloat64OrZero(value)",
        ts_col="block_timestamp",
        tx_hash_col="tx_hash",
        chain_filter="AND chain = %(chain)s",
        block_col="block_number",
    )


def _evm_spec(chain: str) -> ChainDataSpec:
    return ChainDataSpec(
        chain=chain,
        is_solana=False,
        transfers_table="wallet_memory.evm_transfers",
        dex_table=_DEX_TABLE,
        addr_col="token_address",
        from_col="from_address",
        to_col="to_address",
        amount_expr="toFloat64OrZero(value)",
        ts_col="block_timestamp",
        tx_hash_col="tx_hash",
        chain_filter="AND chain = %(chain)s",
        block_col="block_number",
    )


def get_chain_spec(chain: str) -> ChainDataSpec:
    """Return the data spec for a chain. Raises ValueError for unknown chains.

    This is the ONLY sanctioned way for scanner engines to resolve
    chain→table→column mappings. Never hardcode table names in engine code.

    Args:
        chain: User-facing chain name (any case; ``"SVM"`` and ``"solana"``
            both resolve to the Solana spec).

    Returns:
        The frozen :class:`ChainDataSpec` for the chain.

    Raises:
        ValueError: If the chain is unknown/empty.
    """
    norm = (chain or "").strip().lower()
    if norm in SOLANA_CHAINS:
        return _solana_spec()
    if norm in TRON_CHAINS:
        return _tron_spec()
    if norm in EVM_CHAINS:
        return _evm_spec(norm)
    raise ValueError(f"Unknown chain: {chain!r}. Known chains: solana/svm, tron, {sorted(EVM_CHAINS)}")


def normalize_addr(addr: str, chain: str) -> str:
    """Chain-aware address normalization (re-exports ``_common._norm_addr``).

    Solana base58 addresses are CASE-SENSITIVE — preserve them exactly
    (strip whitespace only). EVM hex addresses are case-insensitive —
    lowercase them for consistent matching. Tron follows the EVM rule per
    the platform convention (see module docstring).

    Args:
        addr: Raw address string (may be None/empty/whitespace).
        chain: Chain name (any case); resolved via :func:`get_chain_spec`.

    Returns:
        The normalized address string.
    """
    spec = get_chain_spec(chain)
    s = (addr or "").strip()
    return s if spec.is_solana else s.lower()
