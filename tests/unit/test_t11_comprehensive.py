"""Comprehensive test suite for T11 - Domain module tests."""

import sys

sys.path.insert(0, "/home/dev/rmi/backend")

tests_passed = 0
tests_failed = 0


def run_test(name, fn):
    global tests_passed, tests_failed
    try:
        fn()
        print(f"  ✓ {name}")
        tests_passed += 1
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        tests_failed += 1


# ─────────────────────────────────────────────────────────────────────
# CITATION VALIDATOR TESTS
# ─────────────────────────────────────────────────────────────────────
print("=" * 60)
print("CITATION VALIDATOR TESTS")
print("=" * 60)

from app.domains.reports.citation_validator import validate_section  # noqa: E402


def test_valid_citation():
    result = validate_section(
        "Risk score 75/100 [1]. Token flagged [2].",
        ["Risk score 75/100 detected", "Token flagged as suspicious"],
        on_unciteable="strip",
    )
    assert result["validation_rate"] == 1.0
    assert result["unciteable_count"] == 0


def test_invalid_citation():
    result = validate_section(
        "Risk score 75/100 [99].", ["Only source 1 available"], on_unciteable="strip"
    )
    assert result["unciteable_count"] == 1
    assert "Data not available" in result["validated_text"]


def test_no_citations():
    result = validate_section("This has no citations.", ["Source text"], on_unciteable="strip")
    assert result["validation_rate"] == 0.0
    assert result["unciteable_count"] == 1


def test_empty_sources():
    result = validate_section("Some text [1].", [], on_unciteable="strip")
    assert result["validation_rate"] == 0.0
    assert "Data not available" in result["validated_text"]


def test_validation_report_structure():
    result = validate_section("Test [1].", ["Source"])
    assert "validated_text" in result
    assert "citations" in result
    assert "unciteable_count" in result
    assert "validation_rate" in result
    assert isinstance(result["citations"], list)


def test_citation_range():
    result = validate_section(
        "Token risk is high [1-2].",
        ["Token risk is high", "Risk score elevated"],
        on_unciteable="strip",
    )
    assert result["validation_rate"] == 1.0


def test_multiple_citations():
    result = validate_section(
        "Token is risky [1]. Risk factors detected [2]. High buy tax [3].",
        ["Token is risky", "Risk factors detected", "High buy tax detected"],
        on_unciteable="strip",
    )
    assert result["validation_rate"] == 1.0


def test_keep_unciteable():
    result = validate_section("Test [99].", ["Source"], on_unciteable="keep")
    assert result["unciteable_count"] == 1
    assert "Test [99]." in result["validated_text"]


print("\nRunning citation validator tests...")
run_test("test_valid_citation", test_valid_citation)
run_test("test_invalid_citation", test_invalid_citation)
run_test("test_no_citations", test_no_citations)
run_test("test_empty_sources", test_empty_sources)
run_test("test_validation_report_structure", test_validation_report_structure)
run_test("test_citation_range", test_citation_range)
run_test("test_multiple_citations", test_multiple_citations)
run_test("test_keep_unciteable", test_keep_unciteable)


# ─────────────────────────────────────────────────────────────────────
# HEALTH MODULE TESTS
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("HEALTH MODULE TESTS")
print("=" * 60)

from app.core.health import DomainHealth, register_health_check  # noqa: E402


def test_domain_health_creation():
    health = DomainHealth(name="test", healthy=True, details={"key": "value"}, latency_ms=50)
    assert health.name == "test"
    assert health.healthy is True
    assert health.details == {"key": "value"}
    assert health.latency_ms == 50
    assert health.error is None


def test_domain_health_with_error():
    health = DomainHealth(name="test", healthy=False, error="Connection failed")
    assert health.healthy is False
    assert health.error == "Connection failed"


def test_domain_health_default_details():
    health = DomainHealth(name="test", healthy=True)
    assert health.details == {}


def test_domain_health_no_latency():
    health = DomainHealth(name="test", healthy=True)
    assert health.latency_ms is None


def test_domain_health_empty_details():
    health = DomainHealth(name="test", healthy=True, details={})
    assert health.details == {}


def test_domain_health_large_details():
    health = DomainHealth(name="test", healthy=True, details={f"k{i}": f"v{i}" for i in range(100)})
    assert len(health.details) == 100


def test_health_registry():
    def mock_health():
        return DomainHealth(name="mock", healthy=True)

    register_health_check("mock", mock_health)


print("\nRunning health module tests...")
run_test("test_domain_health_creation", test_domain_health_creation)
run_test("test_domain_health_with_error", test_domain_health_with_error)
run_test("test_domain_health_default_details", test_domain_health_default_details)
run_test("test_domain_health_no_latency", test_domain_health_no_latency)
run_test("test_domain_health_empty_details", test_domain_health_empty_details)
run_test("test_domain_health_large_details", test_domain_health_large_details)
run_test("test_health_registry", test_health_registry)


# ─────────────────────────────────────────────────────────────────────
# RISK COMPUTATION TESTS
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("RISK COMPUTATION TESTS")
print("=" * 60)

from app.domains.reports.generator import _compute_risk_token, _compute_risk_wallet  # noqa: E402


def test_compute_risk_token_low():
    token_data = {
        "token": type(
            "Token",
            (),
            {
                "is_honeypot": False,
                "is_mintable": False,
                "is_proxy": False,
                "tax_buy_bps": 100,
                "tax_sell_bps": 100,
                "risk_factors": [],
            },
        )()
    }
    score, _factors, tier = _compute_risk_token(token_data)
    assert score < 25
    assert tier.name == "LOW"


def test_compute_risk_token_high():
    token_data = {
        "token": type(
            "Token",
            (),
            {
                "is_honeypot": True,
                "is_mintable": True,
                "is_proxy": True,
                "tax_buy_bps": 2000,
                "tax_sell_bps": 2000,
                "risk_factors": ["a", "b"],
            },
        )()
    }
    score, _factors, tier = _compute_risk_token(token_data)
    assert score >= 75
    assert tier.name in ["HIGH", "CRITICAL"]


def test_compute_risk_token_max():
    token_data = {
        "token": type(
            "Token",
            (),
            {
                "is_honeypot": True,
                "is_mintable": True,
                "is_proxy": True,
                "tax_buy_bps": 5000,
                "tax_sell_bps": 5000,
                "risk_factors": ["a", "b", "c", "d", "e"],
            },
        )()
    }
    score, _factors, _tier = _compute_risk_token(token_data)
    assert score == 100


def test_compute_risk_wallet_low():
    wallet_data = {
        "wallet": type("Wallet", (), {"is_suspicious": False, "tx_count": 100})(),
        "entity": {},
        "news": [],
    }
    score, _factors, tier = _compute_risk_wallet(wallet_data)
    assert score < 25
    assert tier.name == "LOW"


def test_compute_risk_wallet_high():
    wallet_data = {
        "wallet": type("Wallet", (), {"is_suspicious": True, "tx_count": 15000})(),
        "entity": {"wallets": ["a", "b", "c", "d", "e"]},
        "news": [],
    }
    score, _factors, tier = _compute_risk_wallet(wallet_data)
    assert score >= 50
    assert tier.name in ["MEDIUM", "HIGH", "CRITICAL"]


print("\nRunning risk computation tests...")
run_test("test_compute_risk_token_low", test_compute_risk_token_low)
run_test("test_compute_risk_token_high", test_compute_risk_token_high)
run_test("test_compute_risk_token_max", test_compute_risk_token_max)
run_test("test_compute_risk_wallet_low", test_compute_risk_wallet_low)
run_test("test_compute_risk_wallet_high", test_compute_risk_wallet_high)


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE FALLBACK TESTS
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("TEMPLATE FALLBACK TESTS")
print("=" * 60)

from app.domains.reports.generator import _template_fallback  # noqa: E402


def test_template_fallback_executive_summary():
    result = _template_fallback(
        "executive_summary",
        {"subject_id": "eth:0x1", "risk_score": 75, "risk_tier": "HIGH", "risk_factors": "test"},
    )
    assert "Executive Summary" in result
    assert "75" in result
    assert "HIGH" in result


def test_template_fallback_recommendation():
    result = _template_fallback(
        "recommendation",
        {"subject_id": "eth:0x1", "risk_score": 75, "risk_tier": "HIGH", "risk_factors": "test"},
    )
    assert "AVOID" in result


def test_template_fallback_onchain():
    result = _template_fallback("onchain", {"data": "test data"})
    assert "On-Chain" in result


def test_template_fallback_deployer():
    result = _template_fallback("deployer", {"deployer": "0x1", "reputation_score": 50})
    assert "Deployer" in result


def test_template_fallback_news_sentiment():
    result = _template_fallback("news_sentiment", {"news_count": 5, "avg_sentiment": "0.5"})
    assert "Sentiment" in result


def test_template_fallback_rag_findings():
    result = _template_fallback("rag_findings", {"findings": ["f1", "f2"]})
    assert "RAG" in result


def test_template_fallback_social_signals():
    result = _template_fallback("social_signals", {})
    assert "Social" in result


print("\nRunning template fallback tests...")
run_test("test_template_fallback_executive_summary", test_template_fallback_executive_summary)
run_test("test_template_fallback_recommendation", test_template_fallback_recommendation)
run_test("test_template_fallback_onchain", test_template_fallback_onchain)
run_test("test_template_fallback_deployer", test_template_fallback_deployer)
run_test("test_template_fallback_news_sentiment", test_template_fallback_news_sentiment)
run_test("test_template_fallback_rag_findings", test_template_fallback_rag_findings)
run_test("test_template_fallback_social_signals", test_template_fallback_social_signals)


# ─────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"TOTAL: {tests_passed} passed, {tests_failed} failed")
print("=" * 60)

if tests_failed > 0:
    sys.exit(1)
else:
    print("\n✅ All 24 tests passed!")
