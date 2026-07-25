"""
Tests: Web3 Chat Service & Router
===================================
Tests for payment processing, wallet integration, token/NFT gate verification,
and command parsing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.web3_chat import (
    SUPPORTED_CHAINS,
    SUPPORTED_TOKENS,
    TOKEN_ADDRESSES,
    GateType,
    PaymentRequest,
    PaymentStatus,
    TokenGate,
    generate_nonce,
    get_store,
    parse_amount,
    validate_address,
    verify_payment_command,
)

# ── Fixtures ──


@pytest.fixture
def store():
    """Get a fresh web3 store for each test."""
    s = get_store()
    # Clear internal state
    s._payments.clear()
    s._tips.clear()
    s._splits.clear()
    s._token_gates.clear()
    s._nft_gates.clear()
    s._connected_wallets.clear()
    return s


# ── Address Validation ──


class TestValidateAddress:
    def test_valid_evm_address(self):
        assert validate_address("0x1234567890abcdef1234567890abcdef12345678", "ethereum")

    def test_invalid_evm_address_short(self):
        assert not validate_address("0x1234", "ethereum")

    def test_invalid_evm_address_no_prefix(self):
        assert not validate_address("1234567890abcdef1234567890abcdef12345678", "ethereum")

    def test_valid_solana_address(self):
        assert validate_address("DmDZ4CcSNqE5p4Uz6Lx9J4tcgxtLCBzPiGYPpoRbejgh", "solana")

    def test_invalid_solana_address_short(self):
        assert not validate_address("abc", "solana")

    def test_valid_lightning_address(self):
        assert validate_address(
            "lnbc1p0g5q0zpp5q2q3q4q5q6q7q8q9q0q1q2q3q4q5q6q7q8q9q0q1q2q3q4q5q6q7q8q9q0", "lightning"
        )
        assert validate_address("lnurl1dp68gurn8ghj7mr0vdskc6r0wd6z7mrww4excttsv9un7g9uh7mm", "lightning")


# ── Amount Parsing ──


class TestParseAmount:
    def test_valid_amount(self):
        assert parse_amount("0.1") == Decimal("0.1")
        assert parse_amount("100") == Decimal("100")
        assert parse_amount("0.000001") == Decimal("0.000001")

    def test_invalid_amount(self):
        with pytest.raises(ValueError, match="Invalid amount"):
            parse_amount("abc")
        with pytest.raises(ValueError, match="Invalid amount"):
            parse_amount("")


# ── Nonce Generation ──


class TestGenerateNonce:
    def test_nonce_format(self):
        nonce = generate_nonce("user123")
        assert len(nonce) == 24
        assert isinstance(nonce, str)

    def test_nonce_unique(self):
        n1 = generate_nonce("user123")
        n2 = generate_nonce("user123")
        assert n1 != n2


# ── Command Parsing ──


class TestVerifyPaymentCommand:
    def test_pay_command(self):
        result = verify_payment_command("/pay @alice 0.1 ETH")
        assert result is not None
        assert result["command"] == "pay"
        assert result["recipient"] == "alice"
        assert result["amount"] == "0.1"
        assert result["token"] == "ETH"

    def test_pay_command_no_at(self):
        result = verify_payment_command("/pay alice 0.1 ETH")
        assert result is not None
        assert result["recipient"] == "alice"

    def test_pay_missing_args(self):
        result = verify_payment_command("/pay @alice")
        assert result is not None
        assert "error" in result

    def test_tip_command(self):
        result = verify_payment_command("/tip @bob 50 USDC")
        assert result is not None
        assert result["command"] == "tip"
        assert result["recipient"] == "bob"
        assert result["amount"] == "50"
        assert result["token"] == "USDC"

    def test_split_command(self):
        result = verify_payment_command("/split 1 ETH @alice @bob @charlie")
        assert result is not None
        assert result["command"] == "split"
        assert result["amount"] == "1"
        assert result["token"] == "ETH"
        assert result["recipients"] == ["alice", "bob", "charlie"]

    def test_request_command(self):
        result = verify_payment_command("/request 0.1 ETH @alice for lunch")
        assert result is not None
        assert result["command"] == "request"
        assert result["amount"] == "0.1"
        assert result["token"] == "ETH"
        assert result["recipient"] == "alice"
        assert result["reason"] == "for lunch"

    def test_not_a_command(self):
        result = verify_payment_command("hello world")
        assert result is None

    def test_empty_string(self):
        result = verify_payment_command("")
        assert result is None


# ── Payment Store ──


class TestWeb3ChatStore:
    def test_save_and_get_payment(self, store):
        payment = PaymentRequest(
            sender_id="user1",
            recipient_username="user2",
            amount="0.5",
            token="ETH",
            chain="ethereum",
        )
        store.save_payment(payment)
        retrieved = store.get_payment(payment.id)
        assert retrieved is not None
        assert retrieved.amount == "0.5"
        assert retrieved.token == "ETH"

    def test_update_payment_status(self, store):
        payment = PaymentRequest(
            sender_id="user1",
            recipient_username="user2",
            amount="100",
            token="USDC",
            chain="base",
        )
        store.save_payment(payment)
        store.update_payment_status(payment.id, PaymentStatus.CONFIRMED, "0xabc123")
        updated = store.get_payment(payment.id)
        assert updated is not None
        assert updated.status == PaymentStatus.CONFIRMED
        assert updated.tx_hash == "0xabc123"

    def test_get_user_payments(self, store):
        p1 = PaymentRequest(sender_id="user1", recipient_username="user2", amount="1", token="ETH", chain="ethereum")
        p2 = PaymentRequest(sender_id="user1", recipient_username="user3", amount="2", token="SOL", chain="solana")
        p3 = PaymentRequest(sender_id="user4", recipient_username="user1", amount="3", token="USDC", chain="polygon")
        store.save_payment(p1)
        store.save_payment(p2)
        store.save_payment(p3)

        user1_payments = store.get_user_payments("user1")
        assert len(user1_payments) == 3  # sent 2, received 1

    def test_token_gate_crud(self, store):
        gate = TokenGate(
            room_id="room1",
            gate_type=GateType.TOKEN,
            token_address="0xtoken",
            token_symbol="$TICKER",
            chain="ethereum",
            min_amount="100",
        )
        store.save_token_gate(gate)

        retrieved = store.get_token_gate("room1")
        assert retrieved is not None
        assert retrieved.min_amount == "100"
        assert retrieved.token_symbol == "$TICKER"

        store.remove_gate("room1")
        assert store.get_token_gate("room1") is None

    def test_wallet_connection(self, store):
        connection = {"address": "0xabc", "chain": "ethereum", "wallet_type": "metamask", "connected_at": "now"}
        store.save_connection("user1", connection)

        retrieved = store.get_connection("user1")
        assert retrieved is not None
        assert retrieved["address"] == "0xabc"

        store.remove_connection("user1")
        assert store.get_connection("user1") is None


# ── Token/Chain Constants ──


class TestConstants:
    def test_supported_chains(self):
        assert "ethereum" in SUPPORTED_CHAINS
        assert "solana" in SUPPORTED_CHAINS
        assert "base" in SUPPORTED_CHAINS
        assert "lightning" in SUPPORTED_CHAINS

    def test_supported_tokens(self):
        assert SUPPORTED_TOKENS["ETH"]["decimals"] == 18
        assert SUPPORTED_TOKENS["SOL"]["decimals"] == 9
        assert SUPPORTED_TOKENS["USDC"]["decimals"] == 6
        assert SUPPORTED_TOKENS["USDT"]["decimals"] == 6
        assert SUPPORTED_TOKENS["DAI"]["decimals"] == 18

    def test_token_addresses(self):
        assert TOKEN_ADDRESSES["ethereum"]["USDC"] == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert TOKEN_ADDRESSES["solana"]["USDC"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    def test_chain_explorers(self):
        assert "https://etherscan.io/tx/" in SUPPORTED_CHAINS["ethereum"]["explorer"]
        assert "https://solscan.io/tx/" in SUPPORTED_CHAINS["solana"]["explorer"]
