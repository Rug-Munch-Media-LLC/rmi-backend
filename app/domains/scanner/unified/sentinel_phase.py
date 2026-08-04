"""SENTINEL deep-forensic phase for the unified token scanner.

Phase 5 of :class:`UnifiedTokenScanner` — runs the SENTINEL module fleet
directly (no DataBus hop) with a free/premium split:

  * free    — core rug-detection signals: holder HHI, LP lock verification,
              dev reputation, metadata fingerprint, contract authority
              (mint/freeze/renounce), and proxy detection. Always populated.
  * premium — every remaining SENTINEL module (bundle/sniper, wash trading,
              MEV, flash loans, pump-and-dump, oracle manipulation, governance
              attack, sentiment, pumpfun, honeypot, static analysis, decompiler,
              address labels, fund flow, contract diff, guilt association,
              bytecode similarity, block-zero sniper, gas trace, cross-chain
              trace, sleep cycle, social velocity). Gated behind the elite/admin
              API-key tier — or unlocked through the standalone x402 SENTINEL
              deep-scan endpoint (``POST /api/v1/x402-tools/sentinel_scan``).

Best-effort by design: a failing module is recorded in ``errors`` and never
crashes the scan. External calls are time-boxed so SENTINEL cannot stall the
rest of the scan.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("sentinel_phase")

# Per-module timeout — protects scan latency when an external API hangs.
MODULE_TIMEOUT_S = 12.0
# Timeout for best-effort deployer resolution (DexScreener lookup).
DEPLOYER_TIMEOUT_S = 4.0

# Tier values that unlock the premium SENTINEL module set. Mirrors the
# unified scanner's PREMIUM phase gate (elite/admin).
PREMIUM_UNLOCK_TIERS: tuple[str, ...] = ("elite", "admin")

# ── Free-tier modules: core rug-detection signals, always run ──────────
SENTINEL_FREE_MODULES: tuple[str, ...] = (
    "holder_analysis",  # holder HHI concentration
    "liquidity_verification",  # LP lock verification
    "dev_reputation",  # serial-rug dev history
    "metadata_fingerprint",  # metadata cloning / fingerprint
    "contract_authority",  # mint / freeze / renounce authority
    "proxy_detection",  # upgradeable proxy risk
)

# ── Premium-tier modules: everything else, tier-gated ─────────────────
SENTINEL_PREMIUM_MODULES: tuple[str, ...] = (
    "bundle_detection",
    "exchange_funding",
    "wash_trading",
    "sentiment",
    "pumpfun_analysis",
    "honeypot_detection",
    "mev_detection",
    "flash_loan_detection",
    "pump_dump_detection",
    "oracle_manipulation",
    "governance_attack",
    "static_analysis",
    "decompiler_analysis",
    "address_labels",
    "fund_flow",
    "contract_diff",
    "guilt_association",
    "sleep_cycle",
    "social_velocity",
    "bytecode_similarity",
    "block_zero_sniper",
    "gas_trace",
    "cross_chain_trace",
)

SENTINEL_ALL_MODULES: tuple[str, ...] = SENTINEL_FREE_MODULES + SENTINEL_PREMIUM_MODULES

# Human-readable preview copy for locked premium modules (shown to free/pro
# callers with an "unlock" prompt instead of the full report).
SENTINEL_PREMIUM_DESCRIPTIONS: dict[str, str] = {
    "bundle_detection": "Same-block bundle/sniper detection with funding-chain analysis",
    "exchange_funding": "CEX-funded wallet detection, withdrawal clustering, deposit-to-dump patterns",
    "wash_trading": "Wash-trading probability scoring on volume authenticity",
    "sentiment": "Social sentiment scoring and artificial-hype detection",
    "pumpfun_analysis": "PumpFun bonding-curve progress, bot ratio, dev concentration",
    "honeypot_detection": "Sell-side honeypot detection (can buy but not sell)",
    "mev_detection": "MEV exposure and sandwich-attack susceptibility",
    "flash_loan_detection": "Flash-loan funded manipulation patterns",
    "pump_dump_detection": "Pump-and-dump cycle detection from on-chain volume",
    "oracle_manipulation": "Price-oracle manipulation susceptibility",
    "governance_attack": "Governance attack surface and vote manipulation",
    "static_analysis": "Static bytecode analysis for malicious patterns",
    "decompiler_analysis": "Decompiled contract review for hidden backdoors",
    "address_labels": "Known-good / known-bad address labeling of holders",
    "fund_flow": "Fund-flow visualization from deployer wallet",
    "contract_diff": "Contract diff vs known scam templates",
    "guilt_association": "Guilt-by-association for the deployer wallet",
    "sleep_cycle": "Deployer sleep-cycle / activity-pattern analysis",
    "social_velocity": "Social velocity and artificial-growth detection",
    "bytecode_similarity": "Bytecode similarity vs known scam contracts",
    "block_zero_sniper": "Block-zero sniper detection (dev/bot buys first block)",
    "gas_trace": "Three-hop gas trace of deployer funding",
    "cross_chain_trace": "Cross-chain trace of the deployer wallet",
}

UNLOCK_PROMPT = (
    "Upgrade to elite tier or run the paid x402 SENTINEL deep scan "
    "(POST /api/v1/x402-tools/sentinel_scan, $0.15) to unlock the full report."
)


def _no_deployer(module_key: str) -> None:
    """Raise for deployer-dependent modules when no deployer could be resolved."""
    raise ValueError(f"skipped: no deployer address resolved for {module_key}")


def _get_sentinel_modules():
    """Import the SENTINEL pipeline modules lazily (keeps import graph light)."""
    from app.domains.scanners.sentinel_pipeline import modules as sentinel_modules

    sentinel_modules._ensure_modules()
    return sentinel_modules


def _get_analyzers(address: str, chain: str, deployer: str | None = None) -> dict[str, Callable[[], Any]]:
    """Build a module-key → async-callable map from the SENTINEL singletons.

    Freshly built on every call (never cached) so tests can patch this
    function wholesale and the module singletons stay current.
    """
    mod = _get_sentinel_modules()
    chain_lower = (chain or "").lower()

    def _guard_attr(name: str) -> Any | None:
        return getattr(mod, name, None)

    analyzers: dict[str, Callable[[], Any]] = {}

    # ── Free tier ──────────────────────────────────────────────────────
    if _guard_attr("_holder_analyzer"):
        analyzers["holder_analysis"] = lambda: mod._holder_analyzer.analyze(address, chain)
    if _guard_attr("_liquidity_verifier"):
        analyzers["liquidity_verification"] = lambda: mod._liquidity_verifier.verify_lock(address, chain)
    if _guard_attr("_dev_reputation"):
        if deployer:
            analyzers["dev_reputation"] = lambda: mod._dev_reputation.analyze(deployer, [chain])
        else:
            analyzers["dev_reputation"] = lambda: _no_deployer("dev_reputation")
    if _guard_attr("_metadata_fingerprinter"):
        analyzers["metadata_fingerprint"] = lambda: mod._metadata_fingerprinter.analyze_token_metadata(address, chain)
    if _guard_attr("_contract_authority"):
        analyzers["contract_authority"] = lambda: mod._contract_authority.analyze(address, chain)
    if _guard_attr("_proxy_detector"):
        analyzers["proxy_detection"] = lambda: mod._proxy_detector.analyze(address, chain)

    # ── Premium tier ───────────────────────────────────────────────────
    if _guard_attr("_bundle_detector"):
        analyzers["bundle_detection"] = lambda: mod._bundle_detector.detect_bundles(address, chain)
    if _guard_attr("_exchange_funder"):
        analyzers["exchange_funding"] = lambda: mod._exchange_funder.analyze(address, chain)
    if _guard_attr("_wash_trading"):
        analyzers["wash_trading"] = lambda: mod._wash_trading.analyze(address, chain)
    if _guard_attr("_sentiment_analyzer"):
        analyzers["sentiment"] = lambda: mod._sentiment_analyzer.analyze(address, chain)
    if _guard_attr("_pumpfun_analyzer"):
        if chain_lower == "solana":
            analyzers["pumpfun_analysis"] = lambda: mod._pumpfun_analyzer.analyze(address)
        else:
            analyzers["pumpfun_analysis"] = lambda: None
    if _guard_attr("_honeypot_detector"):
        analyzers["honeypot_detection"] = lambda: mod._honeypot_detector.analyze(address, chain)
    if _guard_attr("_mev_detector"):
        analyzers["mev_detection"] = lambda: mod._mev_detector.analyze(address, chain)
    if _guard_attr("_flash_loan_detector"):
        analyzers["flash_loan_detection"] = lambda: mod._flash_loan_detector.analyze(address, chain)
    if _guard_attr("_pump_dump_detector"):
        analyzers["pump_dump_detection"] = lambda: mod._pump_dump_detector.analyze(address, chain)
    if _guard_attr("_oracle_manipulation"):
        analyzers["oracle_manipulation"] = lambda: mod._oracle_manipulation.analyze(address, chain)
    if _guard_attr("_governance_attack"):
        analyzers["governance_attack"] = lambda: mod._governance_attack.analyze(address, chain)
    if _guard_attr("_static_analyzer"):
        analyzers["static_analysis"] = lambda: mod._static_analyzer.analyze(address, chain)
    if _guard_attr("_decompiler_analyzer"):
        analyzers["decompiler_analysis"] = lambda: mod._decompiler_analyzer.analyze(address, chain)
    if _guard_attr("_address_labeler"):
        analyzers["address_labels"] = lambda: mod._address_labeler.analyze(address, chain)
    if _guard_attr("_fund_flow_visualizer"):
        analyzers["fund_flow"] = lambda: mod._fund_flow_visualizer.analyze(address, chain)
    if _guard_attr("_contract_diff"):
        analyzers["contract_diff"] = lambda: mod._contract_diff.analyze(address, chain)
    if _guard_attr("_guilt_association"):
        if deployer:
            analyzers["guilt_association"] = lambda: mod._guilt_association.analyze(deployer, chain)
        else:
            analyzers["guilt_association"] = lambda: _no_deployer("guilt_association")
    if _guard_attr("_sleep_cycle"):
        analyzers["sleep_cycle"] = lambda: mod._sleep_cycle.analyze([])
    if _guard_attr("_social_velocity"):
        analyzers["social_velocity"] = lambda: mod._social_velocity.analyze([])
    if _guard_attr("_bytecode_similarity"):
        analyzers["bytecode_similarity"] = lambda: mod._bytecode_similarity.analyze(address, chain)
    if _guard_attr("_block_zero_sniper"):
        analyzers["block_zero_sniper"] = lambda: mod._block_zero_sniper.analyze(address, chain)
    if _guard_attr("_gas_trace"):
        if deployer:
            analyzers["gas_trace"] = lambda: mod._gas_trace.analyze(deployer, chain)
        else:
            analyzers["gas_trace"] = lambda: _no_deployer("gas_trace")
    if _guard_attr("_cross_chain_trace"):
        if deployer:
            analyzers["cross_chain_trace"] = lambda: mod._cross_chain_trace.analyze(deployer, chain)
        else:
            analyzers["cross_chain_trace"] = lambda: mod._cross_chain_trace.analyze(address, chain)

    return analyzers


async def _resolve_deployer(address: str, chain: str) -> str | None:
    """Best-effort deployer resolution via DexScreener (free, no API key).

    Returns the first ``creatorAddress`` found for the token's pairs, or
    ``None`` when the lookup fails/times out. Never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=DEPLOYER_TIMEOUT_S) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
            if resp.status_code != 200:
                return None
            pairs = (resp.json() or {}).get("pairs") or []
            for pair in pairs:
                creator = pair.get("creatorAddress") or ""
                if creator:
                    return creator
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        logger.debug("deployer_resolve_failed", address=address[:16], chain=chain)
    return None


def extract_deployer_from_security(security: Any) -> str | None:
    """Pull a deployer/creator address from core security data (best-effort).

    GoPlus-style security payloads expose the creator in a few shapes;
    we check the common key names defensively and return ``None`` when the
    field is absent or not a valid-looking address.
    """
    if not isinstance(security, dict):
        return None
    for key in ("creator_address", "creator", "deployer", "deployer_address", "owner_address"):
        value = security.get(key)
        if isinstance(value, str) and len(value) >= 32 and value not in ("", "unknown", "null"):
            return value
    # Nested "checks" / "extra" buckets sometimes carry the creator.
    for bucket in ("checks", "extra", "info"):
        value = security.get(bucket)
        if isinstance(value, dict):
            found = extract_deployer_from_security(value)
            if found:
                return found
    return None


async def run_sentinel_phase(
    address: str,
    chain: str,
    tier: str = "free",
    deployer: str | None = None,
) -> dict[str, Any]:
    """Run the SENTINEL phase: free modules always, premium when unlocked.

    Returns a plain dict (safe for JSON serialization):

    .. code-block:: python

        {
            "free": {module: dict_result | None, ...},
            "premium": {module: dict_result | None, ...} | None,
            "premium_unlocked": bool,
            "premium_modules": [module keys available at premium tier],
            "premium_preview": [{"module", "description", "unlock"}, ...],
            "errors": {module: error_string},
            "modules_run": [module keys attempted],
            "deployer_resolved": bool,
        }

    The phase never raises: module failures are captured in ``errors`` and
    the remaining modules still run.
    """
    from app.domains.scanners.sentinel_pipeline.config import dataclass_to_dict

    unlocked = (tier or "").lower() in PREMIUM_UNLOCK_TIERS
    if deployer is None:
        deployer = await _resolve_deployer(address, chain)

    analyzers = _get_analyzers(address, chain, deployer=deployer)
    free_keys = [k for k in SENTINEL_FREE_MODULES if k in analyzers]
    premium_keys = [k for k in SENTINEL_PREMIUM_MODULES if k in analyzers] if unlocked else []
    all_keys = free_keys + premium_keys

    async def _run_one(key: str, fn: Callable[[], Any]) -> tuple[str, Any, str | None]:
        try:
            raw = await asyncio.wait_for(fn(), timeout=MODULE_TIMEOUT_S)
            return key, dataclass_to_dict(raw) if raw is not None else None, None
        except TimeoutError:
            return key, None, f"timeout after {MODULE_TIMEOUT_S}s"
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
            logger.debug("sentinel_module_error", module=key, error=str(exc)[:160])
            return key, None, str(exc)[:200]
        except Exception as exc:
            logger.debug("sentinel_module_error", module=key, error=str(exc)[:160])
            return key, None, str(exc)[:200]

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    modules_run: list[str] = []

    if all_keys:
        gathered = await asyncio.gather(*(_run_one(k, analyzers[k]) for k in all_keys))
        for key, data, err in gathered:
            results[key] = data
            modules_run.append(key)
            if err:
                errors[key] = err

    return {
        "free": {k: results.get(k) for k in free_keys},
        "premium": {k: results.get(k) for k in premium_keys} if unlocked else None,
        "premium_unlocked": unlocked,
        "premium_modules": list(SENTINEL_PREMIUM_MODULES),
        "premium_preview": _build_premium_preview() if not unlocked else [],
        "errors": errors,
        "modules_run": modules_run,
        "deployer_resolved": bool(deployer),
    }


def _build_premium_preview() -> list[dict[str, str]]:
    """Locked-tier preview entries: what premium modules exist + unlock prompt."""
    return [
        {
            "module": key,
            "description": SENTINEL_PREMIUM_DESCRIPTIONS.get(key, "Deep-forensic security module"),
            "unlock": UNLOCK_PROMPT,
        }
        for key in SENTINEL_PREMIUM_MODULES
    ]
