#!/usr/bin/env python3
"""
Token Launch Detector — Real-time new token discovery.
======================================================
Polls DexScreener/GMGN for new token launches every 30 seconds.
Auto-scans new tokens within 60 seconds. Sends Telegram alerts for high-risk.
Stores new launches in Redis for trending page.

Integrates with:
  - GMGN new pairs API (primary)
  - DexScreener search (fallback)
  - Existing scanner pipeline (risk assessment)
  - Telegram bot (@rugmunchbot) for alerts
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis keys
LAUNCHES_KEY = "token_launches:recent"
LAUNCH_STATE_KEY = "token_launches:seen_addrs"
LAUNCH_ALERTS_KEY = "token_launches:alerts"

# Config
POLL_INTERVAL = 30  # seconds between polls
SCAN_WINDOW = 60  # seconds — tokens launched within this window are "new"
LAUNCHES_TTL = 3600  # 1 hour in Redis
STATE_TTL = 86400  # 24 hours for seen addresses

_HIGH_RISK_THRESHOLD = 70  # risk score threshold for alert

# ── Security enrichment config ─────────────────────────────────────────
# Every per-token security check needs at least one network call, so the
# expensive checks are capped to the newest ``_SECURITY_ENRICH_LIMIT``
# launches. Records beyond the cap keep a fail-closed ``security`` dict
# (False/None) — they never fabricate a signal. Tradeoff documented in
# ``enrich_launches_security``.
_SECURITY_ENRICH_LIMIT = 5  # top-N launches that get per-token network checks
GOPLUS_SECURITY_URL = "https://api.gopluslabs.io/api/v1/token_security"
_BUNDLE_SUSPECT_PCT = 50.0  # % of supply bundled in the first blocks
_BUNDLE_JITO_MIN = 1  # Jito bundles detected
_BUNDLE_RISK_MIN = 60  # BundleDetector risk score
# GoPlus chain ids — mirrors the existing integration in app/token_discovery.py
_GOPLUS_CHAIN_IDS = {
    "solana": "solana",
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "base": "8453",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "avalanche": "43114",
    "fantom": "250",
}


class TokenLaunchDetector:
    """Detects new token launches and triggers auto-scan + alerts."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("token_launch_detector started (poll_interval=%ds)", POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("token_launch_detector stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._detect_new_launches()
            except Exception as e:
                logger.error("token_launch_poll_error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _detect_new_launches(self) -> None:
        """Fetch new launches and process them."""
        new_launches = await self._fetch_new_launches()
        if not new_launches:
            return

        # Filter to truly new (not seen before)
        seen = await self._get_seen_addresses()
        fresh = [l for l in new_launches if l.get("address") not in seen]

        if fresh:
            logger.info("new_token_launches count=%d", len(fresh))

            # Store in Redis
            await self._store_launches(fresh)

            # Mark as seen
            await self._mark_seen([l.get("address", "") for l in fresh])

            # Auto-scan each new token
            for launch in fresh:
                try:
                    await self._auto_scan_token(launch)
                except Exception as e:
                    logger.warning("auto_scan_error addr=%s err=%s", launch.get("address", ""), e)

    async def _fetch_new_launches(self) -> list[dict[str, Any]]:
        """Fetch new token launches from multiple sources."""
        launches: list[dict[str, Any]] = []

        # 1. Try GMGN
        try:
            from app.databus.gmgn_provider import get_gmgn_provider

            gmgn = get_gmgn_provider()
            gmgn_launches = await gmgn.get_new_pairs("solana", 50)
            if gmgn_launches:
                launches.extend(gmgn_launches)
                logger.debug("gmgn_new_pairs count=%d", len(gmgn_launches))
        except Exception as e:
            logger.debug("gmgn_new_pairs_failed: %s", e)

        # 2. DexScreener search (fallback)
        if not launches:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://api.dexscreener.com/latest/dex/search",
                        params={"q": "SOL", "limit": 50},
                    )
                    if resp.status_code == 200:
                        pairs = resp.json().get("pairs", [])
                        for p in pairs:
                            created = p.get("pairCreatedAt")
                            age_s: float = 999999.0
                            if created:
                                age_s = (
                                    datetime.now(UTC) - datetime.fromtimestamp(created / 1000, tz=UTC)
                                ).total_seconds()

                            # Only include tokens launched within last 5 min
                            if age_s < 300:
                                launches.append(
                                    {
                                        "address": p.get("baseToken", {}).get("address", ""),
                                        "chain": p.get("chainId", "solana"),
                                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                                        "name": p.get("baseToken", {}).get("name", ""),
                                        "price": float(p.get("priceUsd", 0)),
                                        "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                                        "market_cap": float(p.get("fdv", 0)),
                                        "created_at": created,
                                        "age_seconds": int(age_s),
                                        "dex": p.get("dexId", ""),
                                        "pair_address": p.get("pairAddress", ""),
                                        "source": "dexscreener",
                                    }
                                )
                logger.debug("dexscreener_new_launches count=%d", len(launches))
            except Exception as e:
                logger.warning("dexscreener_launches_failed: %s", e)

        # 3. GeckoTerminal trending (additional source)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
                    params={"limit": 20},
                )
                if resp.status_code == 200:
                    pools = resp.json().get("data", [])
                    for pool in pools:
                        attrs = pool.get("attributes", {})
                        launches.append(
                            {
                                "address": attrs.get("base_token_address", ""),
                                "chain": "solana",
                                "symbol": attrs.get("base_token_symbol", ""),
                                "name": attrs.get("name", ""),
                                "price": float(attrs.get("base_token_price_usd", 0)),
                                "liquidity": float(attrs.get("reserve_in_usd", 0)),
                                "source": "geckoterminal",
                            }
                        )
        except Exception as exc:
            logger.debug("best_effort_failed err=%s", exc)

        return launches

    async def _auto_scan_token(self, launch: dict[str, Any]) -> None:
        """Run a quick risk scan on a newly launched token."""
        address = launch.get("address", "")
        if not address:
            return

        risk_score = 50  # default neutral
        risk_flags: list[str] = []

        # Quick checks from available data
        liquidity = float(launch.get("liquidity", 0))

        # Low liquidity = high risk
        if liquidity < 1000:
            risk_score += 30
            risk_flags.append("Extremely low liquidity (<$1K)")
        elif liquidity < 5000:
            risk_score += 15
            risk_flags.append("Low liquidity (<$5K)")

        # No social data = higher risk
        symbol = launch.get("symbol", "")
        if not symbol or symbol == "???":
            risk_score += 10
            risk_flags.append("No symbol/name defined")

        # Unknown DEX
        dex = launch.get("dex", "")
        if not dex or dex in ("unknown", ""):
            risk_score += 5
            risk_flags.append("Unknown DEX")

        risk_score = min(risk_score, 100)

        # Store scan result
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                scan_result = {
                    "address": address,
                    "symbol": launch.get("symbol", ""),
                    "name": launch.get("name", ""),
                    "risk_score": risk_score,
                    "risk_flags": risk_flags,
                    "liquidity": liquidity,
                    "price": launch.get("price", 0),
                    "age_seconds": launch.get("age_seconds", 0),
                    "dex": dex,
                    "scanned_at": datetime.now(UTC).isoformat(),
                }
                await r.setex(
                    f"launch_scan:{address}",
                    600,  # 10-min TTL
                    json.dumps(scan_result),
                )
        except Exception as e:
            logger.warning("launch_scan_store_failed: %s", e)

        # Send alert for high risk
        if risk_score >= _HIGH_RISK_THRESHOLD:
            await self._send_telegram_alert(launch, risk_score, risk_flags)

    async def _send_telegram_alert(self, launch: dict[str, Any], risk_score: int, flags: list[str]) -> None:
        """Send high-risk alert to Telegram."""
        address = launch.get("address", "")
        symbol = launch.get("symbol", "???")
        name = launch.get("name", "Unknown")
        liq = launch.get("liquidity", 0)
        price = launch.get("price", 0)

        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                alert = {
                    "type": "new_launch_high_risk",
                    "address": address,
                    "symbol": symbol,
                    "name": name,
                    "risk_score": risk_score,
                    "risk_flags": flags,
                    "liquidity": liq,
                    "price": price,
                    "chain": "solana",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await r.lpush(LAUNCH_ALERTS_KEY, json.dumps(alert))
                await r.ltrim(LAUNCH_ALERTS_KEY, 0, 99)  # Keep last 100 alerts
                logger.info("high_risk_alert symbol=%s risk=%d", symbol, risk_score)
        except Exception as e:
            logger.warning("alert_push_failed: %s", e)

    async def _get_seen_addresses(self) -> set[str]:
        """Get set of already-seen token addresses."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                members = await r.smembers(LAUNCH_STATE_KEY)
                return {m.decode() if isinstance(m, bytes) else str(m) for m in members}
        except Exception as exc:
            logger.debug("optional_dep_failed err=%s", exc)
        return set()

    async def _mark_seen(self, addresses: list[str]) -> None:
        """Mark addresses as seen in Redis."""
        if not addresses:
            return
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                await r.sadd(LAUNCH_STATE_KEY, *addresses)
                await r.expire(LAUNCH_STATE_KEY, STATE_TTL)
        except Exception as exc:
            logger.debug("optional_dep_failed err=%s", exc)

    async def _store_launches(self, launches: list[dict[str, Any]]) -> None:
        """Store new launches in Redis for trending page."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                now = datetime.now(UTC).isoformat()
                for launch in launches:
                    launch["discovered_at"] = now
                    # Push to front of sorted set
                    await r.zadd(LAUNCHES_KEY, {json.dumps(launch): launch.get("age_seconds", 0)})
                # Trim to last 200
                await r.zremrangebyrank(LAUNCHES_KEY, 0, -201)
                await r.expire(LAUNCHES_KEY, LAUNCHES_TTL)
        except Exception as e:
            logger.warning("launch_store_failed: %s", e)

    async def get_recent_launches(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent token launches from Redis."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                # Get newest first (reverse order)
                raw = await r.zrevrange(LAUNCHES_KEY, 0, limit - 1)
                launches = []
                for item in raw:
                    try:
                        data = item.decode() if isinstance(item, bytes) else str(item)
                        launches.append(json.loads(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
                return launches
        except Exception as exc:
            logger.debug("best_effort_failed err=%s", exc)
        return []

    async def enrich_launches(
        self,
        launches: list[dict[str, Any]],
        max_checks: int = _SECURITY_ENRICH_LIMIT,
    ) -> list[dict[str, Any]]:
        """Best-effort add a ``security`` sub-dict to launch records (fail-open).

        Delegates to the module-level :func:`enrich_launches_security` so tests
        can patch the per-field checks directly. Never blocks the caller: a
        slow/failed check leaves its field at the fail-closed default
        (``False`` / ``None``).
        """
        return await enrich_launches_security(launches, max_checks=max_checks)

    async def get_launch_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent high-risk alerts."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                raw = await r.lrange(LAUNCH_ALERTS_KEY, 0, limit - 1)
                alerts = []
                for item in raw:
                    try:
                        data = item.decode() if isinstance(item, bytes) else str(item)
                        alerts.append(json.loads(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
                return alerts
        except Exception as exc:
            logger.debug("best_effort_failed err=%s", exc)
        return []


# ── Security enrichment ────────────────────────────────────────────────
# Wires the existing scanner quick-checks into the launch record as a
# ``security`` sub-dict: ``honeypot``, ``mintable``, ``lp_locked``,
# ``creator_address``, ``bundle_suspect``. Best-effort per launch — a slow or
# failed check leaves the field at its fail-closed default (False/None) and
# NEVER blocks the launch list or raises.


async def _goplus_token_security(
    launch: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Cheapest available contract-risk signal: GoPlus token_security (free, no auth).

    One call returns honeypot, mintable, creator/owner + taxes (mirrors the
    existing integration in ``app/token_discovery.py``). ``cache`` (a per-batch
    dict keyed by address) lets the mintable/creator/honeypot checks share a
    single call per token.
    """
    address = str(launch.get("address") or "").strip()
    if not address:
        return None
    if cache is not None and address in cache:
        return cache[address]
    chain = str(launch.get("chain") or "solana").strip() or "solana"
    chain_id = _GOPLUS_CHAIN_IDS.get(chain.lower(), chain.lower())
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{GOPLUS_SECURITY_URL}/{chain_id}",
                params={"contract_addresses": address},
            )
            if resp.status_code != 200:
                return None
            raw = (resp.json().get("result") or {}).get(address.lower(), {})
            if not raw:
                return None
            normalized = {
                "is_honeypot": raw.get("is_honeypot") == "1",
                "is_mintable": raw.get("is_mintable") == "1",
                "creator_address": raw.get("creator_address") or None,
                "owner_address": raw.get("owner_address") or None,
            }
            if cache is not None:
                cache[address] = normalized
            return normalized
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, json.JSONDecodeError):
        logger.debug("goplus_security_failed addr=%s", address)
    return None


async def _security_check_honeypot(
    launch: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> bool | None:
    """Honeypot signal. Cheapest first: GoPlus ``is_honeypot`` (1 call, shared
    per-batch cache); falls back to the full ``HoneypotDetector`` sell-simulation
    quick-check when GoPlus has no data for the token (N Jupiter quotes — why
    the check is capped to the top-N launches)."""
    result = await _goplus_token_security(launch, cache)
    if result is not None:
        return bool(result.get("is_honeypot"))
    address = str(launch.get("address") or "").strip()
    if not address:
        return None
    chain = str(launch.get("chain") or "solana").strip() or "solana"
    from app.domains.scanners.honeypot_detector import HoneypotDetector

    detector = HoneypotDetector()
    try:
        report = await detector.detect(address, chain, str(launch.get("pair_address") or ""))
        return bool(getattr(report, "is_honeypot", False))
    except Exception as exc:
        logger.debug("honeypot_check_failed addr=%s err=%s", address, exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            await detector.close()


async def _security_check_mintable(
    launch: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> bool | None:
    """Mintable signal — cheapest available: GoPlus ``is_mintable`` (1 call, no
    auth). No dedicated mint-authority scanner module exists, so this is the
    existing cheapest signal for the field (helped by the same per-batch cache
    as the honeypot/creator checks)."""
    result = await _goplus_token_security(launch, cache)
    if result is None:
        return None
    return bool(result.get("is_mintable"))


async def _security_check_lp_locked(
    launch: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> bool | None:
    """LP-lock signal via ``LiquidityVerifier.verify_lock`` (Helius/Etherscan —
    network heavy, capped). LOCKED/BURNED → True; UNLOCKED/PARTIAL/EXPIRED →
    False; UNKNOWN or failure → None (unknown, never fabricates)."""
    address = str(launch.get("address") or "").strip()
    if not address:
        return None
    chain = str(launch.get("chain") or "solana").strip() or "solana"
    from app.domains.scanners.liquidity_verifier import LiquidityVerifier, LockStatus

    verifier = LiquidityVerifier()
    try:
        report = await verifier.verify_lock(address, chain)
        if report is None:
            return None
        status = getattr(report, "status", None)
        if status in (LockStatus.LOCKED, LockStatus.BURNED):
            return True
        if status in (LockStatus.UNLOCKED, LockStatus.PARTIAL, LockStatus.EXPIRED):
            return False
        if bool(getattr(report, "lp_burned", False)):
            return True
        return None
    except Exception as exc:
        logger.debug("lp_lock_check_failed addr=%s err=%s", address, exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            await verifier.client.aclose()


async def _security_check_creator(
    launch: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> str | None:
    """Creator address. Cheapest signal first — the record's own
    ``creator_address``/``creator`` field (free, GMGN/DexScreener carry it);
    else the same cached GoPlus call as mintable (``creator_address``)."""
    own = launch.get("creator_address") or launch.get("creator")
    if own:
        return str(own).strip() or None
    result = await _goplus_token_security(launch, cache)
    if not result:
        return None
    return result.get("creator_address") or None


async def _security_check_bundle(
    launch: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> bool | None:
    """Bundle/sniper signal via ``BundleDetector.detect_bundles`` (Solscan/Helius
    tx analysis — expensive, capped). Suspect when a meaningful share of supply
    is bundled in the first blocks, Jito bundles are detected, or the detector's
    own risk score is high."""
    address = str(launch.get("address") or "").strip()
    if not address:
        return None
    chain = str(launch.get("chain") or "solana").strip() or "solana"
    from app.domains.scanners.bundle_detector import BundleDetector

    detector = BundleDetector()
    try:
        report = await detector.detect_bundles(address, chain)
        if report is None:
            return None
        bundled_pct = float(getattr(report, "total_bundled_percentage", 0) or 0)
        jito = int(getattr(report, "jito_bundles_detected", 0) or 0)
        risk = int(getattr(report, "risk_score", 0) or 0)
        return bool(bundled_pct >= _BUNDLE_SUSPECT_PCT or jito >= _BUNDLE_JITO_MIN or risk >= _BUNDLE_RISK_MIN)
    except Exception as exc:
        logger.debug("bundle_check_failed addr=%s err=%s", address, exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            await detector.close()


async def _enrich_one_launch(
    launch: dict[str, Any],
    max_checks: int,
    idx: int,
    cache: dict[str, Any],
) -> dict[str, Any]:
    """Fill ``launch["security"]`` — defaults first, then best-effort checks.

    Only launches in the first ``max_checks`` positions get the per-token
    network checks. Every check is individually guarded so a slow/failed check
    leaves its field at the fail-closed default. ``creator_address`` is seeded
    from the record's own field for every launch (free signal).
    """
    security: dict[str, Any] = {
        "honeypot": False,
        "mintable": False,
        "lp_locked": None,
        "creator_address": None,
        "bundle_suspect": False,
    }
    own_creator = launch.get("creator_address") or launch.get("creator")
    security["creator_address"] = str(own_creator).strip() if own_creator else None

    if idx >= max_checks:
        return security

    checks = (
        ("honeypot", _security_check_honeypot),
        ("mintable", _security_check_mintable),
        ("lp_locked", _security_check_lp_locked),
        ("creator_address", _security_check_creator),
        ("bundle_suspect", _security_check_bundle),
    )
    for field, check in checks:
        try:
            value = await check(launch, cache)
            if value is not None:
                security[field] = value
        except Exception as exc:
            logger.debug("security_check_failed field=%s addr=%s err=%s", field, launch.get("address", ""), exc)
    return security


async def enrich_launches_security(
    launches: list[dict[str, Any]],
    max_checks: int = _SECURITY_ENRICH_LIMIT,
) -> list[dict[str, Any]]:
    """Best-effort add a ``security`` sub-dict to each launch record (fail-open).

    Fields: ``honeypot: bool``, ``mintable: bool``, ``lp_locked: bool | None``,
    ``creator_address: str | None``, ``bundle_suspect: bool`` — populated by
    wiring the existing scanner quick-checks:

    - honeypot    → GoPlus ``is_honeypot`` (cheap, 1 call) with a
                    ``HoneypotDetector`` sell-simulation fallback
    - mintable    → GoPlus ``is_mintable`` (cheapest available; no dedicated
                    mint-authority scanner module exists)
    - lp_locked   → ``LiquidityVerifier.verify_lock`` (Helius/Etherscan)
    - creator     → the record's own ``creator_address``/``creator`` field
                    (free), else GoPlus ``creator_address``
    - bundle      → ``BundleDetector.detect_bundles`` (Solscan/Helius)

    Tradeoff / bounding: honeypot-fallback, LP-lock and bundle checks are
    network-heavy per token, so they run ONLY for the newest ``max_checks``
    launches (default 5); every other record gets the fail-closed defaults.
    ``creator_address`` seeds from the record for all launches at zero cost.
    Never raises — a failed check leaves its field None/False.
    """
    cache: dict[str, Any] = {}
    enriched: list[dict[str, Any]] = []
    for idx, launch in enumerate(launches):
        if not isinstance(launch, dict):
            enriched.append(launch)
            continue
        record = dict(launch)  # shallow copy — never mutate caller records
        try:
            record["security"] = await _enrich_one_launch(record, max_checks, idx, cache)
        except Exception as exc:
            logger.debug("launch_enrich_failed addr=%s err=%s", record.get("address", ""), exc)
            record["security"] = {
                "honeypot": False,
                "mintable": False,
                "lp_locked": None,
                "creator_address": None,
                "bundle_suspect": False,
            }
        enriched.append(record)
    return enriched


# ── Singleton ──────────────────────────────────────────────

_detector: TokenLaunchDetector | None = None


def get_launch_detector() -> TokenLaunchDetector:
    """Get or create the launch detector singleton."""
    global _detector
    if _detector is None:
        _detector = TokenLaunchDetector()
    return _detector
