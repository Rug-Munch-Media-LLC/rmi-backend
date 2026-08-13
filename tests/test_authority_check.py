"""Pure tests for the authority/ownership checks module.

All fetchers are injected stubs — no network, no DB. Covers:
  - mintable / freezable / LP-unlocked flags
  - LP locked (positive + neutral)
  - neutral on empty / failure (fail-open)
  - authority_signal rendering (right string / None)
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.scanner.authority_check import (
    AuthorityFetcher,
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
