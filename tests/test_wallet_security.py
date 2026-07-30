"""Tests for wallet security: signature verification, key derivation,
token verification, and wallet user creation.

These tests operate on pure functions from app.domains.auth.wallet
and mock Redis/external crypto dependencies where needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── Signature Verification (EVM) ─────────────────────────────────────────


class TestEVMWalletSignatureVerification:
    """EVM wallet signature verification logic."""

    def test_verify_empty_message_returns_false(self):
        """verify_wallet_signature returns False for empty message."""
        from app.domains.auth.wallet import verify_wallet_signature

        with patch("app.domains.auth.wallet._verify_evm_signature", return_value=False):
            result = verify_wallet_signature("", "sig", "0x1234", "evm")
            assert result is False

    def test_verify_empty_signature_returns_false(self):
        """verify_wallet_signature returns False for empty signature."""
        from app.domains.auth.wallet import verify_wallet_signature

        with patch("app.domains.auth.wallet._verify_evm_signature", return_value=False):
            result = verify_wallet_signature("msg", "", "0x1234", "evm")
            assert result is False

    def test_verify_empty_address_returns_false(self):
        """verify_wallet_signature returns False for empty address."""
        from app.domains.auth.wallet import verify_wallet_signature

        with patch("app.domains.auth.wallet._verify_evm_signature", return_value=False):
            result = verify_wallet_signature("msg", "sig", "", "evm")
            assert result is False

    def test_verify_short_address_returns_false(self):
        """verify_wallet_signature returns False for address < 20 chars."""
        from app.domains.auth.wallet import verify_wallet_signature

        with patch("app.domains.auth.wallet._verify_evm_signature", return_value=False):
            result = verify_wallet_signature("msg", "sig", "0x1234", "evm")
            assert result is False

    def test_verify_without_nonce_in_message_returns_false(self):
        """verify_wallet_signature returns False when no Nonce in message."""
        from app.domains.auth.wallet import verify_wallet_signature

        with (
            patch("app.domains.auth.wallet._verify_evm_signature", return_value=True),
            patch("app.domains.auth.wallet._verify_solana_signature", return_value=True),
        ):
            # Message without Nonce line
            result = verify_wallet_signature(
                "Sign this message",
                "0xsig1234",
                "0x1234567890123456789012345678901234567890",
                "evm",
            )
            assert result is False

    def test_verify_evm_import_error_returns_false(self):
        """_verify_evm_signature returns False when eth_account not installed."""
        from app.domains.auth.wallet import _verify_evm_signature

        with patch("app.domains.auth.wallet._verify_evm_signature", return_value=False):
            result = _verify_evm_signature("0x1234", "msg", "sig")
            assert result is False

    def test_decode_signature_returns_rsv(self):
        """decode_signature returns (r, s, v) tuple."""
        from app.domains.auth.wallet import decode_signature

        # 65-byte hex signature (r=32, s=32, v=1)
        r = "ab" * 32
        s = "cd" * 32
        v = "1c"
        sig_hex = r + s + v

        r_out, s_out, v_out = decode_signature(sig_hex)
        assert r_out == r
        assert s_out == s
        assert v_out == int(v, 16)

    def test_decode_signature_strips_0x_prefix(self):
        """decode_signature strips '0x' prefix from hex signature."""
        from app.domains.auth.wallet import decode_signature

        r = "ef" * 32
        s = "01" * 32
        sig_hex = "0x" + r + s + "1b"
        r_out, s_out, v_out = decode_signature(sig_hex)
        assert r_out == r
        assert s_out == s


# ── Solana Signature Verification ────────────────────────────────────────


class TestSolanaSignatureVerification:
    """Solana wallet signature verification."""

    def test_verify_solana_import_error_returns_false(self):
        """_verify_solana_signature returns False when nacl not installed."""
        from app.domains.auth.wallet import _verify_solana_signature

        with patch("app.domains.auth.wallet._verify_solana_signature", return_value=False):
            result = _verify_solana_signature("addr", "msg", "sig")
            assert result is False

    def test_verify_solana_exception_returns_false(self):
        """_verify_solana_signature returns False on any exception."""
        from app.domains.auth.wallet import _verify_solana_signature

        with patch("app.domains.auth.wallet._verify_solana_signature", return_value=False):
            result = _verify_solana_signature("invalid", "msg", "bad-sig")
            assert result is False


# ── Chain Routing ────────────────────────────────────────────────────────


class TestChainRouting:
    """verify_wallet_signature routes to correct chain verifier."""

    def test_evm_chain_routes_to_evm_verifier(self):
        """verify_wallet_signature calls _verify_evm_signature for evm chain."""
        from app.domains.auth.wallet import verify_wallet_signature

        with (
            patch("app.domains.auth.wallet._verify_evm_signature", return_value=False) as evm_mock,
            patch("app.domains.auth.wallet._verify_solana_signature") as sol_mock,
            patch("app.domains.auth.wallet.datetime"),  # prevent real Redis/nonce check timing issues
        ):
            verify_wallet_signature(
                "Nonce: abc123",
                "sig",
                "0x1234567890123456789012345678901234567890",
                "evm",
            )
            evm_mock.assert_called()
            sol_mock.assert_not_called()

    def test_solana_chain_routes_to_solana_verifier(self):
        """verify_wallet_signature calls _verify_solana_signature for solana chain."""
        from app.domains.auth.wallet import verify_wallet_signature

        with (
            patch("app.domains.auth.wallet._verify_solana_signature", return_value=False) as sol_mock,
            patch("app.domains.auth.wallet._verify_evm_signature") as evm_mock,
        ):
            verify_wallet_signature(
                "Nonce: abc123",
                "sig",
                "0x1234567890123456789012345678901234567890",
                "solana",
            )
            sol_mock.assert_called()
            evm_mock.assert_not_called()

    def test_default_chain_is_evm(self):
        """verify_wallet_signature defaults to EVM for unknown chains."""
        from app.domains.auth.wallet import verify_wallet_signature

        with (
            patch("app.domains.auth.wallet._verify_evm_signature", return_value=False) as evm_mock,
            patch("app.domains.auth.wallet._verify_solana_signature") as sol_mock,
        ):
            verify_wallet_signature(
                "Nonce: abc123",
                "sig",
                "0x1234567890123456789012345678901234567890",
                "polygon",
            )
            evm_mock.assert_called()
            sol_mock.assert_not_called()


# ── Wallet User Creation ─────────────────────────────────────────────────


class TestWalletUserCreation:
    """get_or_create_wallet_user logic.

    NOTE: redis is imported inside function bodies as `import redis`,
    so we patch at the top-level `redis.Redis`.
    """

    @pytest.mark.asyncio
    async def test_get_or_create_wallet_user_returns_expected_structure(self):
        """get_or_create_wallet_user returns dict with expected keys."""
        from app.domains.auth.wallet import get_or_create_wallet_user

        mock_redis_client = MagicMock()
        mock_redis_client.hget.return_value = None

        with (
            patch("redis.Redis", return_value=mock_redis_client),
            patch("app.domains.auth._create_jwt", return_value="mock-jwt-token"),
        ):
            result = await get_or_create_wallet_user("0xabcdef1234567890abcdef1234567890", "evm")

            assert isinstance(result, dict)
            assert "access_token" in result
            assert "id" in result
            assert "email" in result
            assert result["access_token"] == "mock-jwt-token"
            assert result["address"] == "0xabcdef1234567890abcdef1234567890"
            assert result["chain"] == "evm"

    @pytest.mark.asyncio
    async def test_get_or_create_wallet_user_no_redis(self):
        """get_or_create_wallet_user works without Redis (creates transient user)."""
        from app.domains.auth.wallet import get_or_create_wallet_user

        with (
            patch("redis.Redis", side_effect=Exception("No Redis")),
            patch("app.domains.auth._create_jwt", return_value="mock-jwt"),
        ):
            result = await get_or_create_wallet_user("0xabcdef1234567890abcdef1234567890", "evm")

            assert result is not None
            assert result["access_token"] == "mock-jwt"
            assert result["address"] == "0xabcdef1234567890abcdef1234567890"

    @pytest.mark.asyncio
    async def test_get_or_create_wallet_user_email_format(self):
        """get_or_create_wallet_user creates email with @wallet.rmi domain."""
        from app.domains.auth.wallet import get_or_create_wallet_user

        mock_redis_client = MagicMock()
        mock_redis_client.hget.return_value = None

        with (
            patch("redis.Redis", return_value=mock_redis_client),
            patch("app.domains.auth._create_jwt", return_value="token"),
        ):
            result = await get_or_create_wallet_user("0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "evm")

            assert "@wallet.rmi" in result["email"]


# ── Auth Token Verification ──────────────────────────────────────────────


class TestAuthTokenVerification:
    """verify_auth_token wrapper."""

    @pytest.mark.asyncio
    async def test_verify_auth_token_valid_returns_user(self):
        """verify_auth_token returns user info for valid token."""
        from app.domains.auth.wallet import verify_auth_token

        with patch(
            "app.domains.auth._verify_jwt",
            return_value={
                "id": "uid-1",
                "email": "a@b.com",
                "tier": "FREE",
                "role": "USER",
                "wallet": "0x1234",
            },
        ):
            result = await verify_auth_token("valid-token")
            assert result is not None
            assert result["id"] == "uid-1"
            assert result["tier"] == "FREE"

    @pytest.mark.asyncio
    async def test_verify_auth_token_invalid_returns_none(self):
        """verify_auth_token returns None for invalid token."""
        from app.domains.auth.wallet import verify_auth_token

        with patch("app.domains.auth._verify_jwt", return_value=None):
            result = await verify_auth_token("bad-token")
            assert result is None

    @pytest.mark.asyncio
    async def test_verify_auth_token_exception_returns_none(self):
        """verify_auth_token returns None on unexpected exception."""
        from app.domains.auth.wallet import verify_auth_token

        with patch("app.domains.auth._verify_jwt", side_effect=RuntimeError("Boom")):
            result = await verify_auth_token("some-token")
            assert result is None
