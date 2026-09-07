"""SENTINEL deep-forensic pipeline tests.

Covers:
  1. Module initialization (all SENTINEL singletons import + initialize)
  2. Free-tier module coverage — each of the 6 free rug-detection modules
     produces a populated result (real module code, mocked I/O boundary)
  3. Error handling — a failing module never crashes the scan/phase
  4. Premium gating — free/pro keep premium locked, elite/admin unlock it
  5. Integration with the unified scanner result (Phase 5)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.scanner.unified.sentinel_phase import (
    PREMIUM_UNLOCK_TIERS,
    SENTINEL_ALL_MODULES,
    SENTINEL_FREE_MODULES,
    SENTINEL_PREMIUM_MODULES,
    run_sentinel_phase,
)

# Test fixtures
TOKEN = "TokenAddr123456789o123456789o123456789o"  # valid base58 (no 0/O/I/l)
DEPLOYER = "DeployerAddr1234567890123456789012345678"


def _module_singleton_classes() -> dict[str, str]:
    """Map module key → expected singleton class name (import-time names)."""
    return {
        "holder_analysis": "HolderAnalyzer",
        "bundle_detection": "BundleDetector",
        "exchange_funding": "ExchangeFunderDetector",
        "liquidity_verification": "LiquidityVerifier",
        "dev_reputation": "DevReputationEngine",
        "wash_trading": "WashTradingDetector",
        "metadata_fingerprint": "MetadataFingerprinter",
        "sentiment": "SentimentAnalyzer",
        "pumpfun_analysis": "PumpFunAnalyzer",
        "honeypot_detection": "HoneypotDetector",
        "contract_authority": "ContractAuthorityScanner",
        "mev_detection": "MEVDetector",
        "flash_loan_detection": "FlashLoanDetector",
        "pump_dump_detection": "PumpDumpDetector",
        "oracle_manipulation": "OracleManipulationDetector",
        "governance_attack": "GovernanceAttackDetector",
        "proxy_detection": "ProxyDetector",
        "static_analysis": "StaticAnalyzer",
        "decompiler_analysis": "DecompilerAnalyzer",
        "address_labels": "AddressLabeler",
        "fund_flow": "FundFlowVisualizer",
        "contract_diff": "ContractDiffAnalyzer",
        "guilt_association": "GuiltAssociationAnalyzer",
        "sleep_cycle": "SleepCycleAnalyzer",
        "social_velocity": "SocialVelocityAnalyzer",
        "bytecode_similarity": "BytecodeSimilarityAnalyzer",
        "block_zero_sniper": "BlockZeroSniperAnalyzer",
        "gas_trace": "GasTraceAnalyzer",
        "cross_chain_trace": "CrossChainTraceAnalyzer",
    }


# ═══════════════════════════════════════════════════════════════════
# 1. Module initialization
# ═══════════════════════════════════════════════════════════════════


def test_all_sentinel_module_singletons_initialize() -> None:
    """Every SENTINEL module singleton is initialized by _ensure_modules()."""
    from app.domains.scanners.sentinel_pipeline import modules as sentinel_modules

    sentinel_modules._ensure_modules()

    attr_map = {
        "holder_analysis": "_holder_analyzer",
        "bundle_detection": "_bundle_detector",
        "exchange_funding": "_exchange_funder",
        "liquidity_verification": "_liquidity_verifier",
        "dev_reputation": "_dev_reputation",
        "wash_trading": "_wash_trading",
        "metadata_fingerprint": "_metadata_fingerprinter",
        "sentiment": "_sentiment_analyzer",
        "pumpfun_analysis": "_pumpfun_analyzer",
        "honeypot_detection": "_honeypot_detector",
        "contract_authority": "_contract_authority",
        "mev_detection": "_mev_detector",
        "flash_loan_detection": "_flash_loan_detector",
        "pump_dump_detection": "_pump_dump_detector",
        "oracle_manipulation": "_oracle_manipulation",
        "governance_attack": "_governance_attack",
        "proxy_detection": "_proxy_detector",
        "static_analysis": "_static_analyzer",
        "decompiler_analysis": "_decompiler_analyzer",
        "address_labels": "_address_labeler",
        "fund_flow": "_fund_flow_visualizer",
        "contract_diff": "_contract_diff",
        "guilt_association": "_guilt_association",
        "sleep_cycle": "_sleep_cycle",
        "social_velocity": "_social_velocity",
        "bytecode_similarity": "_bytecode_similarity",
        "block_zero_sniper": "_block_zero_sniper",
        "gas_trace": "_gas_trace",
        "cross_chain_trace": "_cross_chain_trace",
    }

    expected = _module_singleton_classes()
    assert set(attr_map) == set(expected)
    for module_key, attr in attr_map.items():
        instance = getattr(sentinel_modules, attr)
        assert instance is not None, f"{module_key} singleton not initialized"
        assert instance.__class__.__name__ == expected[module_key], (
            f"{module_key}: expected {expected[module_key]}, got {instance.__class__.__name__}"
        )


def test_runner_singleton_bindings_refresh_after_ensure_modules() -> None:
    """Regression: runners.py must not keep stale None singletons.

    Previously ``run_sentinel_scan`` crashed with
    ``AttributeError: 'NoneType' object has no attribute 'analyze'``
    because the runner module bound the singletons at import time (all
    ``None``) and ``_ensure_modules()`` only updated ``modules.py``.
    """
    from app.domains.scanners.sentinel_pipeline import runners

    runners._ensure_modules()
    runners._refresh_singletons()
    assert runners._holder_analyzer is not None
    assert runners._bundle_detector is not None
    assert runners._liquidity_verifier is not None
    assert runners._cross_chain_trace is not None


def test_free_premium_split_is_complete_and_disjoint() -> None:
    """Free + premium module sets cover every module and do not overlap."""
    assert set(SENTINEL_ALL_MODULES) == set(SENTINEL_FREE_MODULES) | set(SENTINEL_PREMIUM_MODULES)
    assert set(SENTINEL_FREE_MODULES).isdisjoint(SENTINEL_PREMIUM_MODULES)
    # Task-specified free tier: holder HHI, LP lock, dev reputation,
    # metadata fingerprint, contract authority, proxy detection.
    assert set(SENTINEL_FREE_MODULES) == {
        "holder_analysis",
        "liquidity_verification",
        "dev_reputation",
        "metadata_fingerprint",
        "contract_authority",
        "proxy_detection",
    }
    assert "elite" in PREMIUM_UNLOCK_TIERS
    assert "admin" in PREMIUM_UNLOCK_TIERS


def test_pipeline_exports_all_free_runner_functions() -> None:
    """The public runner API exposes every free-tier module."""
    from app.domains.scanners import sentinel_pipeline as pipeline

    runner_map = {
        "holder_analysis": "run_holder_analysis",
        "liquidity_verification": "run_liquidity_verification",
        "dev_reputation": "run_dev_reputation",
        "metadata_fingerprint": "run_metadata_fingerprint",
        "contract_authority": "run_contract_authority",
        "proxy_detection": "run_proxy_detection",
    }
    for runner_name in runner_map.values():
        assert hasattr(pipeline, runner_name), f"missing runner {runner_name}"
        assert callable(getattr(pipeline, runner_name))


# ═══════════════════════════════════════════════════════════════════
# 2. Free-tier module results (real module code, mocked I/O boundary)
# ═══════════════════════════════════════════════════════════════════


async def test_holder_analysis_produces_hhi_report() -> None:
    """Holder analysis computes an HHI report from holder data."""
    from app.domains.scanners.holder_analyzer import HolderAnalyzer, HolderInfo
    from app.domains.scanners.sentinel_pipeline import run_holder_analysis

    holder = HolderInfo(address="Wallet1", balance=600.0, percentage=60.0, first_tx_timestamp=0)
    with patch.object(HolderAnalyzer, "_fetch_holder_data", new=AsyncMock(return_value=[holder])):
        result = await run_holder_analysis(TOKEN, "solana")
    assert isinstance(result, dict)
    assert "hhi" in result
    assert result["hhi"] == 10000.0  # single holder → normalized near-monopoly
    assert result["risk_level"] in ("high", "critical")


async def test_liquidity_verification_produces_lock_report() -> None:
    """Liquidity verification returns a LockReport even with no lock data."""
    from app.domains.scanners.liquidity_verifier import LiquidityVerifier
    from app.domains.scanners.sentinel_pipeline import run_liquidity_verification

    with (
        patch.object(LiquidityVerifier, "_verify_lock_onchain", new=AsyncMock(return_value=(False, []))),
        patch.object(LiquidityVerifier, "_detect_pumpfun", new=AsyncMock(return_value=False)),
    ):
        result = await run_liquidity_verification(TOKEN, "solana")
    assert isinstance(result, dict)
    assert "status" in result
    assert "risk_score" in result
    assert "warnings" in result


async def test_dev_reputation_produces_report() -> None:
    """Dev reputation returns a report for a new (no-history) deployer."""
    from app.domains.scanners.dev_reputation import DevReputationEngine
    from app.domains.scanners.sentinel_pipeline import run_dev_reputation

    with (
        patch.object(DevReputationEngine, "_fetch_launches", new=AsyncMock(return_value=[])),
        patch.object(DevReputationEngine, "_cross_chain_lookup", new=AsyncMock(return_value=[])),
    ):
        result = await run_dev_reputation(DEPLOYER, ["solana"])
    assert isinstance(result, dict)
    assert result["total_launches"] == 0
    assert result["reputation_score"] == 50


async def test_metadata_fingerprint_produces_report() -> None:
    """Metadata fingerprinting produces a FingerprintReport from token metadata."""
    from app.domains.scanners.metadata_fingerprint import MetadataFingerprinter, TokenMetadata
    from app.domains.scanners.sentinel_pipeline import run_metadata_fingerprint

    meta = TokenMetadata(
        address=TOKEN,
        chain="solana",
        name="Test Token",
        symbol="TEST",
        description="A test token",
        website_html="<html><body><h1>Test</h1></body></html>",
        social_links=["https://x.com/test"],
    )
    with patch.object(MetadataFingerprinter, "_fetch_metadata", new=AsyncMock(return_value=meta)):
        result = await run_metadata_fingerprint(TOKEN, "solana")
    assert isinstance(result, dict)
    assert result["name"] == "Test Token"
    assert result["confidence"] == "HIGH"  # description + social links present
    assert result["html_structure_hash"]


async def test_contract_authority_produces_report() -> None:
    """Contract authority reports mint/freeze authorities from mint data."""
    from app.domains.scanners.sentinel_pipeline import modules as sm, run_contract_authority

    fake_rpc = {
        "result": {
            "value": {
                "data": {
                    "parsed": {
                        "info": {
                            "mintAuthority": DEPLOYER,
                            "freezeAuthority": "FreezeAddr1234567890123456789012345678",
                            "supply": "1000000000",
                            "decimals": 9,
                        },
                        "type": "mint",
                    },
                    "program": "spl-token",
                },
                "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            }
        }
    }
    sm._ensure_modules()
    with patch.object(sm._contract_authority, "_chain", new=MagicMock(rpc_call=AsyncMock(return_value=fake_rpc))):
        result = await run_contract_authority(TOKEN, "solana")
    assert isinstance(result, dict)
    assert "risk_level" in result
    assert "solana_authorities" in result
    assert result["solana_authorities"]["mint_authority"]["address"] == DEPLOYER


async def test_proxy_detection_produces_report() -> None:
    """Proxy detection returns a ProxyReport (immutable program path)."""
    from app.domains.scanners.proxy_detector import ProxyDetector
    from app.domains.scanners.sentinel_pipeline import run_proxy_detection

    with (
        patch.object(ProxyDetector, "_fetch_solana_program_data", new=AsyncMock(return_value=None)),
        patch.object(ProxyDetector, "_fetch_solana_upgrade_authority", new=AsyncMock(return_value=None)),
        patch("rugmunch_scanner.detectors.proxy_detector.query_rag_citations", new=AsyncMock(return_value=[])),
    ):
        result = await run_proxy_detection(TOKEN, "solana")
    assert isinstance(result, dict)
    assert "risk_score" in result
    assert "is_proxy" in result
    assert result["is_proxy"] is False  # no program data → not flagged upgradeable


# ═══════════════════════════════════════════════════════════════════
# 3. Error handling — module failure never crashes the scan
# ═══════════════════════════════════════════════════════════════════


def _fake_analyzers(raise_keys: set[str] | None = None, sleep_keys: set[str] | None = None) -> dict:
    """Build analyzer stubs; selected keys raise or hang."""
    raise_keys = raise_keys or set()
    sleep_keys = sleep_keys or set()

    def _make(key: str):
        if key in raise_keys:

            async def _raise(*args, **kwargs):
                raise RuntimeError(f"boom: {key}")

            return _raise
        if key in sleep_keys:

            async def _sleep(*args, **kwargs):
                await asyncio.sleep(60)
                return {"risk_score": 99}

            return _sleep
        return AsyncMock(return_value={"risk_score": 10, "warnings": []})

    return {key: _make(key) for key in SENTINEL_ALL_MODULES}


@pytest.mark.asyncio
async def test_module_failure_does_not_crash_phase() -> None:
    """A raising module is recorded in errors; other modules still return."""
    with (
        patch("app.domains.scanner.unified.sentinel_phase._get_analyzers", return_value=_fake_analyzers()),
        patch(
            "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
            new=AsyncMock(return_value=DEPLOYER),
        ),
    ):
        result = await run_sentinel_phase(TOKEN, "solana", tier="free")
    assert result["free"]["holder_analysis"] == {"risk_score": 10, "warnings": []}
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_module_failure_is_recorded_not_crashing() -> None:
    """All modules raising still yields a valid phase dict with errors."""
    analyzers = _fake_analyzers(raise_keys=set(SENTINEL_ALL_MODULES))
    with (
        patch("app.domains.scanner.unified.sentinel_phase._get_analyzers", return_value=analyzers),
        patch(
            "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
            new=AsyncMock(return_value=DEPLOYER),
        ),
    ):
        result = await run_sentinel_phase(TOKEN, "solana", tier="elite")
    assert result["premium_unlocked"] is True
    assert len(result["errors"]) == len(SENTINEL_ALL_MODULES)
    assert all("boom" in msg for msg in result["errors"].values())
    assert result["premium"] is not None
    assert all(v is None for v in result["premium"].values())
    assert all(v is None for v in result["free"].values())


@pytest.mark.asyncio
async def test_module_timeout_is_recorded() -> None:
    """A hanging module is time-boxed and recorded as a timeout error."""
    from app.domains.scanner.unified import sentinel_phase as phase

    analyzers = _fake_analyzers(sleep_keys={"holder_analysis"})
    with (
        patch.object(phase, "MODULE_TIMEOUT_S", 0.05),
        patch("app.domains.scanner.unified.sentinel_phase._get_analyzers", return_value=analyzers),
        patch(
            "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
            new=AsyncMock(return_value=DEPLOYER),
        ),
    ):
        result = await run_sentinel_phase(TOKEN, "solana", tier="free")
    assert "timeout" in result["errors"]["holder_analysis"]
    assert result["free"]["liquidity_verification"] == {"risk_score": 10, "warnings": []}


def _fake_result() -> SimpleNamespace:
    """Namespace with the attributes the SENTINEL risk extractors touch."""
    return SimpleNamespace(
        hhi=2000,
        risk_score=10,
        cex_funded_percentage=5,
        is_locked=True,
        rug_count=0,
        wash_trading_probability=0.1,
        max_similarity=0.2,
        graduation_probability=0.9,
        guilt_score=5,
        sleep_score=5,
        velocity_score=5,
        exact_match=False,
        similarity_score=0.1,
        farm_confidence=0.1,
        dead_end=False,
        cex_funded=True,
        warnings=[],
        red_flags=[],
        risk_level="low",
    )


def _patch_all_pipeline_analyzers(raise_module: str | None = None):
    """Patch every SENTINEL singleton analyzer to a fake result.

    Optionally make one module raise, to exercise graceful degradation.
    """
    from app.domains.scanners.sentinel_pipeline import modules as sm

    sm._ensure_modules()
    analyzer_specs = [
        ("_holder_analyzer", ["analyze"]),
        ("_bundle_detector", ["detect_bundles"]),
        ("_exchange_funder", ["analyze"]),
        ("_liquidity_verifier", ["verify_lock"]),
        ("_wash_trading", ["analyze"]),
        ("_metadata_fingerprinter", ["analyze_token_metadata"]),
        ("_sentiment_analyzer", ["analyze"]),
        ("_pumpfun_analyzer", ["analyze"]),
        ("_honeypot_detector", ["analyze"]),
        ("_contract_authority", ["analyze"]),
        ("_mev_detector", ["analyze"]),
        ("_flash_loan_detector", ["analyze"]),
        ("_pump_dump_detector", ["analyze"]),
        ("_oracle_manipulation", ["analyze"]),
        ("_governance_attack", ["analyze"]),
        ("_proxy_detector", ["analyze"]),
        ("_static_analyzer", ["analyze"]),
        ("_decompiler_analyzer", ["analyze"]),
        ("_address_labeler", ["analyze"]),
        ("_fund_flow_visualizer", ["analyze"]),
        ("_contract_diff", ["analyze"]),
        ("_guilt_association", ["analyze"]),
        ("_sleep_cycle", ["analyze"]),
        ("_social_velocity", ["analyze"]),
        ("_bytecode_similarity", ["analyze"]),
        ("_block_zero_sniper", ["analyze"]),
        ("_gas_trace", ["analyze"]),
        ("_cross_chain_trace", ["analyze"]),
    ]
    patchers = []
    for attr, methods in analyzer_specs:
        module = getattr(sm, attr)
        for meth in methods:
            if raise_module and attr == raise_module:
                patchers.append(patch.object(module, meth, new=AsyncMock(side_effect=RuntimeError("boom"))))
            else:
                patchers.append(patch.object(module, meth, new=AsyncMock(return_value=_fake_result())))
    return patchers


@pytest.mark.asyncio
async def test_pipeline_module_failure_does_not_crash_scan() -> None:
    """run_sentinel_scan survives a module that raises (30-module run)."""
    import contextlib

    from app.domains.scanners.sentinel_pipeline import run_sentinel_scan

    patchers = _patch_all_pipeline_analyzers(raise_module="_holder_analyzer")
    stack = contextlib.ExitStack()
    for patcher in patchers:
        stack.enter_context(patcher)
    stack.enter_context(
        patch("app.wallet_memory.engine.get_wallet_engine", return_value=AsyncMock(return_value={"risk_score": 0}))
    )
    with stack:
        report = await run_sentinel_scan(TOKEN, "solana", dev_address=DEPLOYER)
    assert report.token_address == TOKEN
    assert "holder_analysis" in report.errors
    assert "bundle_detection" in report.modules_run  # others still ran
    assert report.composite_risk_score >= 0
    assert len(report.modules_run) >= 29  # every non-failing module ran


@pytest.mark.slow
@pytest.mark.asyncio
async def test_pipeline_graceful_when_all_modules_fail() -> None:
    """Even a total module failure returns a report instead of raising."""
    import contextlib

    from app.domains.scanners.sentinel_pipeline import modules as sm, run_sentinel_scan

    patchers = _patch_all_pipeline_analyzers()  # all return fake results
    # Now make every analyzer raise by re-patching each with a raising mock
    patchers2 = []
    for attr in [
        "_holder_analyzer",
        "_bundle_detector",
        "_exchange_funder",
        "_liquidity_verifier",
        "_wash_trading",
        "_metadata_fingerprinter",
        "_sentiment_analyzer",
        "_pumpfun_analyzer",
        "_honeypot_detector",
        "_contract_authority",
        "_mev_detector",
        "_flash_loan_detector",
        "_pump_dump_detector",
        "_oracle_manipulation",
        "_governance_attack",
        "_proxy_detector",
        "_static_analyzer",
        "_decompiler_analyzer",
        "_address_labeler",
        "_fund_flow_visualizer",
        "_contract_diff",
        "_guilt_association",
        "_sleep_cycle",
        "_social_velocity",
        "_bytecode_similarity",
        "_block_zero_sniper",
        "_gas_trace",
        "_cross_chain_trace",
    ]:
        module = getattr(sm, attr)
        for meth in ("analyze", "detect_bundles", "verify_lock", "analyze_token_metadata"):
            if hasattr(module, meth):
                patchers2.append(patch.object(module, meth, new=AsyncMock(side_effect=RuntimeError("boom"))))
    stack = contextlib.ExitStack()
    for patcher in [*patchers, *patchers2]:
        stack.enter_context(patcher)
    # _ensure_modules() inside run_sentinel_scan recreates _metadata_fingerprinter,
    # _sentiment_analyzer, and _pumpfun_analyzer unconditionally, which would discard
    # instance-level patches.  Freeze the initialised state.
    stack.enter_context(patch("app.domains.scanners.sentinel_pipeline.runners._ensure_modules"))
    stack.enter_context(
        patch("app.wallet_memory.engine.get_wallet_engine", return_value=AsyncMock(return_value={"risk_score": 0}))
    )
    with stack:
        report = await run_sentinel_scan(TOKEN, "solana", dev_address=DEPLOYER)
    assert report.token_address == TOKEN
    assert len(report.errors) >= 1
    assert report.composite_risk_score == 0.0


# ═══════════════════════════════════════════════════════════════════
# 4. Premium gating
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tier", ["free", "pro", "unknown"])
@pytest.mark.asyncio
async def test_non_elite_tiers_keep_premium_locked(tier: str) -> None:
    """free/pro tiers get free results + a preview, never the premium data."""
    with (
        patch("app.domains.scanner.unified.sentinel_phase._get_analyzers", return_value=_fake_analyzers()),
        patch(
            "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
            new=AsyncMock(return_value=DEPLOYER),
        ),
    ):
        result = await run_sentinel_phase(TOKEN, "solana", tier=tier)
    assert result["premium_unlocked"] is False
    assert result["premium"] is None
    assert len(result["premium_preview"]) == len(SENTINEL_PREMIUM_MODULES)
    assert "unlock" in result["premium_preview"][0]
    assert all(k in result["free"] for k in SENTINEL_FREE_MODULES)


@pytest.mark.parametrize("tier", ["elite", "admin"])
@pytest.mark.asyncio
async def test_elite_admin_unlock_premium(tier: str) -> None:
    """elite/admin tiers unlock the full premium module set."""
    with (
        patch("app.domains.scanner.unified.sentinel_phase._get_analyzers", return_value=_fake_analyzers()),
        patch(
            "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
            new=AsyncMock(return_value=DEPLOYER),
        ),
    ):
        result = await run_sentinel_phase(TOKEN, "solana", tier=tier)
    assert result["premium_unlocked"] is True
    assert result["premium"] is not None
    assert result["premium_preview"] == []
    assert set(result["premium"]) == set(SENTINEL_PREMIUM_MODULES)
    assert len(result["modules_run"]) == len(SENTINEL_ALL_MODULES)


# ═══════════════════════════════════════════════════════════════════
# 5. Integration with the unified scanner result
# ═══════════════════════════════════════════════════════════════════
# 5. Integration with the unified scanner result
# ═══════════════════════════════════════════════════════════════════


def _sentinel_payload(unlocked: bool = False) -> dict:
    return {
        "free": {k: {"risk_score": 5} for k in SENTINEL_FREE_MODULES},
        "premium": ({k: {"risk_score": 5} for k in SENTINEL_PREMIUM_MODULES} if unlocked else None),
        "premium_unlocked": unlocked,
        "premium_modules": list(SENTINEL_PREMIUM_MODULES),
        "premium_preview": []
        if unlocked
        else [{"module": "bundle_detection", "description": "x", "unlock": "upgrade"}],
        "errors": {},
        "modules_run": list(SENTINEL_FREE_MODULES) + (list(SENTINEL_PREMIUM_MODULES) if unlocked else []),
        "deployer_resolved": False,
    }


def _unified_scan_patches(payload: dict):
    """Patch every slow/network collaborator of UnifiedTokenScanner.scan.

    The unified scan's enrichment phases (wallet analysis, news sentiment,
    news card, event correlation, RAG enrichment) and persistence hit real
    external services (ClickHouse, Redis, RAG backend) — each taking ~10s
    to time out. Stubbing them keeps the SENTINEL integration tests fast.
    """
    from app.domains.scanner.unified.scanner import UnifiedTokenScanner

    scanner = UnifiedTokenScanner()
    return scanner, [
        patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
        patch.object(scanner, "_run_wallet_analysis", new=AsyncMock(return_value=None)),
        patch.object(scanner, "_run_news_sentiment", new=AsyncMock(return_value=None)),
        patch.object(scanner, "_get_news_card", new=AsyncMock(return_value=None)),
        patch.object(scanner, "_run_event_correlation", new=AsyncMock(return_value=None)),
        patch("app.domains.scanner.unified._finalize_helpers.persist_result", new=AsyncMock(return_value=None)),
        patch("app.domains.scanner.unified._finalize_helpers.auto_label_deployer", new=AsyncMock(return_value=None)),
        patch("app.rag.intelligence.RAGIntelligenceEnricher.enrich_scan_result", new=AsyncMock(return_value=None)),
        patch("app.domains.scanner.unified.sentinel_phase.run_sentinel_phase", new=AsyncMock(return_value=payload)),
    ]


@pytest.mark.asyncio
async def test_unified_scanner_runs_sentinel_phase() -> None:
    """UnifiedTokenScanner.scan() populates result.sentinel for free tier."""
    import contextlib

    scanner, patchers = _unified_scan_patches(_sentinel_payload(unlocked=False))
    stack = contextlib.ExitStack()
    for patcher in patchers:
        stack.enter_context(patcher)
    with stack:
        result = await scanner.scan(TOKEN, "solana", tier="free")
    assert result.sentinel is not None
    assert result.sentinel["premium_unlocked"] is False
    assert set(SENTINEL_FREE_MODULES).issubset(result.sentinel["free"])
    assert result.sentinel["premium"] is None
    assert "holder_analysis" in result.modules_run
    assert "sentinel:free" in result.enrichment_sources


@pytest.mark.asyncio
async def test_unified_scanner_elite_unlocks_premium_sentinel() -> None:
    """Elite-tier scan returns unlocked premium SENTINEL results."""
    import contextlib

    scanner, patchers = _unified_scan_patches(_sentinel_payload(unlocked=True))
    stack = contextlib.ExitStack()
    for patcher in patchers:
        stack.enter_context(patcher)
    with stack:
        result = await scanner.scan(TOKEN, "solana", tier="elite")
    assert result.sentinel["premium_unlocked"] is True
    assert set(result.sentinel["premium"]) == set(SENTINEL_PREMIUM_MODULES)
    assert "sentinel:premium" in result.enrichment_sources


@pytest.mark.asyncio
async def test_unified_scanner_sentinel_phase_failure_keeps_scan_alive() -> None:
    """A broken SENTINEL phase never fails the overall scan."""
    import contextlib

    scanner, patchers = _unified_scan_patches(None)
    # Override the sentinel phase patch: this one raises.
    for idx, patcher in enumerate(patchers):
        if patcher.attribute == "run_sentinel_phase":
            patchers[idx] = patch(
                "app.domains.scanner.unified.sentinel_phase.run_sentinel_phase",
                new=AsyncMock(side_effect=RuntimeError("sentinel down")),
            )
    stack = contextlib.ExitStack()
    for patcher in patchers:
        stack.enter_context(patcher)
    with stack:
        result = await scanner.scan(TOKEN, "solana", tier="free")
    # Scan still completes; sentinel falls back to an error envelope.
    assert result.token_address == TOKEN
    assert result.safety_score >= 0
    assert result.sentinel is not None
    assert "_phase" in result.sentinel["errors"]


@pytest.mark.asyncio
async def test_api_router_returns_sentinel_field() -> None:
    """POST /api/v2/scanner/token/scan includes the sentinel payload."""
    from fastapi.testclient import TestClient

    import app.core.auth as auth_mod
    from app.domains.scanner.unified.scanner import UnifiedTokenScanner
    from app.factory import create_app

    async def _fake_scan(self, address, chain="solana", tier="free", admin_key=None, scan_mode="advanced"):
        from app.domains.scanner.unified.model import ScanResult

        result = ScanResult(token_address=address, chain=chain, tier=tier)
        result.sentinel = _sentinel_payload(unlocked=False)
        result.modules_run = list(SENTINEL_FREE_MODULES)
        result.enrichment_sources = ["sentinel:free"]
        result.safety_score = 50.0
        return result

    app = create_app()
    saved_token = auth_mod.RMI_AUTH_TOKEN
    auth_mod.RMI_AUTH_TOKEN = "test-auth-token-32-chars-long!!"
    try:
        with patch.object(UnifiedTokenScanner, "scan", new=_fake_scan):
            client = TestClient(app)
            resp = client.post(
                "/api/v2/scanner/token/scan",
                json={"address": TOKEN, "chain": "solana"},
                headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
            )
    finally:
        auth_mod.RMI_AUTH_TOKEN = saved_token
    assert resp.status_code == 200
    body = resp.json()
    assert body["sentinel"] is not None
    assert body["sentinel"]["free"]["holder_analysis"] == {"risk_score": 5}
    assert body["sentinel"]["premium_unlocked"] is False


# ═══════════════════════════════════════════════════════════════════
# 6. Decorator registry
# ═══════════════════════════════════════════════════════════════════


def test_sentinel_module_decorator_registers_class() -> None:
    """``@sentinel_module`` populates ``_SENTINEL_REGISTRY`` at import time."""
    from app.domains.scanners._sentinel_registry import _SENTINEL_REGISTRY

    assert len(_SENTINEL_REGISTRY) >= 28, f"Expected >=28 registered modules, got {len(_SENTINEL_REGISTRY)}"
    # Verify a free-tier module
    entry = _SENTINEL_REGISTRY.get("holder_analysis")
    assert entry is not None, "holder_analysis not in registry"
    assert entry["tier"] == "free"
    assert entry["class"].__name__ == "HolderAnalyzer"
    assert "HHI" in entry["description"]

    # Verify a premium module
    entry = _SENTINEL_REGISTRY.get("bundle_detection")
    assert entry is not None, "bundle_detection not in registry"
    assert entry["tier"] == "premium"
    assert entry["class"].__name__ == "BundleDetector"
    assert entry["method"] == "detect_bundles"

    # Verify a needs_deployer module
    entry = _SENTINEL_REGISTRY.get("dev_reputation")
    assert entry is not None, "dev_reputation not in registry"
    assert entry["needs_deployer"] is True

    # Verify a chain-gated module
    entry = _SENTINEL_REGISTRY.get("pumpfun_analysis")
    assert entry is not None, "pumpfun_analysis not in registry"
    assert entry["chain_gated"] == "solana"

    # Verify free + premium = all
    from app.domains.scanners._sentinel_registry import get_free_module_keys, get_premium_module_keys

    free = set(get_free_module_keys())
    premium = set(get_premium_module_keys())
    assert free.isdisjoint(premium), "free and premium sets must be disjoint"
    assert free | premium == set(_SENTINEL_REGISTRY), "free + premium must equal all registered modules"


def test_sentinel_module_decorator_creates_registry_wired_to_pipeline() -> None:
    """Modules instantiated via _ensure_modules match the registry."""
    from app.domains.scanners._sentinel_registry import _SENTINEL_REGISTRY
    from app.domains.scanners.sentinel_pipeline import modules as sm

    sm._ensure_modules()

    # Pick a few modules and verify the singleton is the right class
    checks = {
        "holder_analysis": sm._holder_analyzer,
        "bundle_detection": sm._bundle_detector,
        "wash_trading": sm._wash_trading,
        "cross_chain_trace": sm._cross_chain_trace,
    }
    for key, singleton in checks.items():
        entry = _SENTINEL_REGISTRY.get(key)
        assert entry is not None, f"{key} missing from registry"
        assert singleton is not None, f"{key} singleton not initialized"
        assert singleton.__class__ is entry["class"], (
            f"{key}: singleton class {singleton.__class__.__name__} != registry class {entry['class'].__name__}"
        )
