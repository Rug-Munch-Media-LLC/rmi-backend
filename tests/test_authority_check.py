"""Pure tests for the authority/ownership checks module.

All fetchers are injected stubs — no network, no DB. Covers:
  - mintable / freezable / LP-unlocked flags
  - LP locked (positive + neutral)
  - neutral on empty / failure (fail-open)
  - authority_signal rendering (right string / None)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.authority_check import (
    AuthorityFetcher,
    _as_bool_or_none,
    _as_float_or_none,
    _derive_renounced,
    _guard,
    _is_renounced_evm,
    authority_signal,
    check_authority,
)


def _ok(payload: dict[str, Any]) -> AuthorityFetcher:
    """Stub fetcher returning a fixed payload."""

    async def _fetcher(address: str, chain: str) -> dict[str, Any]:
        return payload

    return _fetcher


async def _boom(address: str, chain: str) -> dict[str, Any]:
    raise RuntimeError("network down")


async def _run(**injects: Any) -> dict[str, Any]:
    return await check_authority("0xtoken", "ethereum", **injects)


# ── check_authority: flags ────────────────────────────────────────────────


async def test_mintable_flag() -> None:
    result = await _run(fetch_authority=_ok({"mintable": True, "source": "stub"}))
    assert result["mintable"] is True
    assert authority_signal(result) == "mintable"


async def test_freeze_authority_flag() -> None:
    result = await _run(fetch_authority=_ok({"freeze_authority": True, "source": "stub"}))
    assert result["freeze_authority"] is True
    assert authority_signal(result) == "freezable"


async def test_lp_unlocked_flag() -> None:
    result = await _run(fetch_lp_lock=_ok({"lp_locked": False, "lp_lock_pct": 0.0, "source": "stub"}))
    assert result["lp_locked"] is False
    assert result["lp_lock_pct"] == 0.0
    assert authority_signal(result) == "LP not locked"


async def test_lp_locked_positive() -> None:
    result = await _run(fetch_lp_lock=_ok({"lp_locked": True, "lp_lock_pct": 100.0, "source": "stub"}))
    assert result["lp_locked"] is True
    assert result["lp_lock_pct"] == 100.0
    assert authority_signal(result) == "LP 100% locked"


async def test_lp_locked_neutral_when_partial() -> None:
    result = await _run(fetch_lp_lock=_ok({"lp_locked": True, "lp_lock_pct": 85.0, "source": "stub"}))
    assert result["lp_locked"] is True
    assert authority_signal(result) is None


async def test_renounced_positive_is_neutral() -> None:
    result = await _run(
        fetch_authority=_ok({"mintable": False, "freeze_authority": False, "source": "stub"}),
        fetch_lp_lock=_ok({"lp_locked": True, "lp_lock_pct": 100.0, "source": "stub"}),
        fetch_renounced=_ok({"renounced": True, "source": "stub"}),
    )
    assert result["renounced"] is True
    assert result["mintable"] is False
    assert authority_signal(result) == "LP 100% locked"


async def test_renounced_false_surfaces() -> None:
    result = await _run(
        fetch_renounced=_ok({"renounced": False, "source": "stub"}),
        fetch_authority=_ok({"mintable": False, "freeze_authority": False, "source": "stub"}),
        fetch_lp_lock=_ok({"lp_locked": True, "lp_lock_pct": 100.0, "source": "stub"}),
    )
    assert result["renounced"] is False
    assert authority_signal(result) == "LP 100% locked"


# ── check_authority: fail-open / unknown ──────────────────────────────────


async def test_fail_open_on_fetcher_error() -> None:
    result = await _run(fetch_authority=_boom, fetch_lp_lock=_boom, fetch_renounced=_boom)
    assert result == {
        "mintable": None,
        "freeze_authority": None,
        "lp_locked": None,
        "lp_lock_pct": None,
        "renounced": None,
        "evidence": None,
    }
    assert authority_signal(result) is None


async def test_neutral_on_empty_fetchers() -> None:
    result = await _run(fetch_authority=_ok({}), fetch_lp_lock=_ok({}), fetch_renounced=_ok({}))
    assert result["mintable"] is None
    assert result["lp_lock_pct"] is None
    assert result["renounced"] is None
    assert authority_signal(result) is None


async def test_renounced_derived_from_authority_payload() -> None:
    result = await _run(fetch_authority=_ok({"mintable": False, "renounced": True, "source": "solana_rpc"}))
    assert result["renounced"] is True
    assert result["mintable"] is False
    assert authority_signal(result) is None


async def test_evidence_mentions_sources() -> None:
    result = await _run(
        fetch_authority=_ok({"mintable": True, "source": "goplus"}),
        fetch_lp_lock=_ok({"lp_locked": True, "lp_lock_pct": 100.0, "source": "own_lp_lock"}),
    )
    assert "authority=goplus" in (result["evidence"] or "")
    assert "lp=own_lp_lock" in (result["evidence"] or "")


# ── authority_signal: pure rendering ──────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        # mintable wins over freeze / LP
        ({"mintable": True, "freeze_authority": True}, "mintable"),
        ({"mintable": True, "lp_locked": False}, "mintable"),
        ({"freeze_authority": True, "lp_locked": False}, "freezable"),
        # LP states
        ({"lp_locked": False, "lp_lock_pct": 0.0}, "LP not locked"),
        ({"lp_locked": True, "lp_lock_pct": 100.0}, "LP 100% locked"),
        ({"lp_locked": True, "lp_lock_pct": 99.0}, None),
        ({"lp_locked": None, "lp_lock_pct": 100.0}, "LP 100% locked"),
        # neutral / unknown
        ({}, None),
        ({"mintable": None, "freeze_authority": None}, None),
        ({"mintable": False, "freeze_authority": False, "lp_locked": True, "lp_lock_pct": 95.0}, None),
        (
            {"mintable": False, "freeze_authority": False, "renounced": True, "lp_locked": True, "lp_lock_pct": 100.0},
            "LP 100% locked",
        ),
        # string flags are not coerced as truthy when absent
        ({"mintable": "1", "freeze_authority": "0"}, "mintable"),
    ],
)
def test_signal_renders_expected_string(result: dict[str, Any], expected: str | None) -> None:
    assert authority_signal(result) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"mintable": True},
        {"mintable": "1"},
        {"mintable": 1},
    ],
)
def test_mintable_coercion(payload: dict[str, Any]) -> None:
    assert authority_signal(payload) == "mintable"


# ═══════════════════════════════════════════════════════════════════════
# Renounce derivation / coercion helpers (missing lines)
# ═══════════════════════════════════════════════════════════════════════


def test_is_renounced_evm_provable_only() -> None:
    # can_take_back_ownership == "1" → explicitly NOT renounced.
    assert _is_renounced_evm({"can_take_back_ownership": "1", "owner_address": "0x0"}) is False
    # Null owner → renounced.
    assert _is_renounced_evm({"owner_address": "0x0000000000000000000000000000000000000000"}) is True
    # Dead owner → renounced.
    assert _is_renounced_evm({"owner_address": "0x000000000000000000000000000000000000dEaD"}) is True
    # Active owner → not renounced.
    assert _is_renounced_evm({"owner_address": "0xAbC123"}) is False
    # Missing owner → unknown (None), not guessed.
    assert _is_renounced_evm({}) is None
    assert _is_renounced_evm({"owner_address": ""}) is None
    # EVM owner comparison is case-insensitive.
    assert _is_renounced_evm({"owner_address": "0X0000000000000000000000000000000000000000"}) is True


def test_as_bool_or_none_handles_uncoercible() -> None:
    assert _as_bool_or_none(None) is None
    assert _as_bool_or_none(True) is True
    assert _as_bool_or_none(0) is False

    class _Weird:
        def __bool__(self):
            raise ValueError("nope")

    assert _as_bool_or_none(_Weird()) is None


def test_as_float_or_none_handles_uncoercible() -> None:
    assert _as_float_or_none(None) is None
    assert _as_float_or_none("3.5") == 3.5

    class _Weird:
        def __float__(self):
            raise TypeError("nope")

    assert _as_float_or_none(_Weird()) is None


def test_derive_renounced_presence_and_absence() -> None:
    assert _derive_renounced({"renounced": True, "source": "solana_rpc"}) == {
        "renounced": True,
        "source": "solana_rpc",
    }
    assert _derive_renounced({"mintable": True}) == {}


async def test_guard_returns_empty_for_non_dict_payload() -> None:
    async def _bad() -> str:
        return "not a dict"

    assert await _guard(_bad()) == {}


# ═══════════════════════════════════════════════════════════════════════
# Default fetchers (mocked, no network/DB)
# ═══════════════════════════════════════════════════════════════════════


async def test_fetch_mint_freeze_solana_path() -> None:
    with patch(
        "app.domains.scanner.authority_check._solana_mint_freeze", new=AsyncMock(return_value={"mintable": True})
    ):
        from app.domains.scanner.authority_check import _fetch_mint_freeze

        result = await _fetch_mint_freeze("mintaddr", "solana")
    assert result == {"mintable": True}


async def test_fetch_mint_freeze_evm_path() -> None:
    with patch("app.domains.scanner.authority_check._evm_goplus_flags", new=AsyncMock(return_value={"mintable": True})):
        from app.domains.scanner.authority_check import _fetch_mint_freeze

        result = await _fetch_mint_freeze("0xtoken", "ethereum")
    assert result == {"mintable": True}


async def test_fetch_mint_freeze_fail_open_on_error() -> None:
    with patch(
        "app.domains.scanner.authority_check._solana_mint_freeze", new=AsyncMock(side_effect=RuntimeError("rpc down"))
    ):
        from app.domains.scanner.authority_check import _fetch_mint_freeze

        result = await _fetch_mint_freeze("mintaddr", "solana")
    assert result == {}


async def test_fetch_lp_lock_mocked_engine() -> None:
    with patch(
        "app.domains.scanner.engines.own_lp_lock.get_own_lp_lock_status",
        new=AsyncMock(return_value={"is_locked": True, "locked_pct": 100.0, "source": "own_lp_lock"}),
    ):
        from app.domains.scanner.authority_check import _fetch_lp_lock

        result = await _fetch_lp_lock("0xtoken", "ethereum")
    assert result == {"lp_locked": True, "lp_lock_pct": 100.0, "source": "own_lp_lock"}


async def test_fetch_lp_lock_unavailable_is_unknown() -> None:
    with patch(
        "app.domains.scanner.engines.own_lp_lock.get_own_lp_lock_status",
        new=AsyncMock(return_value={"source": "unavailable"}),
    ):
        from app.domains.scanner.authority_check import _fetch_lp_lock

        result = await _fetch_lp_lock("0xtoken", "ethereum")
    assert result == {}


async def test_fetch_lp_lock_fail_open_on_error() -> None:
    with patch(
        "app.domains.scanner.engines.own_lp_lock.get_own_lp_lock_status",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        from app.domains.scanner.authority_check import _fetch_lp_lock

        result = await _fetch_lp_lock("0xtoken", "ethereum")
    assert result == {}


async def test_solana_mint_freeze_parses_mint_info() -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "result": {
                    "value": {
                        "data": {
                            "parsed": {
                                "type": "mint",
                                "info": {"mintAuthority": "authAddr", "freezeAuthority": None},
                            }
                        }
                    }
                }
            }

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = _Resp()

    from app.domains.scanner.authority_check import _solana_mint_freeze

    with patch("httpx.AsyncClient", return_value=client):
        result = await _solana_mint_freeze("mintaddr")

    assert result["mintable"] is True
    assert result["freeze_authority"] is False
    assert result["renounced"] is False
    assert result["source"] == "solana_rpc"


async def test_solana_mint_freeze_renounced_no_authority() -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "result": {
                    "value": {
                        "data": {"parsed": {"type": "mint", "info": {"mintAuthority": None, "freezeAuthority": None}}}
                    }
                }
            }

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = _Resp()

    from app.domains.scanner.authority_check import _solana_mint_freeze

    with patch("httpx.AsyncClient", return_value=client):
        result = await _solana_mint_freeze("mintaddr")

    assert result["renounced"] is True
    assert result["mintable"] is False


async def test_solana_mint_freeze_skips_non_mint_and_empty() -> None:
    from app.domains.scanner.authority_check import _solana_mint_freeze
    from app.domains.scanner.models import SOLANA_RPC_ENDPOINTS

    class _Resp1:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"result": {"value": {"data": {"parsed": {"type": "token"}}}}}

    class _Resp2:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"result": None}

    async def _post(url, *args, **kwargs):
        return _Resp1() if url == SOLANA_RPC_ENDPOINTS[0] else _Resp2()

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = _post

    with patch("httpx.AsyncClient", return_value=client):
        result = await _solana_mint_freeze("mintaddr")

    assert result == {}


async def test_solana_mint_freeze_fail_open_on_connection_error() -> None:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = ConnectionError("refused")

    from app.domains.scanner.authority_check import _solana_mint_freeze

    with patch("httpx.AsyncClient", return_value=client):
        result = await _solana_mint_freeze("mintaddr")

    assert result == {}


async def test_solana_mint_freeze_retries_next_rpc_on_non_200() -> None:
    from app.domains.scanner.authority_check import _solana_mint_freeze
    from app.domains.scanner.models import SOLANA_RPC_ENDPOINTS

    class _ErrResp:
        status_code = 503

        def json(self) -> dict[str, Any]:
            return {}

    class _OkResp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "result": {
                    "value": {
                        "data": {"parsed": {"type": "mint", "info": {"mintAuthority": "a", "freezeAuthority": "b"}}}
                    }
                }
            }

    async def _post(url, *args, **kwargs):
        return _ErrResp() if url == SOLANA_RPC_ENDPOINTS[0] else _OkResp()

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = _post

    with patch("httpx.AsyncClient", return_value=client):
        result = await _solana_mint_freeze("mintaddr")

    assert result["source"] == "solana_rpc"
    assert result["mintable"] is True


async def test_evm_goplus_flags_parses_flags() -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "result": {
                    "0xtoken": {"is_mintable": "1", "owner_address": "0x0000000000000000000000000000000000000000"}
                }
            }

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = _Resp()

    from app.domains.scanner.authority_check import _evm_goplus_flags

    with patch("httpx.AsyncClient", return_value=client):
        result = await _evm_goplus_flags("0xToKeN", "ethereum")

    assert result["mintable"] is True
    assert result["freeze_authority"] is None
    assert result["renounced"] is True
    assert result["source"] == "goplus"


async def test_evm_goplus_flags_fail_open_on_bad_response() -> None:
    from app.domains.scanner.authority_check import _evm_goplus_flags

    class _ErrResp:
        status_code = 500

        def json(self) -> dict[str, Any]:
            return {}

    class _EmptyResp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"result": {}}

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = _ErrResp()
    with patch("httpx.AsyncClient", return_value=client):
        assert await _evm_goplus_flags("0xtoken", "ethereum") == {}

    client2 = AsyncMock()
    client2.__aenter__.return_value = client2
    client2.get.return_value = _EmptyResp()
    with patch("httpx.AsyncClient", return_value=client2):
        assert await _evm_goplus_flags("0xtoken", "ethereum") == {}
