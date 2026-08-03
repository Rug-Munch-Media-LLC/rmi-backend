"""
Deployer Behavioral Fingerprint (C7 v1) — ClickHouse-Powered Deployer Scoring
=============================================================================
Scores every token deployer from our own ClickHouse data (chain-indexer
wallet_memory tables). Goes beyond the numeric reputation in
``own_deployer_reputation`` by assigning a *behavioral bucket*:

  - ``NEW``          — first deployment happened < 7 days ago
  - ``REPEAT_CLEAN`` — >1 deployments, every deployment survived 7+ days
  - ``REPEAT_RUG``   — ANY deployment had zero DEX survival past 7 days
  - ``FARM``         — >3 deployments funded by the same non-CEX wallet
  - ``PROLIFIC``     — >50 deployments with a low rug rate

Data sources (all LOCAL, zero cost):
  - ``wallet_memory.deployer_events``  — deployment stats + per-token history
  - ``wallet_memory.dex_events``       — DEX swap activity → survival/lifespan
  - ``wallet_memory.evm_transfers``    — funding analysis (first funder)
  - ``wallet_memory.wallet_labels``    — funder CEX / label classification

Caching:
  - Redis key ``fingerprint:{chain}:{deployer}`` with 1h TTL (async, graceful
    fallback when Redis is unavailable).
  - In-process dict cache (10 min) mirrors the other own-data engines.

Usage:
    from app.domains.scanner.engines.deployer_fingerprint import fingerprint_deployer

    result = await fingerprint_deployer("0x...", "ethereum")

Notes:
  - ``pair_created_ts`` is proxied by the earliest DEX swap timestamp for the
    token because chain-indexers persist only ``type = 'swap'`` rows in
    dex_events (pair-created events are not stored as rows).
  - ``funder_address`` is the first wallet that sent the deployer an incoming
    transfer on record. ``evm_transfers`` holds ERC-20 transfers; native ETH
    is indexed only when it carries the native token marker (zero address /
    0xeeee…), otherwise the first ERC-20 funder is used as the proxy.

  [REVIEW] Spec interpretation notes:
  - Risk-bucket precedence is operational, not the display order in the spec:
    PROLIFIC (>50 deployments, success >= 0.8) wins over FARM/REPEAT_RUG so a
    high-volume low-rug-rate deployer is not mislabeled as a rugger; FARM wins
    over REPEAT_RUG because the farm pattern is the more specific signal.
  - A single old (>= 7 days) clean deployment falls into REPEAT_CLEAN; the spec
    buckets only define NEW for young deployers and give no label for an old
    single clean deployment, so REPEAT_CLEAN is the least-bad fit (the count is
    visible in ``total_deployments`` so callers can distinguish).
  - success_rate uses *matured* tokens (deployed >= 7 days ago) as the
    denominator; tokens too young to judge never count against the deployer.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.core.logging import get_logger
from app.domains.databus.providers.chains.chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
    to_iso,
)

# Legacy module-local names kept so existing unit tests that patch
# ``deployer_fingerprint._redis_get`` / ``deployer_fingerprint._redis_set``
# keep intercepting the shared implementation.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_TTL_SECONDS = 3600  # 1 hour — per spec
REDIS_CACHE_PREFIX = "fingerprint"
_FINGERPRINT_CACHE: dict[str, dict[str, Any]] = {}
_FINGERPRINT_CACHE_TTL = 600  # 10 minutes in-process mirror
_FINGERPRINT_CACHE_MAX = 200

# ── Risk buckets ──────────────────────────────────────────────────────────
RISK_BUCKETS = ("NEW", "REPEAT_CLEAN", "REPEAT_RUG", "FARM", "PROLIFIC")

# Tokens used to represent native chain currency (ETH/MATIC) in evm_transfers
_NATIVE_TOKEN_MARKERS = frozenset(
    {
        "0x0000000000000000000000000000000000000000",
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "0x0000000000000000000000000000000000001010",  # Polygon native
        "eth",
        "native",
        "",
    }
)

# Known exchange keywords used to classify funder labels as CEX
_CEX_KEYWORDS = (
    "binance",
    "coinbase",
    "kraken",
    "okx",
    "okex",
    "bybit",
    "huobi",
    "gate.io",
    "gateio",
    "kucoin",
    "bitfinex",
    "mexc",
    "crypto.com",
    "bitget",
    "upbit",
    "bithumb",
    "gemini",
    "bitstamp",
    "coincheck",
    "bitpanda",
    "exchange",
    "cex",
)

# ClickHouse query execution guard — every query is bounded
_QUERY_TIMEOUT_SECONDS = 10


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def fingerprint_deployer(deployer_address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Compute the behavioral fingerprint for a token deployer from ClickHouse.

    Combines deployment stats, DEX survival rate, median token lifespan, and
    funding-source analysis into a risk bucket (NEW / REPEAT_CLEAN / REPEAT_RUG /
    FARM / PROLIFIC) plus a 0-100 risk score (higher = riskier).

    Launchpad awareness:
      If the given deployer is a known launchpad factory (e.g. pump.fun /
      bags.fm), the factory itself is NOT fingerprinted — that would score the
      factory's aggregate history (10M+ deployments) instead of the real
      creator. Instead we resolve the real deployer first (see
      ``launchpad_resolver``) and fingerprint the creator EOA. When resolution
      is unavailable, we return a degraded ``unavailable`` result rather than a
      misleading factory score.

    Args:
        deployer_address: Deployer wallet address (lowercased internally).
            May be a launchpad factory address — resolved to the real creator.
        chain: Chain to analyze (default ``ethereum``). Empty string or
               ``any`` means all chains.

    Returns:
        Dict with the fingerprint::

            {
                "deployer": "0x...",
                "risk_bucket": "NEW" | "REPEAT_CLEAN" | "REPEAT_RUG" | "FARM" | "PROLIFIC",
                "risk_score": 0-100,
                "total_deployments": int,
                "success_rate": float,
                "median_lifespan_hours": float,
                "funder_profile": {...},
                "evidence": [...],
                # launchpad context (only when the input was a factory):
                "launchpad": "pump.fun" | ...,
                "real_deployer": "0x...",       # resolved creator EOA
                "launchpad_resolved": bool,
            }

        Returns an ``unavailable`` result (graceful degradation) when
        ClickHouse is unreachable or the deployer has no own-data history.
    """
    deployer_address = deployer_address.lower().strip()
    chain_norm = _normalize_chain(chain) if chain else ""
    _start = time.monotonic()

    if not deployer_address:
        return _error_result("No deployer address")

    # ── 0. Launchpad factory → real deployer resolution ──
    # Only Solana base58 addresses are case-sensitive; EVM deployers are
    # lowercased above and compared against the (lowercased) registry keys.
    launchpad_ctx: dict[str, Any] | None = None
    if _is_known_launchpad_factory(deployer_address, chain_norm):
        launchpad_ctx = await _resolve_launchpad_context(deployer_address, chain_norm)
        if launchpad_ctx is None:
            # Factory is known but we could not trace the real creator —
            # do not fingerprint the factory itself.
            result = _unavailable_result(
                deployer_address,
                chain_norm,
                reason="launchpad factory deployer — real creator could not be resolved",
            )
            result["launchpad"] = _launchpad_name(deployer_address, chain_norm)
            result["launchpad_resolved"] = False
            return result
        deployer_address = launchpad_ctx["real_deployer"]

    # ── 1. Redis fast cache (1h TTL) ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm or 'any'}:{deployer_address}"
    cached = await _redis_get(cache_key)
    if cached is not None:
        logger.debug("deployer_fingerprint_redis_hit", key=cache_key)
        return cached

    # ── 2. In-process mirror cache ──
    if cache_key in _FINGERPRINT_CACHE:
        entry = _FINGERPRINT_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _FINGERPRINT_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_fingerprint(deployer_address, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("deployer_fingerprint_query_failed: %s", exc)
        result = _unavailable_result(deployer_address, chain_norm)

    # ── Stamp launchpad context (factory → real creator) ──
    if launchpad_ctx is not None:
        result["launchpad"] = launchpad_ctx.get("launchpad")
        result["real_deployer"] = launchpad_ctx.get("real_deployer")
        result["launchpad_resolved"] = True
        result["evidence_tx"] = launchpad_ctx.get("evidence_tx")
        result["launchpad_confidence"] = launchpad_ctx.get("confidence")
        result["deployer"] = deployer_address  # fingerprint is of the real creator
        result["note"] = "fingerprint computed for real deployer resolved from launchpad factory"

    # Only cache meaningful results (skip unavailable/error)
    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _FINGERPRINT_CACHE[cache_key] = result
        if len(_FINGERPRINT_CACHE) > _FINGERPRINT_CACHE_MAX:
            _FINGERPRINT_CACHE.pop(next(iter(_FINGERPRINT_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "deployer_fingerprint_latency",
        ms=round(_elapsed_ms, 1),
        deployer=deployer_address[:10],
        bucket=result.get("risk_bucket"),
        risk=result.get("risk_score"),
        deployments=result.get("total_deployments"),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_launchpad_context(deployer_address: str, chain_norm: str) -> dict[str, Any] | None:
    """Resolve a known launchpad factory to its real creator EOA.

    Delegates to ``launchpad_resolver.resolve_launchpad_deployer`` (lazy import
    to keep this engine's import graph light). When the caller has no token
    context, we anchor resolution on the factory's most recent recorded deploy
    transaction from ``deployer_events``.

    Returns ``None`` when the factory is not in the registry or the creator
    cannot be traced.
    """
    try:
        from app.domains.scanner.engines.launchpad_resolver import (
            _query_factory_latest_deploy_tx,
            resolve_launchpad_deployer,
        )

        deploy_tx = await run_sync(_query_factory_latest_deploy_tx, deployer_address, chain_norm)
        return await resolve_launchpad_deployer("", chain_norm, deployer_address, deploy_tx=deploy_tx)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("deployer_fingerprint_launchpad_resolve_failed: %s", exc)
        return None


def _is_known_launchpad_factory(deployer_address: str, chain_norm: str) -> bool:
    """True when the deployer address is a registered launchpad factory.

    Non-lazy import is avoided at module load; the lookup itself is cheap.
    """
    try:
        from app.domains.scanner.engines.launchpad_resolver import lookup_launchpad

        return lookup_launchpad(chain_norm or "ethereum", deployer_address) is not None
    except (ImportError, ValueError):
        return False


def _launchpad_name(deployer_address: str, chain_norm: str) -> str | None:
    """Return the launchpad label for a factory address (or None)."""
    try:
        from app.domains.scanner.engines.launchpad_resolver import lookup_launchpad

        return lookup_launchpad(chain_norm or "ethereum", deployer_address)
    except (ImportError, ValueError):
        return None


async def _compute_fingerprint(deployer_address: str, chain_norm: str) -> dict[str, Any]:
    """Run all ClickHouse queries and assemble the fingerprint result."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)

    # ── Round 1 (parallel, independent) ──
    stats_task = asyncio.create_task(run_sync(_query_deployer_stats, deployer_address, chain_norm))
    tokens_task = asyncio.create_task(run_sync(_query_per_token_deployments, deployer_address, chain_norm))
    funder_task = asyncio.create_task(run_sync(_query_funder, deployer_address, chain_norm))
    stats, tokens, funder = await asyncio.gather(stats_task, tokens_task, funder_task)

    if not stats or stats.get("tokens_deployed", 0) == 0:
        return _unavailable_result(deployer_address, chain_norm, reason="no deployer history")

    total_deployments = int(stats.get("tokens_deployed", 0) or 0)
    first_seen = stats.get("first_ts")
    last_seen = stats.get("last_ts")
    chains_used = stats.get("chains_used", [])

    # ── Round 2: per-token DEX survival (needs token list) ──
    dex_stats = await run_sync(_query_token_dex_stats, tokens, chain_norm)

    # ── Round 3: funder profile (needs funder address) ──
    funder_profile = await _build_funder_profile(funder, chain_norm, deployer_address)

    # ── Assemble token outcomes ──
    token_outcomes: list[dict[str, Any]] = []
    survived_count = 0
    matured_count = 0
    lifespans: list[float] = []
    evidence: list[dict[str, Any]] = []

    for tok in tokens:
        dex = dex_stats.get(tok["token_address"].lower()) or {}
        deployed_ts = tok.get("deployed_ts")
        first_dex_ts = dex.get("first_dex_ts")
        last_swap_ts = dex.get("last_swap_ts")
        swap_count = int(dex.get("swap_count", 0) or 0)

        # Lifespan: last swap minus the pair-created proxy (first DEX activity),
        # falling back to the deployment timestamp when no swaps exist.
        lifespan_hours: float | None = None
        if last_swap_ts is not None:
            base_ts = first_dex_ts or deployed_ts
            if base_ts is not None:
                try:
                    lifespan_hours = round(max((last_swap_ts - base_ts).total_seconds() / 3600, 0.0), 1)
                except (TypeError, ValueError):
                    lifespan_hours = None

        # Survived 7 days: had DEX activity at least 7 days after deployment
        survived_7d = False
        if deployed_ts is not None and last_swap_ts is not None:
            try:
                survived_7d = last_swap_ts >= deployed_ts + timedelta(days=7)
            except (TypeError, ValueError):
                survived_7d = False

        # Only tokens old enough to be judged count toward success rate
        matured = False
        if deployed_ts is not None:
            try:
                matured = deployed_ts + timedelta(days=7) <= now_naive
            except (TypeError, ValueError):
                matured = False

        if matured:
            matured_count += 1
            if survived_7d:
                survived_count += 1
        if lifespan_hours is not None:
            lifespans.append(lifespan_hours)

        token_outcomes.append(
            {
                "token_address": tok["token_address"],
                "chain": tok.get("chain") or chain_norm,
                "deployed_at": to_iso(tok.get("deployed_ts")),
                "deploy_tx": tok.get("deploy_tx"),
                "first_dex_ts": to_iso(first_dex_ts),
                "last_swap_ts": to_iso(last_swap_ts),
                "swap_count": swap_count,
                "lifespan_hours": lifespan_hours,
                "survived_7d": survived_7d,
                "matured": matured,
            }
        )

    success_rate: float | None = None
    if matured_count > 0:
        success_rate = round(survived_count / matured_count, 3)

    median_lifespan_hours: float | None = None
    if lifespans:
        lifespans_sorted = sorted(lifespans)
        n = len(lifespans_sorted)
        if n % 2 == 1:
            median_lifespan_hours = round(lifespans_sorted[n // 2], 1)
        else:
            median_lifespan_hours = round((lifespans_sorted[n // 2 - 1] + lifespans_sorted[n // 2]) / 2, 1)

    # Deployment velocity (deployments per week)
    deployments_per_week = 0.0
    if first_seen is not None and last_seen is not None and last_seen > first_seen:
        try:
            span_weeks = max((last_seen - first_seen).total_seconds() / (7 * 86400), 0.01)
            deployments_per_week = round(total_deployments / span_weeks, 3)
        except (TypeError, ValueError):
            deployments_per_week = 0.0

    # ── Risk bucket + score ──
    metrics: dict[str, Any] = {
        "total_deployments": total_deployments,
        "first_seen": first_seen,
        "survived_count": survived_count,
        "matured_count": matured_count,
        "success_rate": success_rate,
        "any_rug": any(not t["survived_7d"] for t in token_outcomes if t["matured"]),
        "funder_profile": funder_profile,
    }
    risk_bucket = _assign_risk_bucket(metrics, now_naive)
    risk_score = _compute_risk_score(risk_bucket, metrics)

    # Evidence: sample tokens (max 5) + funder proof
    for t in sorted(token_outcomes, key=lambda x: x["deployed_at"] or "", reverse=True)[:5]:
        evidence.append(
            {
                "kind": "token",
                "token_address": t["token_address"],
                "chain": t["chain"],
                "deployed_at": t["deployed_at"],
                "deploy_tx": t["deploy_tx"],
                "survived_7d": t["survived_7d"],
                "lifespan_hours": t["lifespan_hours"],
                "last_swap_ts": t["last_swap_ts"],
            }
        )
    if funder_profile.get("funder_address"):
        evidence.append(
            {
                "kind": "funder",
                "funder_address": funder_profile["funder_address"],
                "funding_tx": funder_profile.get("funding_tx"),
                "funding_ts": funder_profile.get("funding_ts"),
                "funding_token": funder_profile.get("funding_token"),
                "funder_is_cex": funder_profile.get("funder_is_cex"),
                "funder_is_fresh": funder_profile.get("funder_is_fresh"),
                "same_funder_multiple": funder_profile.get("same_funder_multiple"),
            }
        )

    return {
        "check": "deployer_fingerprint",
        "result": "scanned",
        "source": "own",
        "deployer": deployer_address,
        "chain": chain_norm or "any",
        "risk_bucket": risk_bucket,
        "risk_score": risk_score,
        "total_deployments": total_deployments,
        "chains_used": chains_used,
        "first_seen": to_iso(first_seen),
        "last_seen": to_iso(last_seen),
        "deployments_per_week": deployments_per_week,
        "survived_7d": survived_count,
        "matured_tokens": matured_count,
        "success_rate": success_rate,
        "median_lifespan_hours": median_lifespan_hours,
        "funder_profile": funder_profile,
        "evidence": evidence,
        "confidence": _confidence(total_deployments, matured_count, funder_profile),
    }


# ═══════════════════════════════════════════════════════════════════════════
# RISK ASSIGNMENT
# ═══════════════════════════════════════════════════════════════════════════


def _assign_risk_bucket(metrics: dict[str, Any], now_naive: datetime) -> str:
    """Assign the risk bucket per the C7 v1 spec.

    Precedence (operational ordering — see [REVIEW] note in module docstring):
      1. NEW         — first deployment < 7 days ago
      2. PROLIFIC    — >50 deployments AND success_rate >= 0.8 (low rug rate)
      3. FARM        — >3 deployments funded by the same non-CEX wallet
      4. REPEAT_RUG  — any matured deployment had zero survival past 7 days
      5. REPEAT_CLEAN — all matured deployments survived 7+ days

    PROLIFIC is checked before REPEAT_RUG/FARM so a high-volume deployer with a
    genuinely low rug rate is not mislabeled as a serial rugger or farm; FARM is
    checked before REPEAT_RUG because the farm pattern (many deployments from one
    non-CEX wallet) is the more specific signal when it applies.
    """
    total = int(metrics.get("total_deployments", 0) or 0)
    first_seen = metrics.get("first_seen")

    # NEW: first deployment within the last 7 days
    if first_seen is not None:
        try:
            if now_naive - first_seen < timedelta(days=7):
                return "NEW"
        except (TypeError, ValueError):
            pass

    success_rate = metrics.get("success_rate")

    # PROLIFIC: >50 deployments with a confirmed low rug rate
    if total > 50 and success_rate is not None and success_rate >= 0.8:
        return "PROLIFIC"

    funder_profile = metrics.get("funder_profile", {})
    funder = funder_profile.get("funder_address")
    funder_is_cex = bool(funder_profile.get("funder_is_cex"))

    # FARM: >3 deployments funded by the same non-CEX wallet
    if total > 3 and funder and not funder_is_cex:
        return "FARM"

    # REPEAT_RUG: ANY matured deployment had zero survival past 7 days
    if metrics.get("any_rug"):
        return "REPEAT_RUG"

    # REPEAT_CLEAN: >1 deployments, all survived 7+ days
    # (A single old, clean deployment also lands here — see [REVIEW] note in
    # the module docstring: spec buckets do not define single-old-clean.)
    if total >= 1 and not metrics.get("any_rug"):
        return "REPEAT_CLEAN"

    return "NEW"


def _compute_risk_score(bucket: str, metrics: dict[str, Any]) -> int:
    """Map the risk bucket to a 0-100 risk score (higher = riskier)."""
    total = int(metrics.get("total_deployments", 0) or 0)
    survived = int(metrics.get("survived_count", 0) or 0)
    matured = int(metrics.get("matured_count", 0) or 0)
    funder_profile = metrics.get("funder_profile", {})

    if bucket == "NEW":
        return 50  # unknown, neutral
    if bucket == "REPEAT_CLEAN":
        return 15
    if bucket == "REPEAT_RUG":
        rug_count = max(matured - survived, 0)
        return min(95, 85 + min(rug_count, 5) * 2)
    if bucket == "FARM":
        score = 80
        if funder_profile.get("funder_is_fresh"):
            score += 10
        if funder_profile.get("same_funder_multiple"):
            score += 5
        return min(95, score)
    if bucket == "PROLIFIC":
        score = 25
        if matured > 0 and metrics.get("success_rate") is not None and metrics["success_rate"] >= 0.9:
            score -= 10
        return max(15, score)

    # Fallback for unknown bucket / no data
    return 50 if total == 0 else 30


def _confidence(total_deployments: int, matured_count: int, funder_profile: dict[str, Any]) -> float:
    """0.0-1.0 confidence based on data volume."""
    confidence = 0.3
    if total_deployments > 0:
        confidence = min(1.0, 0.4 + 0.1 * min(total_deployments, 5))
    if matured_count > 0:
        confidence = min(1.0, confidence + 0.15)
    if funder_profile.get("funder_address"):
        confidence = min(1.0, confidence + 0.1)
    return round(confidence, 2)


# ═══════════════════════════════════════════════════════════════════════════
# FUNDER PROFILE
# ═══════════════════════════════════════════════════════════════════════════


async def _build_funder_profile(
    funder: dict[str, Any] | None,
    chain_norm: str,
    deployer_address: str,
) -> dict[str, Any]:
    """Enrich the raw funder row with label, freshness, and multiplicity."""
    profile: dict[str, Any] = {
        "funder_address": None,
        "funding_tx": None,
        "funding_ts": None,
        "funding_token": None,
        "funding_native": False,
        "funder_is_cex": False,
        "cex_label": None,
        "funder_is_fresh": None,
        "funder_tx_count": None,
        "same_funder_multiple": None,
    }

    if not funder:
        return profile

    funder_addr = funder.get("from_address")
    if not funder_addr or funder_addr.lower() == deployer_address:
        return profile

    profile["funder_address"] = funder_addr
    profile["funding_tx"] = funder.get("first_tx")
    profile["funding_ts"] = to_iso(funder.get("first_ts"))
    profile["funding_token"] = funder.get("first_token")
    profile["funding_native"] = bool(funder.get("is_native"))

    # Parallel enrichment queries (all bounded, graceful on failure)
    label_task = asyncio.create_task(run_sync(_query_funder_label, funder_addr))
    tx_count_task = asyncio.create_task(run_sync(_query_funder_tx_count, funder_addr))
    overlap_task = asyncio.create_task(run_sync(_query_same_funder_deployers, funder_addr, chain_norm))
    labels_result, tx_count_result, overlap_result = await asyncio.gather(
        label_task, tx_count_task, overlap_task, return_exceptions=True
    )

    labels: list[dict[str, Any]] = []
    if not isinstance(labels_result, BaseException) and labels_result:
        labels = labels_result
    tx_count: int | None = None
    if not isinstance(tx_count_result, BaseException):
        tx_count = tx_count_result
    overlap: int = 0
    if not isinstance(overlap_result, BaseException) and overlap_result is not None:
        overlap = overlap_result

    # CEX classification from wallet_labels
    is_cex, cex_label = _classify_cex(labels)
    profile["funder_is_cex"] = is_cex
    profile["cex_label"] = cex_label
    profile["funder_labels"] = [
        {
            "label_name": lbl.get("label_name"),
            "label_category": lbl.get("label_category"),
            "source": lbl.get("source"),
        }
        for lbl in labels[:5]
    ]

    # Freshness: < 10 total transactions
    if tx_count is not None:
        profile["funder_tx_count"] = int(tx_count)
        profile["funder_is_fresh"] = int(tx_count) < 10

    # Same funder used by 2+ known deployers
    profile["same_funder_multiple"] = int(overlap or 0) >= 2

    return profile


def _classify_cex(labels: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Return (is_cex, cex_label) from a list of wallet_labels rows."""
    for lbl in labels:
        category = (lbl.get("label_category") or "").lower()
        name = (lbl.get("label_name") or "").lower()
        if category in ("exchange", "cex") or any(kw in name for kw in _CEX_KEYWORDS):
            return True, lbl.get("label_name") or name
    return False, None


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_stats(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Aggregate deployment stats from deployer_events."""
    client = _get_ch_client()
    if client is None:
        return None

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                count() AS total_deployments,
                uniqExact(token_address) AS tokens_deployed,
                groupUniqArray(chain) AS chains_used,
                min(block_timestamp) AS first_ts,
                max(block_timestamp) AS last_ts
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            """,  # noqa: S608
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_deployer_stats_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None

    row = rows[0]
    return {
        "total_deployments": int(row[0] or 0),
        "tokens_deployed": int(row[1] or 0),
        "chains_used": sorted({str(c) for c in (row[2] or [])}),
        "first_ts": row[3],
        "last_ts": row[4],
    }


def _query_per_token_deployments(deployer_address: str, chain: str) -> list[dict[str, Any]]:
    """Return per-token deployment records from deployer_events (bounded)."""
    client = _get_ch_client()
    if client is None:
        return []

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address, "limit": 100}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                token_address,
                any(chain) AS chain,
                min(block_timestamp) AS deployed_ts,
                argMin(tx_hash, block_timestamp) AS deploy_tx
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            GROUP BY token_address
            ORDER BY deployed_ts DESC
            LIMIT %(limit)s
            """,  # noqa: S608
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_per_token_deployments_failed: %s", exc)
        return []

    tokens: list[dict[str, Any]] = []
    for row in rows:
        tokens.append(
            {
                "token_address": row[0].lower(),
                "chain": row[1],
                "deployed_ts": row[2],
                "deploy_tx": row[3],
            }
        )
    return tokens


def _query_token_dex_stats(
    tokens: list[dict[str, Any]],
    chain: str,
) -> dict[str, dict[str, Any]]:
    """Compute per-token first/last swap + count from dex_events.

    ``pair_created_ts`` is proxied by the earliest DEX swap timestamp (the
    chain-indexers persist only ``type = 'swap'`` rows). Historical rows with
    empty token0/token1 are covered via the pair→tokens cache.
    """
    client = _get_ch_client()
    if client is None or not tokens:
        return {}

    try:
        from app.domains.scanner.engines.own_dex_data.client import (
            _get_pools_for_token,
            _load_pair_cache,
        )
    except ImportError:
        # Importers available; fall back to None when the module is missing
        _get_pools_for_token = None  # type: ignore[assignment]
        _load_pair_cache = None  # type: ignore[assignment]

    token_addrs = [t["token_address"] for t in tokens]
    pools: set[str] = set()
    if _get_pools_for_token is not None and _load_pair_cache is not None:
        try:
            for t in tokens:
                pair_cache = _load_pair_cache(t.get("chain") or chain or "")
                pools.update(_get_pools_for_token(pair_cache, t["token_address"]))
        except (ValueError, OSError):
            pass

    params: dict[str, Any] = {
        "tokens": tuple(token_addrs),
        "pools": tuple(sorted(pools)) if pools else (),
    }
    where_parts = ["type = 'swap'"]
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    pool_clause = ""
    if pools:
        pool_clause = " OR contract_address IN %(pools)s"
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                token_address,
                min(block_timestamp) AS first_dex_ts,
                max(block_timestamp) AS last_swap_ts,
                count() AS swap_count
            FROM wallet_memory.dex_events
            WHERE {where_clause}
              AND (token0 IN %(tokens)s OR token1 IN %(tokens)s{pool_clause})
            GROUP BY token_address
            """,  # noqa: S608
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_token_dex_stats_failed: %s", exc)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result[row[0].lower()] = {
            "first_dex_ts": row[1],
            "last_swap_ts": row[2],
            "swap_count": int(row[3] or 0),
        }
    return result


def _query_funder(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Find the first wallet that funded the deployer (evm_transfers).

    Prefers native (ETH-like) transfers; falls back to the first ERC-20
    incoming transfer on record.
    """
    client = _get_ch_client()
    if client is None:
        return None

    where_parts = ["to_address = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    # ── Preferred: native currency transfer (ETH / MATIC marker) ──
    native = _query_first_incoming(client, where_clause, params, native_only=True)
    if native:
        return native

    # ── Fallback: any first incoming transfer ──
    return _query_first_incoming(client, where_clause, params, native_only=False)


def _query_first_incoming(
    client: Any,
    where_clause: str,
    params: dict[str, Any],
    native_only: bool,
) -> dict[str, Any] | None:
    """Single first-incoming-transfer query (native filter optional)."""
    try:
        if native_only:
            rows = client.execute(
                f"""
                SELECT
                    from_address,
                    min(block_timestamp) AS first_ts,
                    argMin(tx_hash, block_timestamp) AS first_tx,
                    argMin(token_address, block_timestamp) AS first_token
                FROM wallet_memory.evm_transfers
                WHERE {where_clause}
                  AND lower(token_address) IN %(native_markers)s
                GROUP BY from_address
                ORDER BY first_ts ASC
                LIMIT 1
                """,  # noqa: S608
                {**params, "native_markers": tuple(_NATIVE_TOKEN_MARKERS)},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        else:
            rows = client.execute(
                f"""
                SELECT
                    from_address,
                    min(block_timestamp) AS first_ts,
                    argMin(tx_hash, block_timestamp) AS first_tx,
                    argMin(token_address, block_timestamp) AS first_token
                FROM wallet_memory.evm_transfers
                WHERE {where_clause}
                GROUP BY from_address
                ORDER BY first_ts ASC
                LIMIT 1
                """,  # noqa: S608
                params,
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_funder_query_failed: %s", exc)
        return None

    if not rows:
        return None

    row = rows[0]
    return {
        "from_address": row[0].lower(),
        "first_ts": row[1],
        "first_tx": row[2],
        "first_token": (row[3] or "").lower() if row[3] else None,
        "is_native": native_only,
    }


def _query_funder_label(funder_address: str) -> list[dict[str, Any]]:
    """Look up wallet_labels for the funder (CEX / entity classification)."""
    client = _get_ch_client()
    if client is None:
        return []

    try:
        rows = client.execute(
            """
            SELECT address, chain_id, label_name, label_category, label_subtype, source
            FROM wallet_memory.wallet_labels
            WHERE address = %(addr)s
            ORDER BY loaded_at DESC
            LIMIT 20
            """,
            {"addr": funder_address},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_funder_label_failed: %s", exc)
        return []

    labels: list[dict[str, Any]] = []
    for row in rows:
        labels.append(
            {
                "address": row[0],
                "chain_id": row[1],
                "label_name": row[2] or "",
                "label_category": row[3] or "",
                "label_subtype": row[4] or "",
                "source": row[5] or "",
            }
        )
    return labels


def _query_funder_tx_count(funder_address: str) -> int | None:
    """Count total transactions for the funder (bounded at 11 for freshness)."""
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT count()
            FROM (
                SELECT 1 AS x
                FROM wallet_memory.evm_transfers
                WHERE from_address = %(addr)s OR to_address = %(addr)s
                LIMIT 11
            )
            """,
            {"addr": funder_address},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_funder_tx_count_failed: %s", exc)
        return None

    return int(rows[0][0]) if rows and rows[0] else None


def _query_same_funder_deployers(funder_address: str, chain: str) -> int:
    """Count known deployers funded by the same wallet (2+ = multiple)."""
    client = _get_ch_client()
    if client is None:
        return 0

    try:
        deployer_rows = client.execute(
            """
            SELECT DISTINCT deployer
            FROM wallet_memory.deployer_events
            {chain_clause}
            LIMIT 5000
            """.format(chain_clause="WHERE chain = %(chain)s" if chain else ""),  # noqa: S608  # where_clause/chain_clause built from code literals; values parameterized
            {"chain": chain} if chain else {},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_same_funder_deployer_list_failed: %s", exc)
        return 0

    if not deployer_rows:
        return 0
    deployers = tuple({r[0].lower() for r in deployer_rows})
    if len(deployers) > 5000:
        deployers = tuple(list(deployers)[:5000])

    try:
        rows = client.execute(
            """
            SELECT count(DISTINCT to_address) AS n
            FROM wallet_memory.evm_transfers
            WHERE from_address = %(funder)s
              AND to_address != %(funder)s
              AND to_address IN %(deployers)s
            """,
            {"funder": funder_address, "deployers": deployers},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("fingerprint_same_funder_overlap_failed: %s", exc)
        return 0

    return int(rows[0][0]) if rows and rows[0] else 0


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _unavailable_result(
    deployer_address: str,
    chain_norm: str,
    reason: str = "clickhouse unavailable",
) -> dict[str, Any]:
    """Graceful degradation result — callers fall back to other engines."""
    return {
        "check": "deployer_fingerprint",
        "result": "unavailable",
        "source": "own",
        "deployer": deployer_address,
        "chain": chain_norm or "any",
        "risk_bucket": "NEW",
        "risk_score": 50,
        "total_deployments": 0,
        "success_rate": None,
        "median_lifespan_hours": None,
        "funder_profile": {},
        "evidence": [],
        "confidence": 0.0,
        "note": reason,
    }


def _error_result(message: str) -> dict[str, Any]:
    """Invalid-input error result."""
    return {
        "check": "deployer_fingerprint",
        "result": "error",
        "error": message,
        "risk_bucket": "NEW",
        "risk_score": 50,
        "total_deployments": 0,
        "success_rate": None,
        "median_lifespan_hours": None,
        "funder_profile": {},
        "evidence": [],
        "confidence": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _FINGERPRINT_CACHE,
        _FINGERPRINT_CACHE_MAX,
        _FINGERPRINT_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_FINGERPRINT_CACHE)
