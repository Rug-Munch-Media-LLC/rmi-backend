"""Pure unit tests for launch security enrichment + suspicious ranking.

Covers:
  - ``app.databus.token_launch_detector.enrich_launches_security`` — adds a
    ``security`` sub-dict (honeypot / mintable / lp_locked / creator_address /
    bundle_suspect) by wiring the existing scanner quick-checks, fail-open per
    check, capped to the top-N launches, backward-compatible (original fields
    preserved, caller records untouched).
  - ``TokenLaunchDetector.enrich_launches`` — thin delegation to the module
    function.
  - ``app.domains.chat_tools._launch_suspicion_score`` — ranks honeypot /
    mintable / unlocked-LP / bundled above age & liquidity and emits the
    concrete reason strings.
  - ``app.domains.chat_tools._exec_suspicious`` — renders a honeypot launch
    with "honeypot" and a mintable launch with "mintable".

No network / DB / LLM calls: the security checks and the detector are mocked
at the module boundary.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any
from unittest import mock

import pytest

from app.databus import token_launch_detector as tld
from app.databus.token_launch_detector import TokenLaunchDetector, enrich_launches_security
from app.domains.chat_tools import _exec_suspicious, _launch_suspicion_score


def _launch(**overrides: Any) -> dict[str, Any]:
    """A launch record shaped like ``TokenLaunchDetector.get_recent_launches``."""
    base: dict[str, Any] = {
        "address": "LaunChAddr111111111111111111111111111111111",
        "chain": "solana",
        "symbol": "PEPE",
        "name": "Pepe Coin",
        "liquidity": 1200.0,
        "market_cap": 8000.0,
        "age_seconds": 240,
        "dex": "pump",
        "pair_address": "PairAddr1111111111111111111111111111111",
    }
    base.update(overrides)
    return base


# Field name → module-level check function. The ``creator_address`` field is
# backed by ``_security_check_creator`` (not a ``_creator_address`` suffix).
_CHECK_FN_BY_FIELD = {
    "honeypot": "_security_check_honeypot",
    "mintable": "_security_check_mintable",
    "lp_locked": "_security_check_lp_locked",
    "creator_address": "_security_check_creator",
    "bundle_suspect": "_security_check_bundle",
}


@contextmanager
def _patched_checks(**returns: Any):
    """Patch every per-field security check; ``returns`` maps field→value.

    A field mapped to an ``Exception`` instance makes that check raise (to
    exercise fail-open). Fields not listed default to their fail-closed value.
    """
    defaults: dict[str, Any] = {
        "honeypot": False,
        "mintable": False,
        "lp_locked": None,
        "creator_address": None,
        "bundle_suspect": False,
    }
    values = {**defaults, **returns}

    async def _stub(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    def _side_effect_for(value: Any):
        # The patched checks are async functions, so ``mock.patch`` auto-creates
        # an AsyncMock and only awaits an *async* side_effect (a sync one that
        # merely returns a coroutine is NOT awaited).
        async def _effect(*args: Any) -> Any:
            return await _stub(value)

        return _effect

    with ExitStack() as stack:
        patched = {}
        for field, fn_name in _CHECK_FN_BY_FIELD.items():
            patched[field] = stack.enter_context(
                mock.patch(
                    f"app.databus.token_launch_detector.{fn_name}",
                    side_effect=_side_effect_for(values[field]),
                )
            )
        yield patched


# ── 1. Enrichment adds the security fields from mocked checks ────────────


async def test_enrich_adds_security_fields_from_mocked_checks() -> None:
    launch = _launch()
    with _patched_checks(
        honeypot=True, mintable=True, lp_locked=True, creator_address="Creator1111", bundle_suspect=True
    ):
        enriched = await enrich_launches_security([launch])
    assert len(enriched) == 1
    sec = enriched[0]["security"]
    assert sec["honeypot"] is True
    assert sec["mintable"] is True
    assert sec["lp_locked"] is True
    assert sec["creator_address"] == "Creator1111"
    assert sec["bundle_suspect"] is True


async def test_enrich_unknown_checks_stay_none_or_false() -> None:
    # lp_locked unknown → None (never fires "LP not locked"); missing booleans → False.
    with _patched_checks(lp_locked=None, mintable=None, honeypot=None, bundle_suspect=None):
        enriched = await enrich_launches_security([_launch()])
    sec = enriched[0]["security"]
    assert sec["honeypot"] is False
    assert sec["mintable"] is False
    assert sec["lp_locked"] is None
    assert sec["bundle_suspect"] is False


# ── 2. Fail-open: a failed check → None/False, never raises ───────────────


async def test_enrich_failed_check_fail_open() -> None:
    launch = _launch()
    with _patched_checks(honeypot=RuntimeError("boom"), mintable=True):
        enriched = await enrich_launches_security([launch])  # must not raise
    sec = enriched[0]["security"]
    assert sec["honeypot"] is False  # failed check → fail-closed default
    assert sec["mintable"] is True  # other checks still ran


async def test_enrich_never_raises_when_detector_construction_fails() -> None:
    # Even an exception escaping _enrich_one (e.g. inside the check wiring)
    # leaves a default security dict — the batch never raises.
    with mock.patch(
        "app.databus.token_launch_detector._enrich_one_launch",
        side_effect=RuntimeError("catastrophic"),
    ):
        enriched = await enrich_launches_security([_launch(), _launch(address="Addr22222")])
    assert len(enriched) == 2
    for record in enriched:
        assert record["security"] == {
            "honeypot": False,
            "mintable": False,
            "lp_locked": None,
            "creator_address": None,
            "bundle_suspect": False,
        }


# ── 3. Capping: only the top-N launches get network checks ───────────────


@pytest.mark.slow  # 34s: heavy transitive imports (torch/faiss/guardrails) on first call
async def test_enrich_caps_network_checks_to_top_n() -> None:
    seen: list[str] = []

    async def tracking_honeypot(launch, cache=None):
        seen.append(str(launch.get("address")))
        return True

    launches = [
        _launch(address="One11111111111111111111111111111111"),
        _launch(address="Two22222222222222222222222222222222"),
        _launch(address="Three3333333333333333333333333333333"),
    ]
    with mock.patch("app.databus.token_launch_detector._security_check_honeypot", side_effect=tracking_honeypot):
        enriched = await enrich_launches_security(launches, max_checks=1)

    assert seen == ["One11111111111111111111111111111111"]  # only the newest is checked
    assert enriched[0]["security"]["honeypot"] is True
    # Beyond the cap: fail-closed defaults, but every record still gets a security dict.
    assert enriched[1]["security"]["honeypot"] is False
    assert enriched[2]["security"]["honeypot"] is False
    for record in enriched:
        assert "security" in record


async def test_enrich_seeds_creator_from_record_for_all_launches() -> None:
    # creator_address is a free signal — seeded from the record even beyond the cap.
    launches = [
        _launch(address="One11111111111111111111111111111111", creator="RecordCreator11111111111111111111111111"),
        _launch(
            address="Two22222222222222222222222222222222", creator_address="FieldCreator22222222222222222222222222"
        ),
    ]
    with _patched_checks():
        enriched = await enrich_launches_security(launches, max_checks=0)
    assert enriched[0]["security"]["creator_address"] == "RecordCreator11111111111111111111111111"
    assert enriched[1]["security"]["creator_address"] == "FieldCreator22222222222222222222222222"


# ── 4. Backward compatibility: original fields preserved, no caller mutation ──


async def test_enrich_preserves_original_fields_and_does_not_mutate_caller() -> None:
    launch = _launch(symbol="KEEP", liquidity=777.0, age_seconds=99)
    with _patched_checks(honeypot=True):
        enriched = await enrich_launches_security([launch])
    original_keys = {
        "address",
        "chain",
        "symbol",
        "name",
        "liquidity",
        "market_cap",
        "age_seconds",
        "dex",
        "pair_address",
    }
    assert original_keys <= enriched[0].keys()
    assert enriched[0]["symbol"] == "KEEP"
    assert enriched[0]["liquidity"] == 777.0
    assert enriched[0]["age_seconds"] == 99
    # Caller's record is untouched — only the returned copy gained "security".
    assert "security" not in launch


async def test_detector_enrich_launches_delegates_to_module_function() -> None:
    detector = TokenLaunchDetector()
    launch = _launch()
    with _patched_checks(honeypot=True):
        enriched = await detector.enrich_launches([launch])
    assert enriched[0]["security"]["honeypot"] is True
    # Default cap matches the module default.
    with mock.patch("app.databus.token_launch_detector.enrich_launches_security") as module_fn:
        module_fn.return_value = []
        await detector.enrich_launches([_launch()])
        module_fn.assert_awaited_once_with([mock.ANY], max_checks=tld._SECURITY_ENRICH_LIMIT)


# ── 5. Suspicion scoring: security ranks above age/liquidity ──────────────


def _scored(launch: dict[str, Any], scammers: set[str] | None = None) -> tuple[float, str]:
    return _launch_suspicion_score(launch, scammers or set())


async def test_suspicion_score_emits_security_reason_strings() -> None:
    score, reason = _scored(_launch(security={"honeypot": True}))
    assert score > 0
    assert "honeypot" in reason

    _, reason = _scored(_launch(security={"mintable": True}))
    assert "mintable" in reason

    _, reason = _scored(_launch(security={"lp_locked": False}))
    assert "LP not locked" in reason

    _, reason = _scored(_launch(security={"bundle_suspect": True}))
    assert "bundled snipers" in reason

    # Unknown LP lock (None) never fires the "LP not locked" reason.
    score, reason = _scored(
        _launch(liquidity=50000.0, market_cap=1_000_000.0, age_seconds=7200, security={"lp_locked": None})
    )
    assert score == 0.0
    assert reason == ""


async def test_suspicion_score_ranks_security_above_age_and_liquidity() -> None:
    honeypot = _launch(liquidity=50000.0, market_cap=1_000_000.0, age_seconds=7200, security={"honeypot": True})
    mintable = _launch(liquidity=50000.0, market_cap=1_000_000.0, age_seconds=7200, security={"mintable": True})
    zero_liq = _launch(liquidity=0.0, market_cap=1_000_000.0, age_seconds=7200)  # +50
    young = _launch(liquidity=50000.0, market_cap=1_000_000.0, age_seconds=60)  # +40

    h_score, _ = _scored(honeypot)
    m_score, _ = _scored(mintable)
    z_score, _ = _scored(zero_liq)
    y_score, _ = _scored(young)

    assert h_score > z_score > y_score  # honeypot (+60) > 0-liquidity (+50) > age (+40)
    assert m_score > y_score  # mintable (+60) > age (+40)


async def test_suspicion_score_creator_matches_known_scammer() -> None:
    scam = "KnownScammerAddr11111111111111111111111111"
    launch = _launch(
        liquidity=50000.0,
        market_cap=1_000_000.0,
        age_seconds=7200,
        security={"honeypot": False, "creator_address": scam},
    )
    score, reason = _scored(launch, scammers={scam})
    assert score > 0
    assert "creator matches known scammer" in reason
    # A creator that is NOT a known scammer contributes no reason on its own.
    score, reason = _scored(launch, scammers=set())
    assert score == 0.0
    assert reason == ""


async def test_suspicion_score_flat_fields_still_work() -> None:
    # Legacy flat fields keep firing alongside the security sub-dict.
    _, reason = _scored(_launch(is_honeypot=True, is_mintable=True))
    assert "honeypot" in reason
    assert "mintable" in reason


# ── 6. Executor renders concrete security reasons ────────────────────────


async def test_exec_suspicious_renders_honeypot_and_mintable() -> None:
    launches = [
        _launch(symbol="HONEY", address="HoneyAddr1111111111111111111111111111111"),
        _launch(symbol="MINT", address="MintAddr2222222222222222222222222222222"),
    ]
    enriched = [
        {
            **launches[0],
            "security": {
                "honeypot": True,
                "mintable": False,
                "lp_locked": None,
                "creator_address": None,
                "bundle_suspect": False,
            },
        },
        {
            **launches[1],
            "security": {
                "honeypot": False,
                "mintable": True,
                "lp_locked": None,
                "creator_address": None,
                "bundle_suspect": False,
            },
        },
    ]
    detector = mock.MagicMock()
    detector.get_recent_launches = mock.AsyncMock(return_value=launches)
    detector.enrich_launches = mock.AsyncMock(return_value=enriched)
    with (
        mock.patch("app.databus.token_launch_detector.get_launch_detector", return_value=detector),
        mock.patch("app.databus.gmgn_provider.get_gmgn_provider") as gmgn,
        mock.patch("app.databus.shill_detector.load_known_scammers", return_value=set()),
    ):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        text = await _exec_suspicious()

    assert text is not None
    assert "honeypot" in text
    assert "mintable" in text
    detector.enrich_launches.assert_awaited_once()


async def test_exec_suspicious_fails_open_without_enricher() -> None:
    # Detector without enrich_launches → raw flat fields still drive the list
    # (consolidated scorer pinned to None so the legacy heuristic runs).
    launches = [
        _launch(symbol="FLAT", address="FlatAddr1111111111111111111111111111111", liquidity=0.0, age_seconds=60),
    ]
    detector = mock.MagicMock()
    detector.get_recent_launches = mock.AsyncMock(return_value=launches)
    with (
        mock.patch("app.databus.token_launch_detector.get_launch_detector", return_value=detector),
        mock.patch("app.databus.gmgn_provider.get_gmgn_provider") as gmgn,
        mock.patch("app.databus.shill_detector.load_known_scammers", return_value=set()),
        mock.patch("app.domains.chat_tools.rug_risk_score", None),
    ):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        text = await _exec_suspicious()
    assert text is not None
    assert "FLAT" in text
    assert "0 liquidity" in text


async def test_exec_suspicious_fails_open_when_enrichment_raises() -> None:
    launches = [
        _launch(symbol="RUG", address="RugAddr1111111111111111111111111111111", liquidity=0.0, age_seconds=60),
    ]
    detector = mock.MagicMock()
    detector.get_recent_launches = mock.AsyncMock(return_value=launches)
    detector.enrich_launches = mock.AsyncMock(side_effect=RuntimeError("boom"))
    with (
        mock.patch("app.databus.token_launch_detector.get_launch_detector", return_value=detector),
        mock.patch("app.databus.gmgn_provider.get_gmgn_provider") as gmgn,
        mock.patch("app.databus.shill_detector.load_known_scammers", return_value=set()),
    ):
        gmgn.return_value.get_new_pairs = mock.AsyncMock(return_value=[])
        text = await _exec_suspicious()
    assert text is not None
    assert "RUG" in text
