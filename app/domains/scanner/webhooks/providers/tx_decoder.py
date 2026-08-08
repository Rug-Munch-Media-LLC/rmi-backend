"""Transaction decoder — pure functions turning raw chain data into enhanced payloads.

Mirrors Helius "enhanced transaction" webhook output: instead of an aggregated
row (what our poll providers emit), an enhanced payload carries the full decoded
transaction with instruction-level detail:

  - Solana ``jsonParsed`` transactions (from ``getTransaction`` / ``getParsedTransaction``):
    token transfers (SPL Token + Token-2022), native SOL transfers, swap
    classification by program ID, NFT mint/sale detection, and per-account
    balance deltas (``accountData``).
  - EVM receipts (from ``eth_getTransactionReceipt``): ERC-20 / ERC-721 / ERC-1155
    transfer logs decoded into ``tokenTransfers``, swap classification by router
    address.

This module is intentionally pure — no network, no Redis, no ClickHouse — so
every branch is unit-testable. The providers (``enhanced_transaction.py``) are
responsible for fetching the raw data and feeding it here.

Payload shape (normalized across chains)::

    {
      "chain": "solana" | "ethereum" | ...,
      "signature": "tx signature/hash",
      "slot": 171942732,                  # solana only
      "block_number": "0x...",            # evm only
      "timestamp": 1673445241,
      "status": "success" | "failed",
      "type": "SWAP" | "TRANSFER" | "NFT_SALE" | "NFT_MINT" | "UNKNOWN" | ...,
      "fee": 10000,
      "fee_payer": "address",
      "description": "human-readable summary",
      "tokenTransfers": [...],
      "nativeTransfers": [...],
      "accountData": [...],
      "instructions": [...]
    }
"""

from __future__ import annotations

from typing import Any

# ── Chain names ─────────────────────────────────────────────────────────
CHAIN_SOLANA = "solana"
CHAIN_EVM = "evm"  # normalized EVM; provider fills the concrete chain id

# ── Decoded transaction types (Helius-aligned subset) ───────────────────
TX_SWAP = "SWAP"
TX_TRANSFER = "TRANSFER"
TX_NFT_SALE = "NFT_SALE"
TX_NFT_MINT = "NFT_MINT"
TX_NFT_BURN = "BURN"
TX_LIQUIDITY = "LIQUIDITY"
TX_UNKNOWN = "UNKNOWN"

# ── Known Solana program IDs (used for swap/NFT classification) ─────────
# These are well-known public constants — do NOT invent program IDs.
SOLANA_PROGRAMS: dict[str, str] = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "JUPITER",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "JUPITER_V4",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wF2t2j98": "RAYDIUM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "RAYDIUM_CLMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyD": "ORCA",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "PUMP_FUN",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s": "METAPLEX",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL_TOKEN",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "SPL_TOKEN_2022",
    "11111111111111111111111111111111": "SYSTEM",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "ASSOCIATED_TOKEN",
}

# Program IDs that indicate a swap instruction (DEX AMM programs)
SWAP_PROGRAM_IDS: set[str] = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter v6
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",  # Jupiter v4
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wF2t2j98",  # Raydium V4
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyD",  # Orca Whirlpools
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PumpFun AMM
}

# Metaplex program IDs that indicate NFT minting
NFT_MINT_PROGRAM_IDS: set[str] = {
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",  # Metaplex Token Metadata
}

# ── EVM topic0 signatures ───────────────────────────────────────────────
TOPIC_ERC20_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_ERC721_TRANSFER = TOPIC_ERC20_TRANSFER  # same topic, used by ERC-721 too
TOPIC_ERC1155_TRANSFER_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"

# Known EVM DEX router address prefixes (same list as first_swap provider)
EVM_DEX_PREFIXES: list[str] = [
    "0x7a250d",  # Uniswap V2 Router
    "0x68b346",  # Uniswap V3 Router
    "0xe59242",  # Uniswap V3 SwapRouter
    "0xd9e1ce",  # SushiSwap Router
    "0x10ed43",  # PancakeSwap V2 Router (BSC)
    "0x13f4ea",  # PancakeSwap V2 Router (BSC)
    "0xa5e082",  # QuickSwap Router (Polygon)
    "0x1b02da",  # SushiSwap on Arbitrum
    "0x4c6004",  # Velodrome Router (Optimism)
    "0x327df1",  # Aerodrome Router (Base)
]


# ═══════════════════════════════════════════════════════════════════════
# Solana jsonParsed decoding
# ═══════════════════════════════════════════════════════════════════════


def _sol_account_keys(tx: dict[str, Any]) -> list[str]:
    """Extract the ordered account key list from a parsed Solana tx.

    ``accountKeys`` may be plain strings or ``{pubkey, signer, writable}``
    objects depending on RPC encoding.
    """
    msg = tx.get("transaction", {}).get("message", {})
    keys: list[str] = []
    for key in msg.get("accountKeys", []) or []:
        if isinstance(key, dict):
            keys.append(str(key.get("pubkey", "")))
        else:
            keys.append(str(key))
    return keys


def _resolve_account(tx: dict[str, Any], index: int | None) -> str:
    """Resolve an account index to an address, falling back to empty string."""
    if index is None:
        return ""
    keys = _sol_account_keys(tx)
    if 0 <= index < len(keys):
        return keys[index]
    return ""


def _sol_parse_spl_transfer(
    tx: dict[str, Any],
    parsed: dict[str, Any],
) -> dict[str, Any] | None:
    """Parse an SPL token transfer/transferChecked parsed instruction.

    Returns a normalized tokenTransfer dict (mint, source/token account,
    amount, decimals) or None when the instruction is not a transfer.
    ``transferChecked`` carries the mint inline; plain ``transfer`` does not,
    so the mint is resolved from ``meta.pre/postTokenBalances`` by matching
    the source/destination token account.
    """
    ptype = parsed.get("type")
    if ptype not in ("transfer", "transferChecked"):
        return None
    info = parsed.get("info", {})
    amount = info.get("amount")
    try:
        amount_int = int(amount) if amount is not None else 0
    except (TypeError, ValueError):
        amount_int = 0
    decimals = info.get("decimals")
    try:
        decimals_int = int(decimals) if decimals is not None else 0
    except (TypeError, ValueError):
        decimals_int = 0

    mint = str(info.get("mint", "") or "")
    if not mint:
        mint = _sol_resolve_mint(tx, info.get("source"), info.get("destination"))

    return {
        "mint": mint,
        "source": str(info.get("source", "")),
        "destination": str(info.get("destination", "")),
        "authority": str(info.get("authority", "")),
        "amount": amount_int,
        "decimals": decimals_int,
        "uiAmount": amount_int / (10**decimals_int) if decimals_int >= 0 else amount_int,
    }


def _sol_resolve_mint(tx: dict[str, Any], *token_accounts: Any) -> str:
    """Resolve a mint address from pre/post token balances by token account.

    SPL ``transfer`` instructions name only token accounts; the mint lives in
    ``meta.preTokenBalances`` / ``meta.postTokenBalances`` keyed by the same
    account index as the accountKeys list.
    """
    keys = _sol_account_keys(tx)
    wanted = {str(a).lower() for a in token_accounts if a}
    meta = tx.get("meta", {}) or {}

    def _scan(entries: list[dict[str, Any]]) -> str:
        for entry in entries:
            try:
                idx = int(entry.get("accountIndex", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(keys) and keys[idx].lower() in wanted:
                mint = str(entry.get("mint", "") or "")
                if mint:
                    return mint
        return ""

    return _scan(meta.get("preTokenBalances") or []) or _scan(meta.get("postTokenBalances") or [])


def _sol_parse_system_transfer(parsed: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a system program transfer (native SOL)."""
    if parsed.get("type") != "transfer":
        return None
    info = parsed.get("info", {})
    lamports = info.get("lamports")
    try:
        lamports_int = int(lamports) if lamports is not None else 0
    except (TypeError, ValueError):
        lamports_int = 0
    return {
        "fromUserAccount": str(info.get("source", "")),
        "toUserAccount": str(info.get("destination", "")),
        "amount": lamports_int,
        "uiAmount": lamports_int / 1e9,
    }


def _sol_iterate_instructions(tx: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield every instruction (top-level + inner) with resolved programId.

    Normalizes ``parsed`` instructions to include a resolved ``programId`` and
    a human ``program`` label so downstream classification is uniform.
    """
    out: list[dict[str, Any]] = []
    msg = tx.get("transaction", {}).get("message", {})
    instructions: list[dict[str, Any]] = list(msg.get("instructions", []) or [])

    for inner in tx.get("meta", {}).get("innerInstructions", []) or []:
        for instr in inner.get("instructions", []) or []:
            instructions.append(instr)

    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        program_id = str(instr.get("programId") or _resolve_account(tx, instr.get("programIdIndex")))
        program = str(instr.get("program") or "") or SOLANA_PROGRAMS.get(program_id, "")
        out.append(
            {
                "programId": program_id,
                "program": program,
                "parsed": instr.get("parsed") or {},
                "data": instr.get("data", ""),
            }
        )
    return out


def _sol_balance_changes(tx: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute per-account native + token balance deltas (Helius accountData)."""
    meta = tx.get("meta", {})
    keys = _sol_account_keys(tx)
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    pre_tokens = meta.get("preTokenBalances") or []
    post_tokens = meta.get("postTokenBalances") or []

    # index: [pre_token, post_token] (None when the account has no entry)
    token_map: dict[int, list[dict[str, Any] | None]] = {}
    for entry in pre_tokens:
        token_map.setdefault(int(entry.get("accountIndex", -1)), [None, None])[0] = entry
    for entry in post_tokens:
        token_map.setdefault(int(entry.get("accountIndex", -1)), [None, None])[1] = entry

    out: list[dict[str, Any]] = []
    for idx, pubkey in enumerate(keys):
        native_pre = int(pre[idx]) if idx < len(pre) else 0
        native_post = int(post[idx]) if idx < len(post) else 0
        token_changes: list[dict[str, Any]] = []
        pre_entry, post_entry = token_map.get(idx, (None, None))
        if pre_entry or post_entry:
            base_entry: dict[str, Any] = post_entry or pre_entry or {}
            mint = str(base_entry.get("mint", ""))
            owner = str(base_entry.get("owner", ""))
            pre_amount = int((pre_entry or {}).get("uiTokenAmount", {}).get("amount", "0"))
            post_amount = int((post_entry or {}).get("uiTokenAmount", {}).get("amount", "0"))
            if mint and (pre_amount != post_amount or pre_entry != post_entry):
                token_changes.append(
                    {
                        "mint": mint,
                        "owner": owner,
                        "rawTokenAmount": {"tokenAmount": str(post_amount - pre_amount)},
                        "decimals": base_entry.get("uiTokenAmount", {}).get("decimals", 0),
                    }
                )
        native_delta = native_post - native_pre
        if native_delta != 0 or token_changes:
            out.append(
                {
                    "account": pubkey,
                    "nativeBalanceChange": native_delta,
                    "tokenBalanceChanges": token_changes,
                }
            )
    return out


def _sol_token_transfers(tx: dict[str, Any], instructions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract normalized token transfers from parsed SPL instructions."""
    transfers: list[dict[str, Any]] = []
    for instr in instructions:
        parsed = instr.get("parsed", {})
        if instr.get("program") not in ("spl-token", "spl-token-2022"):
            continue
        transfer = _sol_parse_spl_transfer(tx, parsed)
        if transfer is None:
            continue
        transfers.append(transfer)
    return transfers


def _sol_native_transfers(tx: dict[str, Any], instructions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract native SOL transfers from system program instructions."""
    transfers: list[dict[str, Any]] = []
    for instr in instructions:
        if instr.get("program") != "system":
            continue
        transfer = _sol_parse_system_transfer(instr.get("parsed", {}))
        if transfer is None:
            continue
        transfers.append(transfer)
    return transfers


def _sol_classify(
    instructions: list[dict[str, Any]],
    token_transfers: list[dict[str, Any]],
    status: str,
) -> str:
    """Classify a Solana transaction into a Helius-style type."""
    if status == "failed":
        return TX_UNKNOWN
    program_ids = {i.get("programId", "") for i in instructions}
    if program_ids & SWAP_PROGRAM_IDS:
        return TX_SWAP
    if program_ids & NFT_MINT_PROGRAM_IDS:
        # Metaplex programs can mint or sell; default to mint unless we see a
        # non-fungible transfer already in flight (decimals == 0).
        if any(t.get("decimals") == 0 for t in token_transfers):
            return TX_NFT_SALE
        return TX_NFT_MINT
    if token_transfers:
        return TX_TRANSFER
    return TX_UNKNOWN


def _sol_description(
    tx_type: str,
    token_transfers: list[dict[str, Any]],
    native_transfers: list[dict[str, Any]],
    signature: str,
) -> str:
    """Build a human-readable summary (Helius ``description`` field)."""
    if tx_type == TX_SWAP:
        return f"Swap transaction {signature[:12]}… involving {len(token_transfers)} token transfer(s)"
    if token_transfers:
        t = token_transfers[0]
        dest = t.get("destination", "")[:8]
        amt = t.get("uiAmount", 0)
        return f"{amt:g} of {t.get('mint', '')[:8]}… transferred to {dest}…"
    if native_transfers:
        n = native_transfers[0]
        amt = n.get("uiAmount", 0)
        return f"{amt:g} SOL transferred to {n.get('toUserAccount', '')[:8]}…"
    return f"Transaction {signature[:12]}…"


def decode_solana_transaction(tx: dict[str, Any] | None) -> dict[str, Any] | None:
    """Decode a Solana ``jsonParsed`` transaction into an enhanced payload.

    Accepts the raw ``result`` object from ``getTransaction`` /
    ``getParsedTransaction`` with ``encoding="jsonParsed"``. Returns None for
    empty/malformed input so providers can fail open.
    """
    if not tx or not isinstance(tx, dict) or tx.get("transaction") is None:
        return None

    meta = tx.get("meta", {}) or {}
    signature = ""
    signatures = tx.get("transaction", {}).get("signatures") or []
    if signatures:
        signature = str(signatures[0])

    instructions = _sol_iterate_instructions(tx)
    token_transfers = _sol_token_transfers(tx, instructions)
    native_transfers = _sol_native_transfers(tx, instructions)
    status = "success" if not meta.get("err") else "failed"
    tx_type = _sol_classify(instructions, token_transfers, status)

    fee_payer = ""
    keys = _sol_account_keys(tx)
    if keys:
        fee_payer = keys[0]

    return {
        "chain": CHAIN_SOLANA,
        "signature": signature,
        "slot": tx.get("slot"),
        "timestamp": tx.get("blockTime"),
        "status": status,
        "type": tx_type,
        "fee": meta.get("fee", 0),
        "fee_payer": fee_payer,
        "description": _sol_description(tx_type, token_transfers, native_transfers, signature),
        "tokenTransfers": token_transfers,
        "nativeTransfers": native_transfers,
        "accountData": _sol_balance_changes(tx),
        "instructions": [
            {
                "programId": i.get("programId"),
                "program": i.get("program"),
                "instructionType": i.get("parsed", {}).get("type"),
            }
            for i in instructions
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# EVM decoding
# ═══════════════════════════════════════════════════════════════════════


def _evm_hex_to_int(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return 0


def _evm_topic_to_address(topic: str | None) -> str:
    """Convert a 32-byte topics entry to a 20-byte checksum-less address."""
    if not topic or len(topic) < 42:
        return ""
    return "0x" + topic[-40:].lower()


def _is_evm_dex(address: str) -> bool:
    addr = (address or "").lower()
    return any(addr.startswith(prefix) for prefix in EVM_DEX_PREFIXES)


def _evm_transfer_from_log(log: dict[str, Any], chain: str) -> dict[str, Any] | None:
    """Decode one transfer log (ERC-20/721 Transfer or ERC-1155 TransferSingle).

    Returns a normalized tokenTransfer dict or None when the log is not a
    transfer event.
    """
    topics = log.get("topics") or []
    if not topics:
        return None
    topic0 = str(topics[0]).lower()

    if topic0 == TOPIC_ERC20_TRANSFER:
        if len(topics) < 3:
            return None
        return {
            "chain": chain,
            "mint": str(log.get("address", "")).lower(),
            "fromUserAccount": _evm_topic_to_address(str(topics[1])),
            "toUserAccount": _evm_topic_to_address(str(topics[2])),
            "amount": _evm_hex_to_int(log.get("data")),
            "uiAmount": _evm_hex_to_int(log.get("data")) / 1e18,
            "tokenStandard": "NonFungible" if _evm_hex_to_int(log.get("data")) == 1 else "Fungible",
            "logIndex": _evm_hex_to_int(log.get("logIndex")),
        }
    if topic0 == TOPIC_ERC1155_TRANSFER_SINGLE:
        if len(topics) < 4:
            return None
        return {
            "chain": chain,
            "mint": str(log.get("address", "")).lower(),
            "fromUserAccount": _evm_topic_to_address(str(topics[1])),
            "toUserAccount": _evm_topic_to_address(str(topics[2])),
            "tokenId": _evm_hex_to_int(str(topics[3])),
            "amount": _evm_hex_to_int(log.get("data")),
            "uiAmount": _evm_hex_to_int(log.get("data")),
            "tokenStandard": "NonFungible",
            "logIndex": _evm_hex_to_int(log.get("logIndex")),
        }
    return None


def _evm_classify(logs: list[dict[str, Any]], receipt: dict[str, Any]) -> str:
    """Classify an EVM transaction from its logs + receipt."""
    status = str(receipt.get("status", "0x1"))
    if status not in ("0x1", "1", "true", "True"):
        return TX_UNKNOWN

    to_addr = str(receipt.get("to", "") or "").lower()
    if _is_evm_dex(to_addr) or any(_is_evm_dex(str(log.get("address", ""))) for log in logs):
        return TX_SWAP

    for log in logs:
        topics = log.get("topics") or []
        if topics and str(topics[0]).lower() in (
            TOPIC_ERC20_TRANSFER,
            TOPIC_ERC1155_TRANSFER_SINGLE,
        ):
            return TX_TRANSFER
    return TX_UNKNOWN


def decode_evm_receipt(receipt: dict[str, Any] | None, chain: str = "ethereum") -> dict[str, Any] | None:
    """Decode an EVM transaction receipt into an enhanced payload.

    Accepts the raw ``result`` object from ``eth_getTransactionReceipt``.
    ERC-20/721/1155 transfer logs become ``tokenTransfers``; known router
    ``to`` addresses classify the tx as a swap.
    """
    if not receipt or not isinstance(receipt, dict):
        return None

    logs = receipt.get("logs") or []
    token_transfers: list[dict[str, Any]] = []
    for log in logs:
        transfer = _evm_transfer_from_log(log, chain)
        if transfer is not None:
            token_transfers.append(transfer)

    tx_type = _evm_classify(logs, receipt)
    tx_hash = str(receipt.get("transactionHash", ""))
    fee_payer = str(receipt.get("from", ""))
    status = "success" if str(receipt.get("status", "0x1")) in ("0x1", "1", "true", "True") else "failed"

    description = ""
    if tx_type == TX_SWAP:
        description = f"Swap on {chain} involving {len(token_transfers)} token transfer(s)"
    elif token_transfers:
        t = token_transfers[0]
        description = (
            f"{t.get('uiAmount', 0):g} of {t.get('mint', '')[:10]}… "
            f"from {t.get('fromUserAccount', '')[:8]}… to {t.get('toUserAccount', '')[:8]}…"
        )
    else:
        description = f"Transaction {tx_hash[:12]}…"

    return {
        "chain": chain,
        "signature": tx_hash,
        "block_number": str(receipt.get("blockNumber", "")),
        "timestamp": _evm_hex_to_int(receipt.get("blockNumber")),
        "status": status,
        "type": tx_type,
        "fee": _evm_hex_to_int(receipt.get("gasUsed")) * _evm_hex_to_int(receipt.get("effectiveGasPrice")),
        "fee_payer": fee_payer,
        "description": description,
        "tokenTransfers": token_transfers,
        "nativeTransfers": [],
        "accountData": [],
        "instructions": [],
    }


__all__ = [
    "CHAIN_EVM",
    "CHAIN_SOLANA",
    "EVM_DEX_PREFIXES",
    "SWAP_PROGRAM_IDS",
    "TOPIC_ERC20_TRANSFER",
    "TOPIC_ERC1155_TRANSFER_SINGLE",
    "TX_LIQUIDITY",
    "TX_NFT_BURN",
    "TX_NFT_MINT",
    "TX_NFT_SALE",
    "TX_SWAP",
    "TX_TRANSFER",
    "TX_UNKNOWN",
    "decode_evm_receipt",
    "decode_solana_transaction",
]
