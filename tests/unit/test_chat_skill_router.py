"""Crypto skill router + rubrics: class ordering, cost shape, purity.

No network and no ``app.config`` — ``skill_router`` and ``_crypto_skills`` are
stdlib-only by design, so these tests run in the fast default suite.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.domains.chat._crypto_skills import MAX_RUBRICS_PER_ANSWER, RUBRICS, SKILL_KEYS, compose
from app.domains.chat.skill_router import (
    EXPLOIT,
    HOWTO,
    LANE_DEEP,
    LANE_MODEL,
    LANE_TEMPLATE,
    MARKET,
    PORTFOLIO,
    PRICE,
    TOKEN_RISK,
    UNKNOWN,
    WALLET,
    SkillPlan,
    estimate_cost,
    route_skill,
)

EVM = "0x" + "a1" * 20
SOL = "So11111111111111111111111111111111111111112"


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        (f"is {EVM} a scam?", TOKEN_RISK),
        (f"scan {SOL}", TOKEN_RISK),
        ("check this token for honeypot", TOKEN_RISK),
        (f"who funded {EVM}", WALLET),
        ("trace the related wallets of this deployer", WALLET),
        ("is this in my portfolio", PORTFOLIO),
        (f"add {SOL} to my bags", PORTFOLIO),
        ("explain the Euler exploit", EXPLOIT),
        ("latest breaking crypto news", EXPLOIT),
        ("how's the market today", MARKET),
        ("fear and greed", MARKET),
        ("what's the price of $BTC", PRICE),
        ("how much is ethereum", PRICE),
        ("what is a honeypot", HOWTO),
        ("how do i revoke an approval", HOWTO),
        ("gm", UNKNOWN),
        ("", UNKNOWN),
        ("   ", UNKNOWN),
    ],
)
def test_intent_classification(message: str, intent: str) -> None:
    assert route_skill(message).intent == intent


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        # ordering traps: the first match must win
        (f"who funded {EVM} and is it a scam", WALLET),  # wallet beats risk
        (f"is {EVM} in my portfolio", PORTFOLIO),  # portfolio beats wallet
        (f"how much is {EVM}", TOKEN_RISK),  # address beats price
        ("how much is a honeypot token worth", TOKEN_RISK),  # risk beats price
        ("is it safe to approve this contract", TOKEN_RISK),
    ],
)
def test_ordering_traps(message: str, intent: str) -> None:
    assert route_skill(message).intent == intent


def test_unknown_is_fail_safe_not_fail_open() -> None:
    plan = route_skill("gm")
    assert plan.lane in LANE_MODEL
    assert plan.refuse_when_empty is True
    assert plan.max_tokens <= 400


def test_cheap_intents_never_call_a_model() -> None:
    for message in ("price of $ETH", "how is the market"):
        plan = route_skill(message)
        assert plan.lane == LANE_TEMPLATE
        assert plan.uses_model is False
        assert plan.max_tokens == 0
        assert estimate_cost(plan)["cost_usd"] == 0.0


def test_only_deep_class_uses_deep_lane() -> None:
    deep = [
        m for m in ("explain the exploit", "crypto news today", "how do i stake") if route_skill(m).lane == LANE_DEEP
    ]
    assert deep == ["explain the exploit", "crypto news today"]


def test_depth_and_tool_match_intent() -> None:
    assert route_skill(f"scan {EVM}").tools == ("scan",)
    assert route_skill(f"who funded {EVM}").tools == ("trace",)
    assert route_skill("price of $SOL").tools == ("price", "ta")
    assert route_skill("what is a honeypot").tools == ()
    assert route_skill("explain the exploit").depth == 4
    assert route_skill("price of $SOL").depth == 1


def test_every_plan_is_valid() -> None:
    samples = (
        "",
        "gm",
        f"scan {EVM}",
        "price $BTC",
        "market",
        "news",
        "how do i bridge",
        "my pnl",
        f"who funded {SOL}",
        "exploit",
    )
    for message in samples:
        plan = route_skill(message)
        assert isinstance(plan, SkillPlan)
        assert plan.lane in LANE_MODEL
        assert 0 <= plan.depth <= 4
        assert plan.max_tokens >= 0
        assert plan.cache_ttl_s >= 0
        assert set(plan.skills) <= set(SKILL_KEYS)
        assert len(plan.skills) <= MAX_RUBRICS_PER_ANSWER
        assert plan.cite_required is True


def test_router_is_pure() -> None:
    message = f"scan {EVM}"
    first, second = route_skill(message), route_skill(message)
    assert first == second
    # Plans are immutable and shared — the saving is structural, not accidental.
    assert first is second


def test_plan_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        route_skill("gm").intent = "price"  # type: ignore[misc]


def test_cost_model_prices_tokens_and_reports_zero_marginal() -> None:
    risk = estimate_cost(route_skill(f"scan {EVM}"))
    news = estimate_cost(route_skill("explain the exploit"))
    assert risk["tokens_total"] < news["tokens_total"]
    assert risk["cost_usd"] == news["cost_usd"] == 0.0
    assert news["paid_equivalent_usd"] > risk["paid_equivalent_usd"] > 0


def test_rubrics_cover_the_five_required_policies() -> None:
    assert set(RUBRICS) == {
        "risk_explanation",
        "evidence_citation",
        "insufficient_data",
        "no_price_prediction",
        "scam_caution",
    }
    for block in RUBRICS.values():
        assert len(block) >= 3, "a rubric needs a heading plus at least two rules"
        assert block[0].split(" [")[0].isupper()


def test_compose_preserves_order_and_skips_unknown() -> None:
    text = compose(("scam_caution", "not_a_rubric", "evidence_citation"))
    assert text.index("SCAM CAUTION") < text.index("EVIDENCE CITATION")
    assert "not_a_rubric" not in text
    assert compose(()) == ""


def test_no_rubric_promises_a_price_target() -> None:
    for key in ("no_price_prediction", "risk_explanation"):
        body = compose((key,)).lower()
        for banned in ("target price", "buy now", "will pump", "guaranteed"):
            assert f"no {banned}" in body or banned not in body
