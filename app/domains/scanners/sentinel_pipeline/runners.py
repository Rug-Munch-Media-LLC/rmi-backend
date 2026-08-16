"""
SENTINEL - Pipeline Orchestrator & Standalone Module Runners
=============================================================
Main run_sentinel_scan orchestrator plus dynamically-generated standalone runners.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import get_logger

from .config import SentinelConfig, SentinelReport, dataclass_to_dict
from .modules import (
    _address_labeler,
    _block_zero_sniper,
    _bundle_detector,
    _bytecode_similarity,
    _contract_authority,
    _contract_diff,
    _cross_chain_trace,
    _decompiler_analyzer,
    _dev_reputation,
    _ensure_modules,
    _exchange_funder,
    _flash_loan_detector,
    _fund_flow_visualizer,
    _gas_trace,
    _governance_attack,
    _guilt_association,
    _holder_analyzer,
    _honeypot_detector,
    _liquidity_verifier,
    _metadata_fingerprinter,
    _mev_detector,
    _oracle_manipulation,
    _proxy_detector,
    _pump_dump_detector,
    _pumpfun_analyzer,
    _sentiment_analyzer,
    _sleep_cycle,
    _social_velocity,
    _static_analyzer,
    _wash_trading,
)
from .risk import (
    _collect_red_flags,
    _compute_composite,
    _extract_risk_block_zero,
    _extract_risk_bundle,
    _extract_risk_bytecode_similarity,
    _extract_risk_dev,
    _extract_risk_exchange_fund,
    _extract_risk_fingerprint,
    _extract_risk_gas_trace,
    _extract_risk_guilt_association,
    _extract_risk_hhi,
    _extract_risk_lock,
    _extract_risk_pumpfun,
    _extract_risk_sentiment,
    _extract_risk_sleep_cycle,
    _extract_risk_social_velocity,
    _extract_risk_wash_trading,
)

logger = get_logger("sentinel_pipeline")

try:
    from app.wallet_memory.engine import get_wallet_engine

    _WALLET_MEMORY_AVAILABLE_LOCAL = True
except ImportError:
    _WALLET_MEMORY_AVAILABLE_LOCAL = False


def _refresh_singletons() -> None:
    """Re-bind module singletons after ``_ensure_modules()``.

    The ``from .modules import _holder_analyzer`` bindings at the top of
    this module capture the singleton values at import time — which are
    ``None`` until ``_ensure_modules()`` runs. Re-reading the current
    values from the ``modules`` module fixes the stale bindings so
    ``run_sentinel_scan`` (and the paid x402 SENTINEL endpoint) actually
    see initialized analyzers instead of crashing with
    ``AttributeError: 'NoneType' object has no attribute 'analyze'``.
    """
    global _address_labeler, _block_zero_sniper, _bundle_detector
    global _bytecode_similarity, _contract_authority, _contract_diff
    global _cross_chain_trace, _decompiler_analyzer, _dev_reputation
    global _exchange_funder, _flash_loan_detector, _fund_flow_visualizer
    global _gas_trace, _governance_attack, _guilt_association, _holder_analyzer
    global _honeypot_detector, _liquidity_verifier, _metadata_fingerprinter
    global _mev_detector, _oracle_manipulation, _proxy_detector
    global _pump_dump_detector, _pumpfun_analyzer, _sentiment_analyzer
    global _sleep_cycle, _social_velocity, _static_analyzer, _wash_trading

    from . import modules as _modules

    _address_labeler = _modules._address_labeler
    _block_zero_sniper = _modules._block_zero_sniper
    _bundle_detector = _modules._bundle_detector
    _bytecode_similarity = _modules._bytecode_similarity
    _contract_authority = _modules._contract_authority
    _contract_diff = _modules._contract_diff
    _cross_chain_trace = _modules._cross_chain_trace
    _decompiler_analyzer = _modules._decompiler_analyzer
    _dev_reputation = _modules._dev_reputation
    _exchange_funder = _modules._exchange_funder
    _flash_loan_detector = _modules._flash_loan_detector
    _fund_flow_visualizer = _modules._fund_flow_visualizer
    _gas_trace = _modules._gas_trace
    _governance_attack = _modules._governance_attack
    _guilt_association = _modules._guilt_association
    _holder_analyzer = _modules._holder_analyzer
    _honeypot_detector = _modules._honeypot_detector
    _liquidity_verifier = _modules._liquidity_verifier
    _metadata_fingerprinter = _modules._metadata_fingerprinter
    _mev_detector = _modules._mev_detector
    _oracle_manipulation = _modules._oracle_manipulation
    _proxy_detector = _modules._proxy_detector
    _pump_dump_detector = _modules._pump_dump_detector
    _pumpfun_analyzer = _modules._pumpfun_analyzer
    _sentiment_analyzer = _modules._sentiment_analyzer
    _sleep_cycle = _modules._sleep_cycle
    _social_velocity = _modules._social_velocity
    _static_analyzer = _modules._static_analyzer
    _wash_trading = _modules._wash_trading


# ═══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════


async def _run_wallet_map(token_address: str, chain: str, deployer: str | None) -> dict[str, Any] | None:
    """First-20 wallet map engine (fail-open; never raises)."""
    try:
        from app.domains.scanner.engines.own_wallet_map import get_own_wallet_map

        return await get_own_wallet_map(token_address, chain, deployer=deployer)
    except Exception as exc:
        logger.debug("wallet_map_engine_failed: %s", exc)
        return None


async def _run_deployer_timeline(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Deployer history timeline engine (fail-open; never raises)."""
    try:
        from app.domains.scanner.engines.own_deployer_timeline import get_own_deployer_timeline

        return await get_own_deployer_timeline(deployer_address, chain)
    except Exception as exc:
        logger.debug("deployer_timeline_engine_failed: %s", exc)
        return None


async def _run_cluster_analysis(token_address: str, chain: str, deployer: str | None) -> dict[str, Any] | None:
    """Funding-source cluster + coordinated-buy analysis engine (fail-open)."""
    try:
        from app.domains.scanner.engines.own_cluster_analysis import get_own_cluster_analysis

        return await get_own_cluster_analysis(token_address, chain, deployer=deployer)
    except Exception as exc:
        logger.debug("cluster_analysis_engine_failed: %s", exc)
        return None


async def _run_decentralization(
    token_address: str,
    chain: str,
    deployer: str | None,
    wallet_map: dict[str, Any] | None = None,
    cluster_analysis: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """BubbleMaps-style decentralization score + cluster map (fail-open).

    Reuses the wallet-map and cluster-analysis results the pipeline already
    fetched (no duplicate ClickHouse queries); the engine falls back to
    fetching them itself for standalone use.
    """
    try:
        from app.domains.scanner.engines.own_decentralization import get_own_decentralization

        return await get_own_decentralization(
            token_address,
            chain,
            deployer=deployer,
            wallet_map=wallet_map,
            cluster_analysis=cluster_analysis,
        )
    except Exception as exc:
        logger.debug("decentralization_engine_failed: %s", exc)
        return None


async def run_sentinel_scan(
    token_address: str,
    chain: str = "solana",
    dev_address: str | None = None,
    config: SentinelConfig | None = None,
) -> SentinelReport:
    """Run all SENTINEL modules concurrently with graceful degradation."""
    _ensure_modules(config)
    _refresh_singletons()

    # Module singletons are guaranteed non-None after ``_refresh_singletons()``;
    # these asserts narrow ``Optional[X]`` for mypy (union-attr bucket) and are
    # no-ops at runtime in the initialized path.
    assert _address_labeler is not None
    assert _block_zero_sniper is not None
    assert _bundle_detector is not None
    assert _bytecode_similarity is not None
    assert _contract_authority is not None
    assert _contract_diff is not None
    assert _cross_chain_trace is not None
    assert _decompiler_analyzer is not None
    assert _dev_reputation is not None
    assert _exchange_funder is not None
    assert _flash_loan_detector is not None
    assert _fund_flow_visualizer is not None
    assert _gas_trace is not None
    assert _governance_attack is not None
    assert _guilt_association is not None
    assert _holder_analyzer is not None
    assert _honeypot_detector is not None
    assert _liquidity_verifier is not None
    assert _metadata_fingerprinter is not None
    assert _mev_detector is not None
    assert _oracle_manipulation is not None
    assert _proxy_detector is not None
    assert _pump_dump_detector is not None
    assert _pumpfun_analyzer is not None
    assert _sentiment_analyzer is not None
    assert _sleep_cycle is not None
    assert _social_velocity is not None
    assert _static_analyzer is not None
    assert _wash_trading is not None

    report = SentinelReport(token_address=token_address, chain=chain)
    coros: dict[str, Any] = {}

    core_modules: list[tuple[str, Callable[..., Awaitable[Any]]]] = [
        ("holder_analysis", _holder_analyzer.analyze),
        ("bundle_detection", _bundle_detector.detect_bundles),
        ("exchange_funding", _exchange_funder.analyze),
        ("liquidity_verification", _liquidity_verifier.verify_lock),
        ("wash_trading", _wash_trading.analyze),
        ("metadata_fingerprint", _metadata_fingerprinter.analyze_token_metadata),
        ("sentiment", _sentiment_analyzer.analyze),
        ("honeypot_detection", _honeypot_detector.analyze),
        ("contract_authority", _contract_authority.analyze),
        ("mev_detection", _mev_detector.analyze),
        ("flash_loan_detection", _flash_loan_detector.analyze),
        ("pump_dump_detection", _pump_dump_detector.analyze),
        ("oracle_manipulation", _oracle_manipulation.analyze),
        ("governance_attack", _governance_attack.analyze),
        ("proxy_detection", _proxy_detector.analyze),
        ("static_analysis", _static_analyzer.analyze),
        ("decompiler_analysis", _decompiler_analyzer.analyze),
        ("address_labels", _address_labeler.analyze),
        ("fund_flow", _fund_flow_visualizer.analyze),
        ("contract_diff", _contract_diff.analyze),
    ]
    for name, fn in core_modules:
        coros[name] = fn(token_address, chain)

    if dev_address:
        coros["dev_reputation"] = _dev_reputation.analyze(dev_address, chains=[chain])
        coros["guilt_association"] = _guilt_association.analyze(dev_address, chain)
        coros["sleep_cycle"] = _sleep_cycle.analyze([])
        coros["gas_trace"] = _gas_trace.analyze(dev_address, chain)
    else:
        for k in ("dev_reputation", "guilt_association", "sleep_cycle", "gas_trace"):
            report.errors[k] = "skipped: no dev_address provided"
            coros[k] = None

    # ── Competitor deep-scan extras (fail-open engines) ──
    # First-20 wallet map: emoji role classification of early buyers/holders.
    coros["wallet_map"] = _run_wallet_map(token_address, chain, dev_address)
    # Funding-source clusters + coordinated-buy detection (own ClickHouse).
    coros["cluster_analysis"] = _run_cluster_analysis(token_address, chain, dev_address)
    # Deployer prior-token history timeline (needs the deployer address).
    if dev_address:
        coros["deployer_timeline"] = _run_deployer_timeline(dev_address, chain)
    else:
        report.errors["deployer_timeline"] = "skipped: no dev_address provided"
        coros["deployer_timeline"] = None

    if _WALLET_MEMORY_AVAILABLE_LOCAL and dev_address:
        coros["wallet_intel"] = get_wallet_engine().get_deployer_intelligence(dev_address, chain)
    else:
        coros["wallet_intel"] = None

    coros["social_velocity"] = _social_velocity.analyze([])
    coros["bytecode_similarity"] = _bytecode_similarity.analyze(token_address, chain)
    coros["block_zero_sniper"] = _block_zero_sniper.analyze(token_address, chain)

    # Cross-Chain Trace (W4-V2): trace the dev wallet across chains
    if dev_address:
        coros["cross_chain_trace"] = _cross_chain_trace.analyze(dev_address, chain)
    else:
        coros["cross_chain_trace"] = _cross_chain_trace.analyze(token_address, chain)

    coros["pumpfun_analysis"] = _pumpfun_analyzer.analyze(token_address) if chain.lower() == "solana" else None

    # ── Execute all concurrently ──────────────────────────────
    keys = [k for k, v in coros.items() if v is not None]
    tasks = {k: asyncio.create_task(coros[k]) for k in keys}

    results: dict[str, Any] = {}
    for k, task in tasks.items():
        try:
            results[k] = await task
        except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError) as exc:
            logger.debug("module_error", exc_info=True)
            logger.warning(f"SENTINEL module {k} failed for {token_address}: {exc}")
            results[k] = None
            report.errors[k] = str(exc)[:200]
        except Exception as exc:
            logger.debug("module_error", exc_info=True)
            logger.warning(f"SENTINEL module {k} failed for {token_address}: {exc}")
            results[k] = None
            report.errors[k] = str(exc)[:200]

    # ── BubbleMaps-style decentralization (reuses wallet_map + cluster results) ──
    # Computed post-gather from the wallet-map / cluster-analysis payloads the
    # pipeline already fetched — no duplicate ClickHouse queries. Fail-open:
    # skipped when the wallet map itself was unavailable (no holder data).
    wm_result = results.get("wallet_map")
    if isinstance(wm_result, dict) and str(wm_result.get("result")) == "scanned":
        results["decentralization"] = await _run_decentralization(
            token_address,
            chain,
            dev_address,
            wallet_map=wm_result,
            cluster_analysis=results.get("cluster_analysis"),
        )

    # ── Convert to dicts + extract scores ─────────────────────
    risk_scores: dict[str, float] = {}
    extractors: dict[str, Callable[..., float]] = {
        "holder_analysis": _extract_risk_hhi,
        "bundle_detection": _extract_risk_bundle,
        "exchange_funding": _extract_risk_exchange_fund,
        "liquidity_verification": _extract_risk_lock,
        "dev_reputation": _extract_risk_dev,
        "wash_trading": _extract_risk_wash_trading,
        "metadata_fingerprint": _extract_risk_fingerprint,
        "sentiment": _extract_risk_sentiment,
        "pumpfun_analysis": _extract_risk_pumpfun,
        "honeypot_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "contract_authority": lambda r: float(getattr(r, "risk_score", 0)),
        "mev_detection": lambda r: float(getattr(r, "mev_risk_score", 0)),
        "flash_loan_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "pump_dump_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "oracle_manipulation": lambda r: float(getattr(r, "risk_score", 0)),
        "governance_attack": lambda r: float(getattr(r, "risk_score", 0)),
        "proxy_detection": lambda r: float(getattr(r, "risk_score", 0)),
        "static_analysis": lambda r: float(getattr(r, "risk_score", 0)),
        "decompiler_analysis": lambda r: float(getattr(r, "risk_score", 0)),
        "address_labels": lambda r: float(getattr(r, "risk_score", 0)),
        "wallet_intel": lambda r: float(r.get("risk_score", 0)) if isinstance(r, dict) else 0.0,
        "fund_flow": lambda r: float(getattr(r, "risk_score", 0)),
        "contract_diff": lambda r: float(getattr(r, "risk_score", 0)),
        "guilt_association": _extract_risk_guilt_association,
        "sleep_cycle": _extract_risk_sleep_cycle,
        "social_velocity": _extract_risk_social_velocity,
        "bytecode_similarity": _extract_risk_bytecode_similarity,
        "block_zero_sniper": _extract_risk_block_zero,
        "gas_trace": _extract_risk_gas_trace,
        "cross_chain_trace": lambda r: float(r.get("risk_score", 0)) if isinstance(r, dict) else 0.0,
    }
    field_map = {
        "holder_analysis": "holder_analysis",
        "bundle_detection": "bundle_detection",
        "exchange_funding": "exchange_funding",
        "liquidity_verification": "liquidity_verification",
        "dev_reputation": "dev_reputation",
        "wash_trading": "wash_trading",
        "metadata_fingerprint": "metadata_fingerprint",
        "sentiment": "sentiment",
        "pumpfun_analysis": "pumpfun_analysis",
        "honeypot_detection": "honeypot_detection",
        "contract_authority": "contract_authority",
        "mev_detection": "mev_detection",
        "flash_loan_detection": "flash_loan_detection",
        "pump_dump_detection": "pump_dump_detection",
        "oracle_manipulation": "oracle_manipulation",
        "governance_attack": "governance_attack",
        "proxy_detection": "proxy_detection",
        "static_analysis": "static_analysis",
        "decompiler_analysis": "decompiler_analysis",
        "address_labels": "address_labels",
        "wallet_intel": "wallet_intel",
        "fund_flow": "fund_flow",
        "contract_diff": "contract_diff",
        "guilt_association": "guilt_association",
        "sleep_cycle": "sleep_cycle",
        "social_velocity": "social_velocity",
        "bytecode_similarity": "bytecode_similarity",
        "block_zero_sniper": "block_zero_sniper",
        "gas_trace": "gas_trace",
        "cross_chain_trace": "cross_chain_trace",
        "wallet_map": "wallet_map",
        "deployer_timeline": "deployer_timeline",
        "cluster_analysis": "cluster_analysis",
        "decentralization": "decentralization",
    }

    for module_name, raw_result in results.items():
        dict_result = dataclass_to_dict(raw_result) if raw_result is not None else None
        setattr(report, field_map[module_name], dict_result)
        report.modules_run.append(module_name)
        if raw_result is not None and module_name in extractors:
            try:
                risk_scores[module_name] = extractors[module_name](raw_result)
            except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError):
                logger.debug("risk_extract_error", exc_info=True)
        report.red_flags.extend(_collect_red_flags(dict_result, module_name))

    report.module_risk_scores = risk_scores
    composite, level = _compute_composite(risk_scores)
    report.composite_risk_score = composite
    report.risk_level = level

    if _WALLET_MEMORY_AVAILABLE_LOCAL and dev_address and composite > 0:
        try:
            await get_wallet_engine().flag_deployer(
                address=dev_address,
                chain=chain,
                reason="sentinel_scan",
                token=token_address,
                score=composite,
            )
        except httpx.HTTPError:
            logger.debug("wallet_memory_feedback_error", exc_info=True)

    # ── Push Telegram alert for high/critical risk (or severe red flags) ──
    try:
        from app.domains.telegram.alert_hooks import send_scanner_alert, should_send_scanner_alert

        if should_send_scanner_alert(report.risk_level, list(report.red_flags)):
            await send_scanner_alert(
                token_address=token_address,
                chain=chain,
                risk_score=float(report.composite_risk_score),
                threats=list(report.red_flags),
            )
    except Exception as exc:
        logger.debug("sentinel_alert_failed", error=str(exc)[:100])

    # ── Publish high/critical findings to RAG threat-intel pipeline ──
    # Same payload format as the unified scanner (publish_rag_finding →
    # Redis channel ``rmi:rag:ingest`` + ``rmi:rag:findings_queue``).
    # Gated by SCANNER_ALERTS_ENABLED exactly like the Telegram alert above.
    if report.risk_level in ("high", "critical"):
        try:
            from app.domains.telegram.alert_hooks import scanner_alerts_enabled

            if not scanner_alerts_enabled():
                logger.debug("sentinel_rag_publish_skipped (SCANNER_ALERTS_ENABLED=false)")
            else:
                from app.domains.scanner.unified.model import ScanResult
                from app.domains.scanner.unified.persistence import publish_rag_finding

                meta = report.metadata_fingerprint or {}
                await publish_rag_finding(
                    ScanResult(
                        token_address=token_address,
                        chain=chain,
                        scanned_at=report.scanned_at,
                        name=str(meta.get("name") or "") if isinstance(meta, dict) else "",
                        symbol=str(meta.get("symbol") or "") if isinstance(meta, dict) else "",
                        safety_score=max(0.0, 100.0 - float(report.composite_risk_score or 0)),
                        risk_level=report.risk_level,
                        risk_flags=list(report.red_flags),
                        modules_run=list(report.modules_run),
                    )
                )
        except Exception as exc:
            logger.debug("sentinel_rag_publish_failed", error=str(exc)[:100])

    return report


# ═══════════════════════════════════════════════════════════════════
# DYNAMIC STANDALONE RUNNERS
# ═══════════════════════════════════════════════════════════════════
# Each public function calls _run_standalone with the right module key.
# The `_RUNNER_SPEC` dict drives generation.

_RUNNER_SPEC = [
    ("run_holder_analysis", "holder_analysis", None, None),
    ("run_bundle_detection", "bundle_detection", None, None),
    ("run_exchange_funding", "exchange_funding", None, None),
    ("run_liquidity_verification", "liquidity_verification", None, None),
    ("run_wash_trading", "wash_trading", None, None),
    ("run_metadata_fingerprint", "metadata_fingerprint", None, None),
    ("run_sentiment", "sentiment", None, None),
    (
        "run_pumpfun_analysis",
        "pumpfun_analysis",
        lambda: _pumpfun_analyzer.analyze if _pumpfun_analyzer else None,
        None,
    ),
    ("run_honeypot_detection", "honeypot_detection", None, None),
    ("run_contract_authority", "contract_authority", None, None),
    ("run_mev_detection", "mev_detection", None, None),
    ("run_flash_loan_detection", "flash_loan_detection", None, None),
    ("run_pump_dump_detection", "pump_dump_detection", None, None),
    ("run_oracle_manipulation", "oracle_manipulation", None, None),
    ("run_governance_attack", "governance_attack", None, None),
    ("run_proxy_detection", "proxy_detection", None, None),
    ("run_static_analysis", "static_analysis", None, None),
    ("run_decompiler_analysis", "decompiler_analysis", None, None),
    ("run_address_labels", "address_labels", None, None),
    ("run_fund_flow", "fund_flow", None, None),
    ("run_contract_diff", "contract_diff", None, None),
]


def _get_analyzer_map() -> dict[str, Any]:
    """Build analyzer map lazily, ensuring modules are initialized first."""
    _ensure_modules()
    _refresh_singletons()
    assert _address_labeler is not None
    assert _bundle_detector is not None
    assert _contract_authority is not None
    assert _contract_diff is not None
    assert _cross_chain_trace is not None
    assert _decompiler_analyzer is not None
    assert _exchange_funder is not None
    assert _flash_loan_detector is not None
    assert _fund_flow_visualizer is not None
    assert _governance_attack is not None
    assert _holder_analyzer is not None
    assert _honeypot_detector is not None
    assert _liquidity_verifier is not None
    assert _metadata_fingerprinter is not None
    assert _mev_detector is not None
    assert _oracle_manipulation is not None
    assert _proxy_detector is not None
    assert _pump_dump_detector is not None
    assert _pumpfun_analyzer is not None
    assert _sentiment_analyzer is not None
    assert _static_analyzer is not None
    assert _wash_trading is not None
    return {
        "holder_analysis": _holder_analyzer.analyze,
        "bundle_detection": _bundle_detector.detect_bundles,
        "exchange_funding": _exchange_funder.analyze,
        "liquidity_verification": _liquidity_verifier.verify_lock,
        "wash_trading": _wash_trading.analyze,
        "metadata_fingerprint": _metadata_fingerprinter.analyze_token_metadata,
        "sentiment": _sentiment_analyzer.analyze,
        "pumpfun_analysis": _pumpfun_analyzer.analyze,
        "honeypot_detection": _honeypot_detector.analyze,
        "contract_authority": _contract_authority.analyze,
        "mev_detection": _mev_detector.analyze,
        "flash_loan_detection": _flash_loan_detector.analyze,
        "pump_dump_detection": _pump_dump_detector.analyze,
        "oracle_manipulation": _oracle_manipulation.analyze,
        "governance_attack": _governance_attack.analyze,
        "proxy_detection": _proxy_detector.analyze,
        "static_analysis": _static_analyzer.analyze,
        "decompiler_analysis": _decompiler_analyzer.analyze,
        "address_labels": _address_labeler.analyze,
        "fund_flow": _fund_flow_visualizer.analyze,
        "contract_diff": _contract_diff.analyze,
        "cross_chain_trace": _cross_chain_trace.analyze,
    }


# Special runners that don't fit the standard 2-arg pattern
async def run_dev_reputation(
    dev_wallet: str, chains: list[str] | None = None, config: SentinelConfig | None = None
) -> dict[str, Any]:
    _ensure_modules(config)
    _refresh_singletons()
    assert _dev_reputation is not None
    return dataclass_to_dict(await _dev_reputation.analyze(dev_wallet, chains))


async def run_pumpfun_analysis(token_address: str, config: SentinelConfig | None = None) -> dict[str, Any]:
    _ensure_modules(config)
    _refresh_singletons()
    assert _pumpfun_analyzer is not None
    return dataclass_to_dict(await _pumpfun_analyzer.analyze(token_address))


async def _run_standalone(module_key: str, token_address: str, chain: str, config=None) -> dict[str, Any]:
    """Execute a single module and return dict result."""
    _ensure_modules(config)
    _refresh_singletons()
    fn = _get_analyzer_map()[module_key]
    result = await fn(token_address, chain)
    return dataclass_to_dict(result)


# Dynamically generate all standard 2-arg runner functions
def _make_runner(module_key: str, doc: str = ""):
    if module_key == "pumpfun_analysis":
        return run_pumpfun_analysis

    async def runner(token_address: str, chain: str = "solana", config: SentinelConfig | None = None) -> dict[str, Any]:
        return await _run_standalone(module_key, token_address, chain, config)

    runner.__name__ = f"run_{module_key}"
    runner.__qualname__ = runner.__name__
    runner.__doc__ = doc or f"Run {module_key} only, return dict."
    return runner


# Create all standard runners
run_holder_analysis = _make_runner("holder_analysis")
run_bundle_detection = _make_runner("bundle_detection")
run_exchange_funding = _make_runner("exchange_funding")
run_liquidity_verification = _make_runner("liquidity_verification")
run_wash_trading = _make_runner("wash_trading")
run_metadata_fingerprint = _make_runner("metadata_fingerprint")
run_sentiment = _make_runner("sentiment")
run_honeypot_detection = _make_runner("honeypot_detection")
run_contract_authority = _make_runner("contract_authority")
run_mev_detection = _make_runner("mev_detection")
run_flash_loan_detection = _make_runner("flash_loan_detection")
run_pump_dump_detection = _make_runner("pump_dump_detection")
run_oracle_manipulation = _make_runner("oracle_manipulation")
run_governance_attack = _make_runner("governance_attack")
run_proxy_detection = _make_runner("proxy_detection")
run_static_analysis = _make_runner("static_analysis")
run_decompiler_analysis = _make_runner("decompiler_analysis")
run_address_labels = _make_runner("address_labels")
run_fund_flow = _make_runner("fund_flow")
run_contract_diff = _make_runner("contract_diff")
