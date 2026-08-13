"""Pure unit tests for the deployer reputation module (no network/DB).

Every test injects stub fetchers, so ClickHouse and the databus JSON files
are never touched. The legacy behavioral-scoring API is asserted as still
importable because ``app/deployer_reputation.py`` re-exports it as a compat
shim consumed by ``app/streaming_rag.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from app.domains.scanner.deployer_reputation import (
    build_behavioral_fingerprint,
    deployer_reputation,
    reputation_signal,
    score_deployer_risk,
)


async def _fetch_none(*_args: Any) -> Any:
    """Stub fetcher that returns no data (neutral for every signal)."""
    return None


async def _fetch_zero(*_args: Any) -> int:
    """Stub fetcher that reports zero prior deployments."""
    return 0


async def _prior(n: int) -> Callable[[str, str], Awaitable[int]]:
    """Build a prior-deployments fetcher that always returns ``n``."""

    async def _inner(_address: str, _chain: str) -> int:
        return n

    return _inner


async def _rug(flagged: bool) -> Callable[[str, str], Awaitable[bool]]:
    """Build a rug-history fetcher that always returns ``flagged``."""

    async def _inner(_address: str, _chain: str) -> bool:
        return flagged

    return _inner


async def test_flags_repeat_deployer() -> None:
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(5),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result["prior_deployments"] == 5
    assert result["repeat_deployer"] is True
    assert result["rug_history"] is False
    assert result["funding_source"] is None
    assert result["score"] > 0
    assert result["evidence"] == "deployer launched 5 tokens"


async def test_flags_rug_history() -> None:
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_zero,
        fetch_rug_history=await _rug(True),
        fetch_funding_source=_fetch_none,
    )
    assert result["rug_history"] is True
    assert result["repeat_deployer"] is False
    assert result["score"] == pytest.approx(40.0)
    assert result["evidence"] == "deployer has rug history"


async def test_scores_higher_for_more_deployments() -> None:
    single = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(1),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    repeat = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(4),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    batch = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(12),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert single["score"] == pytest.approx(6.0)
    assert single["repeat_deployer"] is False
    assert repeat["score"] > single["score"]
    assert repeat["repeat_deployer"] is True
    assert batch["score"] > repeat["score"]
    assert batch["repeat_deployer"] is True
    # Deployment component is capped at 40: min(12*6, 40) + repeat + batch.
    assert batch["score"] == pytest.approx(40.0 + 18.0 + 12.0)


async def test_neutral_on_empty() -> None:
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result == {
        "score": 0.0,
        "prior_deployments": None,
        "repeat_deployer": False,
        "rug_history": False,
        "funding_source": None,
        "evidence": None,
    }
    assert reputation_signal(result) is None


async def test_neutral_when_no_data() -> None:
    """Zero prior deployments and no flags still renders a neutral result."""
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_zero,
        fetch_rug_history=await _rug(False),
        fetch_funding_source=_fetch_none,
    )
    assert result["score"] == 0.0
    assert result["prior_deployments"] == 0
    assert result["repeat_deployer"] is False
    assert result["evidence"] is None
    assert reputation_signal(result) is None


async def test_fail_open_on_fetcher_error() -> None:
    async def _boom(_address: str, _chain: str) -> int:
        raise RuntimeError("clickhouse down")

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_boom,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result == {
        "score": 0.0,
        "prior_deployments": None,
        "repeat_deployer": False,
        "rug_history": False,
        "funding_source": None,
        "evidence": None,
    }


async def test_fail_open_when_fetcher_raises_synchronously() -> None:
    def _broken(_address: str, _chain: str) -> int:  # sync, not awaitable
        raise ValueError("bad fetcher")

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_broken,  # type: ignore[arg-type]  # intentionally broken
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result["score"] == 0.0
    assert result["prior_deployments"] is None


async def test_funding_bad_actor_bumps_score() -> None:
    async def _funder(_address: str, _chain: str) -> str:
        return "0xbadfunder"

    async def _bad_actor(address: str) -> bool:
        return address == "0xbadfunder"

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_funder,
        is_bad_actor=_bad_actor,
    )
    assert result["funding_source"] == "0xbadfunder"
    assert result["score"] == pytest.approx(25.0)
    assert "known bad actor" in (result["evidence"] or "")


async def test_clean_funder_not_flagged() -> None:
    async def _funder(_address: str, _chain: str) -> str:
        return "0xlegitfunder"

    async def _bad_actor(address: str) -> bool:
        return False

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_funder,
        is_bad_actor=_bad_actor,
    )
    assert result["funding_source"] == "0xlegitfunder"
    assert result["score"] == 0.0
    assert result["evidence"] is None


def test_reputation_signal_renders() -> None:
    # Rug history is the highest-priority signal.
    assert (
        reputation_signal({"rug_history": True, "prior_deployments": 3, "repeat_deployer": True})
        == "deployer has rug history"
    )
    # Known deployment count beats the generic repeat label.
    assert (
        reputation_signal({"rug_history": False, "prior_deployments": 12, "repeat_deployer": True})
        == "deployer launched 12 tokens"
    )
    # Generic repeat label when the count is unknown.
    assert (
        reputation_signal({"rug_history": False, "prior_deployments": None, "repeat_deployer": True})
        == "repeat deployer"
    )
    # Clean / unknown -> None.
    assert reputation_signal({"rug_history": False, "prior_deployments": 0, "repeat_deployer": False}) is None
    assert reputation_signal({}) is None


def test_legacy_behavioral_api_still_importable() -> None:
    """Protects the app/deployer_reputation.py compat shim from regression."""
    vector = build_behavioral_fingerprint("0xabc", deployment_count=2)
    assert len(vector) == 6

    scored = score_deployer_risk("0xabc", deployment_count=12, mixer_funded=True)
    assert 0 <= scored["risk_score"] <= 100
    assert scored["address"] == "0xabc"
    assert scored["risk_label"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
