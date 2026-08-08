"""Tests for the enhanced-transaction decoder + provider (Helius-style).

Covers:
  1. ``decode_solana_transaction`` — jsonParsed tx → enhanced payload
  2. ``decode_evm_receipt`` — ERC-20/721/1155 logs → enhanced payload
  3. ``EnhancedTransactionProvider`` — polling, watch injection, fail-open
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.enhanced_transaction import EnhancedTransactionProvider
from app.domains.scanner.webhooks.providers.tx_decoder import (
    TX_NFT_MINT,
    TX_SWAP,
    TX_TRANSFER,
    TX_UNKNOWN,
    decode_evm_receipt,
    decode_solana_transaction,
)

# ═══════════════════════════════════════════════════════════════════════
# Solana decoder
# ═══════════════════════════════════════════════════════════════════════


def _sol_tx(**overrides) -> dict:
    """Minimal realistic jsonParsed transaction fixture."""
    tx = {
        "blockTime": 1673445241,
        "slot": 171942732,
        "meta": {
            "err": None,
            "fee": 10000,
            "preBalances": [1000000000, 0, 0, 0, 0],
            "postBalances": [900000000, 100000000, 0, 0, 0],
            "preTokenBalances": [
                {
                    "accountIndex": 3,
                    "mint": "FdsNQE5EeCe57tbEYCRV1JwW5dzNCof7MUTaGWhmzYqu",
                    "owner": "11111111111111111111111111111111",
                    "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "uiTokenAmount": {"amount": "1000000", "decimals": 6, "uiAmount": 1.0},
                }
            ],
            "postTokenBalances": [
                {
                    "accountIndex": 4,
                    "mint": "FdsNQE5EeCe57tbEYCRV1JwW5dzNCof7MUTaGWhmzYqu",
                    "owner": "11111111111111111111111111111111",
                    "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "uiTokenAmount": {"amount": "1000000", "decimals": 6, "uiAmount": 1.0},
                }
            ],
            "innerInstructions": [],
        },
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": "11111111111111111111111111111111", "signer": True, "writable": True},
                    {"pubkey": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "signer": False, "writable": False},
                    {"pubkey": "FdsNQE5EeCe57tbEYCRV1JwW5dzNCof7MUTaGWhmzYqu", "signer": False, "writable": False},
                    {"pubkey": "25DTUAd1roBFoUQaxJQByL6Qy2cKQCBp4bK9sgfy9UiM", "signer": False, "writable": True},
                    {"pubkey": "DTYuh7gAGGZg2okM7hdFfU1yMY9LUemCiPyD5Z5GCs6Z", "signer": False, "writable": True},
                ],
                "instructions": [
                    {
                        "programIdIndex": 1,
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "source": "25DTUAd1roBFoUQaxJQByL6Qy2cKQCBp4bK9sgfy9UiM",
                                "destination": "DTYuh7gAGGZg2okM7hdFfU1yMY9LUemCiPyD5Z5GCs6Z",
                                "authority": "11111111111111111111111111111111",
                                "amount": "1000000",
                                "decimals": 6,
                            },
                        },
                        "program": "spl-token",
                        "data": "3Bxs3zs3x6pg4XWo",
                    }
                ],
                "recentBlockhash": "3NRncb7FJuDruQjMDxnHvJBQkvkHa7KSUBqBsxG21roZ",
            },
            "signatures": ["5nNtjezQMYBHvgSQmoRmJPiXGsPAWmJPoGSa64xanqrauogiVzFyGQhKeFataHGXq51jR2hjbzNTkPUpP787HAmL"],
        },
        "version": 0,
    }
    tx.update(overrides)
    return tx


class TestDecodeSolanaTransaction:
    def test_none_on_empty(self):
        assert decode_solana_transaction(None) is None
        assert decode_solana_transaction({}) is None
        assert decode_solana_transaction({"transaction": {}}) is not None

    def test_spl_transfer_payload(self):
        decoded = decode_solana_transaction(_sol_tx())
        assert decoded is not None
        assert decoded["chain"] == "solana"
        assert decoded["status"] == "success"
        assert decoded["type"] == TX_TRANSFER
        assert decoded["signature"].startswith("5nNtjezQ")
        assert decoded["fee"] == 10000
        assert len(decoded["tokenTransfers"]) == 1
        tt = decoded["tokenTransfers"][0]
        assert tt["mint"] == "FdsNQE5EeCe57tbEYCRV1JwW5dzNCof7MUTaGWhmzYqu"
        assert tt["amount"] == 1000000
        assert tt["uiAmount"] == pytest.approx(1.0)
        assert decoded["description"]

    def test_failed_status(self):
        tx = _sol_tx()
        tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        decoded = decode_solana_transaction(tx)
        assert decoded["status"] == "failed"

    def test_swap_classification(self):
        tx = _sol_tx()
        # Add a Jupiter swap instruction on top of the SPL transfer
        tx["transaction"]["message"]["instructions"].append(
            {
                "programId": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
                "parsed": {"type": "route", "info": {}},
                "program": "jupiter-v6",
                "data": "deadbeef",
            }
        )
        decoded = decode_solana_transaction(tx)
        assert decoded["type"] == TX_SWAP

    def test_nft_mint_classification(self):
        tx = _sol_tx()
        tx["transaction"]["message"]["instructions"].append(
            {
                "programId": "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
                "parsed": {"type": "createMetadataAccountV3", "info": {}},
                "program": "metaplex",
                "data": "deadbeef",
            }
        )
        decoded = decode_solana_transaction(tx)
        assert decoded["type"] == TX_NFT_MINT

    def test_account_balance_deltas(self):
        decoded = decode_solana_transaction(_sol_tx())
        assert decoded is not None
        deltas = {d["account"]: d["nativeBalanceChange"] for d in decoded["accountData"]}
        assert deltas["11111111111111111111111111111111"] == -100000000
        assert deltas["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"] == 100000000


# ═══════════════════════════════════════════════════════════════════════
# EVM decoder
# ═══════════════════════════════════════════════════════════════════════


def _evm_receipt(**overrides) -> dict:
    """Minimal EVM receipt fixture with an ERC-20 transfer log."""
    receipt = {
        "transactionHash": "0xabc123",
        "from": "0xaaa",
        "to": "0xbbb",
        "status": "0x1",
        "blockNumber": "0x10a4f2",
        "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00",
        "logs": [
            {
                "address": "0xToken",
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "0x000000000000000000000000bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                ],
                "data": "0x0000000000000000000000000000000000000000000000000de0b6b3a7640000",
                "logIndex": "0x0",
            }
        ],
    }
    receipt.update(overrides)
    return receipt


class TestDecodeEvmReceipt:
    def test_none_on_empty(self):
        assert decode_evm_receipt(None) is None

    def test_erc20_transfer_payload(self):
        decoded = decode_evm_receipt(_evm_receipt())
        assert decoded is not None
        assert decoded["chain"] == "ethereum"
        assert decoded["type"] == TX_TRANSFER
        assert decoded["signature"] == "0xabc123"
        assert len(decoded["tokenTransfers"]) == 1
        tt = decoded["tokenTransfers"][0]
        assert tt["mint"] == "0xtoken"
        assert tt["fromUserAccount"] == "0x" + "a" * 40
        assert tt["toUserAccount"] == "0x" + "b" * 40
        assert tt["amount"] == 10**18  # 1 ETH-decimal token
        assert tt["tokenStandard"] == "Fungible"

    def test_swap_classification_by_router(self):
        receipt = _evm_receipt(to="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")  # Uniswap V2
        decoded = decode_evm_receipt(receipt)
        assert decoded["type"] == TX_SWAP

    def test_unknown_when_failed(self):
        receipt = _evm_receipt(status="0x0")
        decoded = decode_evm_receipt(receipt)
        assert decoded["type"] == TX_UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# Provider
# ═══════════════════════════════════════════════════════════════════════


class TestEnhancedTransactionProvider:
    def test_event_type(self):
        assert EnhancedTransactionProvider.event_type == WebhookEventType.TRANSACTION_ENHANCED

    def test_poll_interval(self):
        assert EnhancedTransactionProvider.poll_interval == 30

    async def test_cursor_key(self):
        provider = EnhancedTransactionProvider()
        assert await provider.cursor_key() == "webhook:cursor:enhanced_transaction"

    async def test_poll_no_watch_targets(self):
        provider = EnhancedTransactionProvider()
        assert await provider.poll({}) == []
        assert await provider.poll(None) == []

    async def test_poll_solana_dedup_and_parse(self):
        """Provider should decode transactions, dedupe signatures, skip junk."""
        provider = EnhancedTransactionProvider()
        provider._helius_key = "test-key"
        provider._solana_rpc = "https://mainnet.helius-rpc.com/?api-key=test-key"

        sig = "5nNtjezQMYBHvgSQmoRmJPiXGsPAWmJPoGSa64xanqrauogiVzFyGQhKeFataHGXq51jR2hjbzNTkPUpP787HAmL"
        decoded_tx = _sol_tx()

        mock_resp_sigs = MagicMock()
        mock_resp_sigs.json.return_value = {"result": [{"signature": sig}]}
        mock_resp_tx = MagicMock()
        mock_resp_tx.json.return_value = {"result": decoded_tx}

        mock_client = AsyncMock()
        mock_client.post.side_effect = [mock_resp_sigs, mock_resp_tx]

        with patch(
            "httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client), __aexit__=AsyncMock(return_value=False)
            ),
        ):
            cursor = {"wallet_addresses": ["11111111111111111111111111111111"]}
            events = await provider.poll(cursor)

        assert len(events) == 1
        assert events[0]["chain"] == "solana"
        assert events[0]["type"] == TX_TRANSFER
        assert events[0]["wallet_address"] == "11111111111111111111111111111111"
        assert events[0]["token_address"] == "fdsnqe5eece57tbeycrv1jww5dzncof7mutagwhmzyqu"  # lowercased
        # seen ring persisted
        assert sig in cursor["seen_sigs"]

    async def test_poll_solana_rpc_error_fail_open(self):
        """RPC failures should not raise — poll returns partial/empty."""
        provider = EnhancedTransactionProvider()
        provider._helius_key = ""
        provider._solana_rpc = "https://api.mainnet-beta.solana.com"

        mock_client = AsyncMock()
        mock_client.post.side_effect = RuntimeError("rpc down")

        with patch(
            "httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_client), __aexit__=AsyncMock(return_value=False)
            ),
        ):
            events = await provider.poll({"wallet_addresses": ["abc123"]})

        assert events == []

    async def test_evm_skipped_without_alchemy_key(self):
        """Without ALCHEMY_API_KEY the EVM path logs and returns no events."""
        provider = EnhancedTransactionProvider()
        provider._alchemy_key = ""
        provider._helius_key = ""
        events = await provider.poll({"wallet_addresses": ["0xabc"]})
        assert events == []

    def test_build_event_skips_unknown_noop(self):
        provider = EnhancedTransactionProvider()
        assert provider._build_event({}, "0xabc") is None
        noop = {"chain": "solana", "type": "UNKNOWN", "tokenTransfers": [], "nativeTransfers": []}
        assert provider._build_event(noop, "0xabc") is None
        swap = {"chain": "solana", "type": "SWAP", "tokenTransfers": [{"mint": "0xMint"}], "nativeTransfers": []}
        event = provider._build_event(swap, "0xabc")
        assert event is not None
        assert event["token_address"] == "0xmint"
        assert event["wallet_address"] == "0xabc"
