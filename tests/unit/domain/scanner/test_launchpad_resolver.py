"""Unit tests for launchpad_resolver — factory → real deployer resolution.

Covers ``resolve_launchpad_deployer()`` for both mechanisms:

  - Solana / pump.fun: creation tx fee payer is the real creator
    (``getSignaturesForAddress`` + ``getTransaction`` jsonParsed).
  - EVM / bags.fm: ``tx.from`` of the creation transaction is the deployer.

Also covers the registry (in-code + JSON overlay) and the case-insensitive
factory lookup used by callers that pre-lowercase deployer addresses.

All external dependencies (RPC, ClickHouse, Redis) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.launchpad_resolver import (
    clear_cache,
    get_launchpad_registry,
    lookup_launchpad,
    resolve_deployer_universal,
    resolve_launchpad_deployer,
)

_PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_TOKEN = "HnT9nFrJkYfLxYQhKfJ8S6aQkR9XyZ2WmTn4cVdEaBcD"
_CREATOR = "7GVW8rU9NqJhYzVpLf3kQxWmAbCdEfGhIjKlMnOpQrStU"
_DEPLOY_TX = "3mNX6yQqRpUeZvS9tYwXkAbCdEfGhIjKlMnOpQrStUvWxYz"
_EVM_TOKEN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_EVM_FACTORY = "0x1111111111111111111111111111111111111111"
_EVM_DEPLOYER = "0x2222222222222222222222222222222222222222"
_EVM_DEPLOY_TX = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd"


def _no_redis_get():
    return patch("app.domains.scanner.engines.launchpad_resolver._redis_get", AsyncMock(return_value=None))


def _no_redis_set():
    return patch("app.domains.scanner.engines.launchpad_resolver._redis_set", AsyncMock(return_value=True))


def _evm_registry():
    """Add a test bags.fm factory to the in-process registry (EVM default is empty)."""
    import app.domains.scanner.engines.launchpad_resolver as resolver

    return patch.object(
        resolver,
        "_REGISTRY",
        {
            "solana": dict(resolver._REGISTRY.get("solana", {})),
            "ethereum": {_EVM_FACTORY: "bags.fm"},
            "bsc": dict(resolver._REGISTRY.get("bsc", {})),
        },
    )


class TestLookupLaunchpad:
    """Registry lookup (case-insensitive for pre-lowered callers)."""

    def setup_method(self) -> None:
        clear_cache()

    def test_pump_fun_exact_case(self) -> None:
        assert lookup_launchpad("solana", _PUMP_FUN) == "pump.fun"

    def test_pump_fun_lowercased_input_still_matches(self) -> None:
        """Callers that pre-lowercase deployer addresses still hit the registry."""
        assert lookup_launchpad("solana", _PUMP_FUN.lower()) == "pump.fun"

    def test_unknown_address_returns_none(self) -> None:
        assert lookup_launchpad("solana", "5Q9jXJYzVpLf3kQxWmAbCdEfGhIjKlMnOpQrStUvWxYz12") is None
        assert lookup_launchpad("ethereum", _EVM_FACTORY) is None

    def test_unknown_chain_returns_none(self) -> None:
        assert lookup_launchpad("notachain", _PUMP_FUN) is None

    def test_registry_includes_pump_fun(self) -> None:
        reg = get_launchpad_registry()
        assert reg["solana"][_PUMP_FUN] == "pump.fun"


class TestResolveLaunchpad:
    """End-to-end resolution with mocked RPC + ClickHouse."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_non_factory_returns_none(self) -> None:
        """A deployer that is not a launchpad factory is untouched."""
        with _no_redis_get(), _no_redis_set():
            out = await resolve_launchpad_deployer(_TOKEN, "solana", "5Q9jXJYzVpLf3kQxWmAbCdEfGhIjKlMnOpQrStUvWxYz12")
        assert out is None

    @pytest.mark.asyncio
    async def test_solana_pump_fun_resolves_fee_payer(self) -> None:
        """Solana: creation tx fee payer (accountKeys[0]) is the real creator."""
        tx_payload = {
            "result": {
                "transaction": {
                    "message": {
                        "accountKeys": [
                            {"pubkey": _CREATOR, "signer": True, "writable": True},
                            {"pubkey": _PUMP_FUN, "signer": False, "writable": False},
                        ]
                    }
                }
            }
        }
        sigs_payload = {"result": [{"signature": _DEPLOY_TX, "slot": 5}]}
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(side_effect=[sigs_payload, tx_payload]),
            ),
        ):
            out = await resolve_launchpad_deployer(_TOKEN, "solana", _PUMP_FUN)

        assert out is not None
        assert out["real_deployer"] == _CREATOR
        assert out["launchpad"] == "pump.fun"
        assert out["confidence"] == 0.9
        assert out["evidence_tx"] == _DEPLOY_TX

    @pytest.mark.asyncio
    async def test_solana_uses_provided_deploy_tx(self) -> None:
        """When the caller already has the deploy tx, no sig lookup is needed."""
        tx_payload = {
            "result": {
                "transaction": {
                    "message": {
                        "accountKeys": [
                            {"pubkey": _CREATOR, "signer": True, "writable": True},
                            {"pubkey": _PUMP_FUN, "signer": False, "writable": False},
                        ]
                    }
                }
            }
        }
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(side_effect=[tx_payload]),
            ) as rpc,
        ):
            out = await resolve_launchpad_deployer(_TOKEN, "solana", _PUMP_FUN, deploy_tx=_DEPLOY_TX)

        assert out is not None
        assert out["real_deployer"] == _CREATOR
        rpc.assert_awaited_once_with(
            "solana",
            "getTransaction",
            [_DEPLOY_TX, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            timeout=10,
        )

    @pytest.mark.asyncio
    async def test_solana_fee_payer_as_plain_string_keys(self) -> None:
        """Legacy accountKeys may be plain strings — handle both shapes."""
        tx_payload = {"result": {"transaction": {"message": {"accountKeys": [_CREATOR, _PUMP_FUN]}}}}
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(side_effect=[{"result": [{"signature": _DEPLOY_TX}]}, tx_payload]),
            ),
        ):
            out = await resolve_launchpad_deployer(_TOKEN, "solana", _PUMP_FUN)
        assert out is not None
        assert out["real_deployer"] == _CREATOR

    @pytest.mark.asyncio
    async def test_solana_no_signatures_returns_none(self) -> None:
        """No creation tx found → no attribution, graceful None."""
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value={"result": []}),
            ),
        ):
            out = await resolve_launchpad_deployer(_TOKEN, "solana", _PUMP_FUN)
        assert out is None

    @pytest.mark.asyncio
    async def test_evm_resolves_tx_from(self) -> None:
        """EVM: tx.from of the creation transaction is the real deployer."""
        tx_payload = {"result": {"from": _EVM_DEPLOYER, "to": _EVM_FACTORY}}
        with (
            _evm_registry(),
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=None),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=tx_payload),
            ) as rpc,
        ):
            out = await resolve_launchpad_deployer(_EVM_TOKEN, "ethereum", _EVM_FACTORY, deploy_tx=_EVM_DEPLOY_TX)

        assert out is not None
        assert out["real_deployer"] == _EVM_DEPLOYER.lower()
        assert out["launchpad"] == "bags.fm"
        assert out["confidence"] == 1.0
        assert out["evidence_tx"] == _EVM_DEPLOY_TX
        rpc.assert_awaited_once_with("ethereum", "eth_getTransactionByHash", [_EVM_DEPLOY_TX], timeout=10)

    @pytest.mark.asyncio
    async def test_evm_uses_clickhouse_deploy_tx_fallback(self) -> None:
        """EVM without a passed tx falls back to deployer_events lookup."""

        class FakeCH:
            def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
                return [(_EVM_DEPLOY_TX,)]

        tx_payload = {"result": {"from": _EVM_DEPLOYER, "to": _EVM_FACTORY}}
        with (
            _evm_registry(),
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=FakeCH()),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=tx_payload),
            ),
        ):
            out = await resolve_launchpad_deployer(_EVM_TOKEN, "ethereum", _EVM_FACTORY)
        assert out is not None
        assert out["real_deployer"] == _EVM_DEPLOYER.lower()

    @pytest.mark.asyncio
    async def test_evm_no_tx_returns_none(self) -> None:
        """No deploy tx known and no ClickHouse data → graceful None."""
        with (
            _evm_registry(),
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=None),
        ):
            out = await resolve_launchpad_deployer(_EVM_TOKEN, "ethereum", _EVM_FACTORY)
        assert out is None

    @pytest.mark.asyncio
    async def test_rpc_failure_returns_none(self) -> None:
        """RPC errors degrade gracefully (no raise)."""
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=None),
            ),
        ):
            out = await resolve_launchpad_deployer(_TOKEN, "solana", _PUMP_FUN, deploy_tx=_DEPLOY_TX)
        assert out is None

    @pytest.mark.asyncio
    async def test_missing_token_and_tx_returns_none(self) -> None:
        """No anchor (no token, no deploy_tx) → cannot resolve."""
        with _no_redis_get(), _no_redis_set():
            out = await resolve_launchpad_deployer("", "solana", _PUMP_FUN)
        assert out is None

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_rpc(self) -> None:
        """A cached resolution is returned without RPC calls."""
        cached = {"real_deployer": _CREATOR, "launchpad": "pump.fun", "confidence": 0.9}
        with (
            patch("app.domains.scanner.engines.launchpad_resolver._redis_get", AsyncMock(return_value=cached)),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value={"result": []}),
            ) as rpc,
        ):
            out = await resolve_launchpad_deployer(_TOKEN, "solana", _PUMP_FUN)
        assert out == cached
        rpc.assert_not_called()


class TestResolveDeployerUniversal:
    """Universal resolver — structural deployer detection for ANY platform.

    Covers the fallback chain: registry factory → indexer deployer_eoa →
    EVM creation tx → Solana fee payer → passthrough. NEVER returns None.
    """

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_pump_fun_returns_real_creator_not_program(self) -> None:
        """A pump.fun token resolves to its creator EOA (fee payer), not the program."""
        tx_payload = {
            "result": {
                "transaction": {
                    "message": {
                        "accountKeys": [
                            {"pubkey": _CREATOR, "signer": True, "writable": True},
                            {"pubkey": _PUMP_FUN, "signer": False, "writable": False},
                        ]
                    }
                }
            }
        }
        sigs_payload = {"result": [{"signature": _DEPLOY_TX, "slot": 5}]}
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=None),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(side_effect=[sigs_payload, tx_payload]),
            ),
        ):
            out = await resolve_deployer_universal(_TOKEN, "solana", _PUMP_FUN)

        assert out is not None
        assert out["real_deployer"] == _CREATOR
        assert out["launchpad"] == "pump.fun"
        assert out["method"] == "factory_trace"
        assert out["resolved"] is True
        assert out["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_normal_erc20_resolves_creation_tx_sender(self) -> None:
        """A normal ERC-20 (deployer not in registry) resolves via creation tx."""
        tx_payload = {"result": {"from": _EVM_DEPLOYER, "to": _EVM_FACTORY}}
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch(
                "app.domains.scanner.engines.launchpad_resolver._get_ch_client",
                return_value=None,
            ),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=tx_payload),
            ) as rpc,
        ):
            out = await resolve_deployer_universal(_EVM_TOKEN, "ethereum", _EVM_FACTORY, deploy_tx=_EVM_DEPLOY_TX)

        assert out is not None
        assert out["real_deployer"] == _EVM_DEPLOYER.lower()
        assert out["method"] in ("evm_contract", "evm_tx")
        assert out["resolved"] is True
        assert out["confidence"] == 1.0
        rpc.assert_awaited_once_with("ethereum", "eth_getTransactionByHash", [_EVM_DEPLOY_TX], timeout=10)

    @pytest.mark.asyncio
    async def test_deployer_eoa_populated_used_directly(self) -> None:
        """When the indexer already resolved deployer_eoa, use it without RPC."""

        class FakeCH:
            def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
                if "deployer_eoa" in sql:
                    return [(_EVM_DEPLOYER,)]
                return []

        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=FakeCH()),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=None),
            ) as rpc,
        ):
            out = await resolve_deployer_universal(_EVM_TOKEN, "ethereum", _EVM_FACTORY)

        assert out is not None
        assert out["real_deployer"] == _EVM_DEPLOYER.lower()
        assert out["method"] == "deployer_eoa"
        assert out["confidence"] == 1.0
        rpc.assert_not_called()

    @pytest.mark.asyncio
    async def test_deployer_eoa_by_deployer_without_token(self) -> None:
        """Fingerprint-style callers (no token) resolve via deployer_eoa lookup."""

        class FakeCH:
            def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
                if "lower(deployer)" in sql:
                    return [(_EVM_DEPLOYER,)]
                return []

        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=FakeCH()),
        ):
            out = await resolve_deployer_universal("", "ethereum", _EVM_FACTORY)

        assert out is not None
        assert out["real_deployer"] == _EVM_DEPLOYER.lower()
        assert out["method"] == "deployer_eoa"
        assert out["resolved"] is True

    @pytest.mark.asyncio
    async def test_never_returns_none_fallback_passthrough(self) -> None:
        """No data anywhere → passthrough of the recorded deployer (never None)."""
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=None),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=None),
            ),
        ):
            out = await resolve_deployer_universal(_EVM_TOKEN, "ethereum", _EVM_FACTORY)

        assert out is not None
        assert out["real_deployer"] == _EVM_FACTORY
        assert out["method"] == "passthrough"
        assert out["resolved"] is False
        assert out["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_solana_unknown_program_structural_fee_payer(self) -> None:
        """An unknown Solana launchpad (not in registry) still resolves via fee payer."""
        unknown_program = "5Q9jXJYzVpLf3kQxWmAbCdEfGhIjKlMnOpQrStUvWxYz12"
        tx_payload = {
            "result": {
                "transaction": {
                    "message": {
                        "accountKeys": [
                            {"pubkey": _CREATOR, "signer": True, "writable": True},
                            {"pubkey": unknown_program, "signer": False, "writable": False},
                        ]
                    }
                }
            }
        }
        sigs_payload = {"result": [{"signature": _DEPLOY_TX, "slot": 5}]}
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=None),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(side_effect=[sigs_payload, tx_payload]),
            ),
        ):
            out = await resolve_deployer_universal(_TOKEN, "solana", unknown_program)

        assert out is not None
        assert out["real_deployer"] == _CREATOR
        assert out["launchpad"] is None  # not in registry
        assert out["method"] == "solana_fee_payer"
        assert out["resolved"] is True

    @pytest.mark.asyncio
    async def test_solana_no_data_passthrough(self) -> None:
        """Solana with no signatures → passthrough, still not None."""
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=None),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value={"result": []}),
            ),
        ):
            out = await resolve_deployer_universal(_TOKEN, "solana", _PUMP_FUN)

        assert out is not None
        assert out["method"] == "passthrough"

    @pytest.mark.asyncio
    async def test_evm_clickhouse_deploy_tx_fallback(self) -> None:
        """EVM without passed tx falls back to deployer_events lookup then RPC."""

        class FakeCH:
            def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
                if "argMin(tx_hash" in sql:
                    return [(_EVM_DEPLOY_TX,)]
                if "deployer_eoa" in sql:
                    return []
                return []

        tx_payload = {"result": {"from": _EVM_DEPLOYER, "to": None}}
        with (
            _no_redis_get(),
            _no_redis_set(),
            patch("app.domains.scanner.engines.launchpad_resolver._get_ch_client", return_value=FakeCH()),
            patch(
                "app.domains.scanner.engines.launchpad_resolver.rpc_call",
                AsyncMock(return_value=tx_payload),
            ),
        ):
            out = await resolve_deployer_universal(_EVM_TOKEN, "ethereum", _EVM_FACTORY)

        assert out is not None
        assert out["real_deployer"] == _EVM_DEPLOYER.lower()
        assert out["method"] == "evm_tx"  # to=None → direct deployment, not factory
        assert out["resolved"] is True


if __name__ == "__main__":
    pytest.main([__file__])
