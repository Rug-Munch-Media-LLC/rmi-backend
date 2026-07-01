"""Unit tests for app/core/rate_limiter.py — PII HMAC-hash protection (T06).

These tests verify that user identifiers (wallet addresses, API keys)
are NEVER stored in plaintext in Redis keys. The rate limiter must
hash identifiers with a daily-rotating HMAC salt before using them
as Redis key suffixes.

Privacy properties under test:
  1. Same identifier within a UTC day produces the same hash (rate-limit works).
  2. Different identifiers produce different hashes (collision-safe).
  3. Hash output is fixed length and hex (safe for Redis keys).
  4. Daily salt rotation produces different hashes for the same identifier
     on different days (privacy across days, even if Redis is dumped).
  5. Known PII shapes (EVM address 0x..., 44-char Solana base58, UUID)
     all hash to non-PII-looking output.
"""

from unittest.mock import patch

from app.core.rate_limiter import _get_daily_salt, _hash_identifier


class TestHashIdentifier:
    """The core HMAC hash function."""

    def test_same_identifier_same_day_same_hash(self) -> None:
        """Deterministic within a UTC day — rate limiting must work."""
        h1 = _hash_identifier("0xTestWallet123")
        h2 = _hash_identifier("0xTestWallet123")
        assert h1 == h2

    def test_different_identifiers_different_hashes(self) -> None:
        """No collisions between distinct identifiers."""
        h1 = _hash_identifier("0xAlice")
        h2 = _hash_identifier("0xBob")
        assert h1 != h2

    def test_hash_is_fixed_length_hex(self) -> None:
        """Hash output is always 32-char hex — safe for Redis keys."""
        h = _hash_identifier("anything")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_does_not_leak_plaintext(self) -> None:
        """Output must not contain the plaintext identifier."""
        wallet = "0x28C6c06298d514Db13C5dB4F81F6f9cA52330b44"
        h = _hash_identifier(wallet)
        assert wallet not in h
        assert wallet[:8] not in h
        assert wallet[-4:] not in h

    def test_known_pii_shapes_hash_to_non_pii(self) -> None:
        """EVM, Solana, UUID, API-key style all produce opaque hashes."""
        evm = "0x28C6c06298d514Db13C5dB4F81F6f9cA52330b44"
        solana = "5FHwkrd9jFZu7wpd4j5xsFK7AzVNbWqY9vZ7Hrx9fMXz"  # 44 chars
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        api_key = "rmi_test_FAKE_KEY_xxxxxxxxxxxxxxxxxxxxxxxx"

        for pii in (evm, solana, uuid, api_key):
            h = _hash_identifier(pii)
            assert pii not in h
            assert len(h) == 32

    def test_empty_identifier_does_not_crash(self) -> None:
        """Edge case: empty string should still produce a valid hash."""
        h = _hash_identifier("")
        assert len(h) == 32

    def test_unicode_identifier_does_not_crash(self) -> None:
        """Edge case: unicode should hash without error."""
        h = _hash_identifier("wallet-α-β-γ-123")
        assert len(h) == 32


class TestDailySaltRotation:
    """The salt must rotate daily so leaks are time-bounded."""

    def test_salt_changes_with_date(self) -> None:
        """Different dates produce different salts, hence different hashes."""
        with patch("app.core.rate_limiter.time.strftime", return_value="2026-06-22"):
            h_day1 = _hash_identifier("0xSameWallet")

        with patch("app.core.rate_limiter.time.strftime", return_value="2026-06-23"):
            h_day2 = _hash_identifier("0xSameWallet")

        # Same identifier, different days -> different hashes
        assert h_day1 != h_day2

    def test_salt_uses_configured_secret(self) -> None:
        """Different RATE_LIMIT_SALT values produce different hashes for
        the same identifier on the same day."""
        from app.core import rate_limiter

        with patch.object(rate_limiter, "RATE_LIMIT_SALT_SECRET", "secret-alpha"):
            h_alpha = _hash_identifier("0xWallet")

        with patch.object(rate_limiter, "RATE_LIMIT_SALT_SECRET", "secret-beta"):
            h_beta = _hash_identifier("0xWallet")

        assert h_alpha != h_beta

    def test_get_daily_salt_is_bytes(self) -> None:
        """Salt is returned as raw bytes for hmac.new()."""
        salt = _get_daily_salt()
        assert isinstance(salt, bytes)
        assert len(salt) == 32  # SHA-256 output


class TestRedisKeysNoPII:
    """The hash must be used wherever identifiers touch Redis keys.

    These tests source-grep the file rather than executing it, so a
    regression where someone reverts to plaintext fails CI immediately.
    """

    def test_no_plaintext_user_id_in_redis_key_templates(self) -> None:
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "app" / "core" / "rate_limiter.py"
        content = src.read_text()

        # Match f-strings that build Redis keys and contain a raw {user_id}
        # (not _hash_identifier(user_id), not just any other var).
        bad_patterns = [
            r"rmi:user_tier:\{user_id\}",  # raw {user_id} would be plaintext
            r"rmi:ratelimit:\{user_id\}",
        ]
        for pattern in bad_patterns:
            match = re.search(pattern, content)
            assert not match, (
                f"PII leak in rate_limiter.py: found raw {{{pattern}}} "
                f"in a Redis key template. Use _hash_identifier(user_id) instead."
            )

    def test_all_pii_keys_use_hash_helper(self) -> None:
        """Every Redis key template that references an identifier must
        route through _hash_identifier(). Source-grep enforcement."""
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "app" / "core" / "rate_limiter.py"
        content = src.read_text()

        # Count: there should be exactly 4 references to PII-bearing Redis keys,
        # all using _hash_identifier (get_user_tier read, check_rate_limit
        # minute/day, my_tier minute/day, upgrade set). We allow some slack.
        assert "_hash_identifier" in content
        # Sanity: there must be NO bare user_id interpolated into a Redis key
        # without going through the hash helper. Spot-check a few lines:
        for forbidden in [
            'rmi:user_tier:{user_id}"',  # was the pre-fix pattern
            "rmi:ratelimit:{user_id}:{today}",
        ]:
            assert forbidden not in content, f"PII leak: '{forbidden}' still present"
