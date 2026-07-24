"""Unit tests for app.spam_registry — SpamRegistry singleton and data loaders.

Tests cover:
- Registry initialization with empty state
- Manual data injection via _spam_tokens / _sanctioned_wallets
- Query methods: is_spam_token, is_sanctioned, is_known_scam, is_phishing_domain
- check_token full response
- filter_spam_tokens split logic
- stats reporting
- GoldRush file loading (mocked temp files)
- Scam Sniffer / OpenSanctions / Guardian / CryptoScamDB loaders with various JSON shapes
- seed_spam_data (mocked HTTP)
- get_spam_registry singleton
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.spam_registry import SpamRegistry, get_spam_registry, seed_spam_data


@pytest.fixture
def registry() -> SpamRegistry:
    """Return a fresh SpamRegistry with no data loaded."""
    reg = SpamRegistry()
    # Bypass file loading — inject directly
    reg._loaded = True
    reg._spam_tokens = {
        "solana": {"so11111111111111111111111111111111111111111"},
        "ethereum": {"0x" + "a" * 40},
    }
    reg._spam_nfts = {
        "solana": set(),
        "ethereum": {"0x" + "b" * 40},
    }
    reg._sanctioned_wallets = {"0xtornado" + "c" * 32, "0xsanctioned" + "d" * 31}
    reg._phishing_domains = {"phishy-site.xyz", "claim-your-airdrop.fake"}
    reg._scam_addresses = {"0xscammy" + "e" * 34, "0xevil" + "f" * 36}
    reg._compute_stats()
    return reg


class TestRegistryInitialization:
    """Empty registry state before loading."""

    def test_initial_empty_state(self) -> None:
        reg = SpamRegistry()
        assert reg._loaded is False
        assert reg._spam_tokens == {}
        assert reg._spam_nfts == {}
        assert reg._phishing_domains == set()
        assert reg._sanctioned_wallets == set()
        assert reg._scam_addresses == set()
        assert reg._stats == {"tokens": 0, "nfts": 0, "phishing": 0, "sanctioned": 0, "scam": 0}

    def test_load_is_idempotent(self) -> None:
        """Calling _ensure_loaded multiple times should not re-trigger file loads."""
        reg = SpamRegistry()
        with patch.object(reg, "_load_goldrush") as mock_goldrush:
            reg._ensure_loaded()
            reg._ensure_loaded()  # second call
            mock_goldrush.assert_called_once()


class TestQueryMethods:
    """Core query functions against injected data."""

    def test_is_spam_token_hit(self, registry: SpamRegistry) -> None:
        assert (
            registry.is_spam_token("So11111111111111111111111111111111111111111", "solana") is True
        )

    def test_is_spam_token_nft_hit(self, registry: SpamRegistry) -> None:
        assert registry.is_spam_token("0x" + "b" * 40, "ethereum") is True

    def test_is_spam_token_miss(self, registry: SpamRegistry) -> None:
        assert registry.is_spam_token("0xclean" + "c" * 33, "solana") is False

    def test_is_spam_token_checks_scam_addresses(self, registry: SpamRegistry) -> None:
        assert registry.is_spam_token("0xscammy" + "e" * 34, "solana") is True

    def test_is_spam_token_empty_chain(self, registry: SpamRegistry) -> None:
        """Unindexed chain should not cause errors."""
        assert registry.is_spam_token("0x" + "a" * 40, "nonexistent-chain") is False

    def test_is_sanctioned_hit(self, registry: SpamRegistry) -> None:
        assert registry.is_sanctioned("0xtornado" + "c" * 32) is True

    def test_is_sanctioned_miss(self, registry: SpamRegistry) -> None:
        assert registry.is_sanctioned("0xinnocent" + "a" * 31) is False

    def test_is_sanctioned_case_insensitive(self, registry: SpamRegistry) -> None:
        assert registry.is_sanctioned("0xTornado" + "C" * 32) is True

    def test_is_known_scam_hit(self, registry: SpamRegistry) -> None:
        assert registry.is_known_scam("0xscammy" + "e" * 34) is True

    def test_is_known_scam_miss(self, registry: SpamRegistry) -> None:
        assert registry.is_known_scam("0xlegit" + "a" * 35) is False

    def test_is_phishing_domain_hit(self, registry: SpamRegistry) -> None:
        assert registry.is_phishing_domain("phishy-site.xyz") is True

    def test_is_phishing_domain_miss(self, registry: SpamRegistry) -> None:
        assert registry.is_phishing_domain("google.com") is False

    def test_is_phishing_domain_case_insensitive(self, registry: SpamRegistry) -> None:
        assert registry.is_phishing_domain("PHISHY-SITE.XYZ") is True


class TestCheckToken:
    """Full check_token response."""

    def test_clean_token(self, registry: SpamRegistry) -> None:
        result = registry.check_token("0xclean" + "c" * 33, "solana")
        assert result["is_spam"] is False
        assert result["is_sanctioned"] is False
        assert result["is_known_scam"] is False
        assert result["address"] == "0xclean" + "c" * 33

    def test_spam_token(self, registry: SpamRegistry) -> None:
        result = registry.check_token("so11111111111111111111111111111111111111111", "solana")
        assert result["is_spam"] is True

    def test_sanctioned_token(self, registry: SpamRegistry) -> None:
        result = registry.check_token("0xtornado" + "c" * 32, "ethereum")
        assert result["is_sanctioned"] is True

    def test_known_scam_token(self, registry: SpamRegistry) -> None:
        result = registry.check_token("0xscammy" + "e" * 34, "ethereum")
        assert result["is_known_scam"] is True

    def test_sources_checked_list(self, registry: SpamRegistry) -> None:
        result = registry.check_token("0x" + "a" * 40)
        assert "goldrush" in result["sources_checked"]
        assert "scamsniffer" in result["sources_checked"]
        assert "opensanctions" in result["sources_checked"]
        assert "guardian" in result["sources_checked"]
        assert "cryptoscamdb" in result["sources_checked"]
        assert len(result["sources_checked"]) == 5


class TestFilterSpamTokens:
    """Splitting token lists into clean and spam."""

    def test_all_clean(self, registry: SpamRegistry) -> None:
        tokens = [
            {"address": "0xclean1" + "a" * 33},
            {"address": "0xclean2" + "b" * 33},
        ]
        clean, spam = registry.filter_spam_tokens(tokens, "solana")
        assert len(clean) == 2
        assert len(spam) == 0

    def test_all_spam(self, registry: SpamRegistry) -> None:
        tokens = [
            {"address": "so11111111111111111111111111111111111111111"},
            {"address": "0xscammy" + "e" * 34},
        ]
        clean, spam = registry.filter_spam_tokens(tokens, "solana")
        assert len(clean) == 0
        assert len(spam) == 2

    def test_mixed(self, registry: SpamRegistry) -> None:
        tokens = [
            {"address": "0xclean1" + "a" * 33},
            {"address": "so11111111111111111111111111111111111111111"},
        ]
        clean, spam = registry.filter_spam_tokens(tokens, "solana")
        assert len(clean) == 1
        assert len(spam) == 1

    def test_uses_contract_address_key(self, registry: SpamRegistry) -> None:
        tokens = [
            {"contract_address": "so11111111111111111111111111111111111111111"},
        ]
        clean, spam = registry.filter_spam_tokens(tokens, "solana")
        assert len(spam) == 1

    def test_empty_input(self, registry: SpamRegistry) -> None:
        clean, spam = registry.filter_spam_tokens([], "solana")
        assert clean == []
        assert spam == []

    def test_ignores_missing_address_key(self, registry: SpamRegistry) -> None:
        tokens = [{"name": "Some Token"}]
        clean, spam = registry.filter_spam_tokens(tokens, "solana")
        assert len(clean) == 1  # treated as clean, no address match
        assert len(spam) == 0


class TestStats:
    """Statistics reporting."""

    def test_stats_after_injection(self, registry: SpamRegistry) -> None:
        stats = registry.stats()
        assert stats["tokens"] == 2  # solana + ethereum
        assert stats["nfts"] == 1  # ethereum only
        assert stats["phishing"] == 2
        assert stats["sanctioned"] == 2
        assert stats["scam"] == 2
        assert stats["total"] == 9
        assert stats["chains_indexed"] == 2
        assert stats["loaded"] is True
        assert "updated" in stats

    def test_stats_empty_registry(self) -> None:
        reg = SpamRegistry()
        reg._loaded = True  # skip loading
        reg._compute_stats()
        stats = reg.stats()
        assert stats["total"] == 0
        assert stats["chains_indexed"] == 0


class TestFileLoaders:
    """Data loading from JSON cache files."""

    def test_goldrush_dict_format(self, registry: SpamRegistry) -> None:
        """GoldRush data as dict with 'erc20' and 'nft' keys."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"erc20": ["0xtoken1", "0xtoken2"], "nft": ["0xnft1"]}, f)
            temp_path = f.name

        reg = SpamRegistry()
        reg._load_goldrush = lambda: None  # skip actual loading
        # Manually test via temp file injection
        with patch("app.spam_registry.SPAM_DATA_DIR", Path(temp_path).parent):
            reg = SpamRegistry()
            reg.SPAM_DATA_DIR = Path(temp_path).parent  # type: ignore[assignment]
            # Direct test of caching logic
            chain = "ethereum"
            cache_file = Path(temp_path).parent / "goldrush_spam_ethereum.json"
            with open(cache_file, "w") as f:
                json.dump({"erc20": ["0xtoken1", "0xtoken2"], "nft": ["0xnft1"]}, f)
            reg._load_goldrush()
            assert "0xtoken1" in reg._spam_tokens.get("ethereum", set())
            assert "0xnft1" in reg._spam_nfts.get("ethereum", set())

        os.unlink(temp_path)

    def test_goldrush_list_format(self) -> None:
        """GoldRush data as a flat list of addresses."""
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "goldrush_spam_ethereum.json"
                with open(cache_file, "w") as f:
                    json.dump(["0xaddr1", "0xaddr2"], f)
                reg._load_goldrush()
                assert "0xaddr1" in reg._spam_tokens.get("ethereum", set())

    def test_scamsniffer_list_format(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "scamsniffer_blacklist.json"
                with open(cache_file, "w") as f:
                    json.dump(["0xscam1", "0xscam2"], f)
                reg._load_scamsniffer()
                assert "0xscam1" in reg._scam_addresses
                assert "0xscam2" in reg._scam_addresses

    def test_scamsniffer_dict_format(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "scamsniffer_blacklist.json"
                with open(cache_file, "w") as f:
                    json.dump({"addresses": ["0xscam1"]}, f)
                reg._load_scamsniffer()
                assert "0xscam1" in reg._scam_addresses

    def test_opensanctions_loading(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "opensanctions_crypto.json"
                with open(cache_file, "w") as f:
                    json.dump(
                        [
                            {"address": "0xsanctioned1"},
                            {"wallet": "0xsanctioned2"},
                            {"address": "0xsanctioned3"},
                        ],
                        f,
                    )
                reg._load_opensanctions()
                assert "0xsanctioned1" in reg._sanctioned_wallets
                assert "0xsanctioned2" in reg._sanctioned_wallets
                assert "0xsanctioned3" in reg._sanctioned_wallets

    def test_guardian_dict_format(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "guardian_phishing.json"
                with open(cache_file, "w") as f:
                    json.dump({"domains": ["evil.com", "phish.net"]}, f)
                reg._load_guardian()
                assert "evil.com" in reg._phishing_domains
                assert "phish.net" in reg._phishing_domains

    def test_guardian_list_format(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "guardian_phishing.json"
                with open(cache_file, "w") as f:
                    json.dump(["scam.org", "fake.io"], f)
                reg._load_guardian()
                assert "scam.org" in reg._phishing_domains

    def test_cryptoscamdb_dict_format(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "cryptoscamdb.json"
                with open(cache_file, "w") as f:
                    json.dump({"addresses": ["0xbad1", "0xbad2"]}, f)
                reg._load_cryptoscamdb()
                assert "0xbad1" in reg._scam_addresses

    def test_cryptoscamdb_list_format(self) -> None:
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "cryptoscamdb.json"
                with open(cache_file, "w") as f:
                    json.dump(["0xbad1", "0xbad2"], f)
                reg._load_cryptoscamdb()
                assert "0xbad1" in reg._scam_addresses

    def test_missing_cache_file_does_not_crash(self) -> None:
        """Loading when cache file is absent should gracefully do nothing."""
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                reg._load_goldrush()  # no files exist
                reg._load_scamsniffer()
                reg._load_opensanctions()
                reg._load_guardian()
                reg._load_cryptoscamdb()
                # Should not raise
                assert reg._stats == {
                    "tokens": 0,
                    "nfts": 0,
                    "phishing": 0,
                    "sanctioned": 0,
                    "scam": 0,
                }

    def test_corrupted_json_file(self) -> None:
        """Corrupted JSON should not crash the loader."""
        reg = SpamRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                cache_file = Path(tmpdir) / "goldrush_spam_ethereum.json"
                with open(cache_file, "w") as f:
                    f.write("not valid json{{{")
                reg._load_goldrush()  # should not raise


class TestSingleton:
    """get_spam_registry() singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        reg1 = get_spam_registry()
        reg2 = get_spam_registry()
        assert reg1 is reg2

    def test_singleton_is_spam_registry_instance(self) -> None:
        reg = get_spam_registry()
        assert isinstance(reg, SpamRegistry)


class TestSeedSpamData:
    """seed_spam_data() with mocked HTTP.

    Note: seed_spam_data uses asyncio.run() internally, which fails in an
    already-running event loop. We test the internal _fetch() coroutine
    directly by patching asyncio.run to execute in the current loop.
    """

    @pytest.mark.asyncio
    async def test_seed_success_path(self) -> None:
        """Seed function should make HTTP requests and write files."""
        mock_client = AsyncMock()

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status_code = 200
            if "goldrush" in url:
                resp.json.return_value = ["0xspam1", "0xspam2"]
            else:
                resp.text = '["0xscam1"]'
            return resp

        mock_client.get = mock_get
        mock_client.__aenter__.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                with patch("httpx.AsyncClient", return_value=mock_client):
                    # Patch asyncio.run to execute in current loop
                    _loop = asyncio.get_running_loop()
                    with patch("asyncio.run", side_effect=lambda c: _loop.run_until_complete(c)):
                        seed_spam_data()
                    # Files should have been written
                    files = list(Path(tmpdir).iterdir())
                    assert len(files) > 0

    @pytest.mark.asyncio
    async def test_seed_http_failure_does_not_crash(self) -> None:
        """HTTP failure should be caught and logged."""
        mock_client = AsyncMock()

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status_code = 500
            return resp

        mock_client.get = mock_get
        mock_client.__aenter__.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                with patch("httpx.AsyncClient", return_value=mock_client):
                    _loop = asyncio.get_running_loop()
                    with patch("asyncio.run", side_effect=lambda c: _loop.run_until_complete(c)):
                        seed_spam_data()  # should not raise

    @pytest.mark.asyncio
    async def test_seed_connection_error(self) -> None:
        """Connection error should be caught."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = ConnectionError("DNS failure")
        mock_client.__aenter__.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.spam_registry.SPAM_DATA_DIR", Path(tmpdir)):
                with patch("httpx.AsyncClient", return_value=mock_client):
                    _loop = asyncio.get_running_loop()
                    with patch("asyncio.run", side_effect=lambda c: _loop.run_until_complete(c)):
                        seed_spam_data()  # should not raise
