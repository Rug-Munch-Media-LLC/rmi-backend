"""RAG learning loop — the scanner learns from every confirmed rug.

When a token is confirmed as a rug (dev abandons, liquidity pulled, price
crashes 90%+), its scan data is embedded into the Qdrant ``rug_patterns``
collection as a behavioral fingerprint. Future scans search that collection
for similar historical rugs so predictions reference real confirmed outcomes.

Lanes (mirrors app/integrations/rugbrain_client.py):
  - Embed: DeepInfra Qwen3-Embedding-4B (2560-dim, matches Qdrant corpus)
  - Store/search: Qdrant direct (rmi-qdrant-v2:6333) — the rugbrain HTTP API
    binds host-loopback and is unreachable from the backend container.
  - Ingest fallback: rugbrain ``POST /api/v1/ingest`` (collection=rug_patterns)

All functions fail-open: they log and return None/[]/False, never raise.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

RUG_PATTERNS_COLLECTION = "rug_patterns"
EMBED_DIM = 2560  # DeepInfra Qwen3-Embedding-4B

# Confirmation thresholds
PRICE_CRASH_PCT = 0.90  # price dropped 90%+ from scan time
LIQUIDITY_REMOVED_PCT = 0.90  # liquidity dropped 90%+ from scan time
DEV_SOLD_ALL_PCT = 0.95  # deployer sold 95%+ of their position

# DexScreener — free, no auth (same source the rug predictor uses)
_DEXSCREENER = "https://api.dexscreener.com/latest/dex"


# ── Fingerprint construction ─────────────────────────────────────────


def _num(v: Any) -> float | None:
    """Best-effort float coercion; None when not numeric."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def build_fingerprint(scan_result: dict[str, Any]) -> str:
    """Build a behavioral fingerprint text from a scan result dict.

    Captures the six behavioral dimensions that define a rug pattern:
    deployer profile, holder distribution, bundle pattern, volume
    authenticity, liquidity state and price action — as compact
    ``key=value`` facts so the embedding encodes the *shape* of the rug,
    not the token's name.
    """
    raw = scan_result.get("raw")
    scan: dict[str, Any] = {}
    if isinstance(raw, str):
        try:
            scan = json.loads(raw)
        except (TypeError, ValueError):
            scan = {}
    elif isinstance(raw, dict):
        scan = raw
    if not scan:
        scan = {k: v for k, v in scan_result.items() if k != "raw"}

    facts: list[str] = []

    def add(key: str, val: Any) -> None:
        if val is None or val == "":
            return
        facts.append(f"{key}={val}")

    # ── Deployer profile ──
    dev = scan.get("dev_reputation") or scan.get("deployer") or {}
    if isinstance(dev, dict):
        add("deployer_risk", _num(dev.get("risk_score")))
        add("deployer_label", dev.get("risk_label") or dev.get("reputation_label"))
        add("deployer_prior_rugs", _num(dev.get("rug_count") or dev.get("prior_rugs")))
        add("deployer_sold_pct", _num(dev.get("sold_pct") or dev.get("dev_sold_pct")))
    add("wallet_freshness", scan.get("wallet_freshness"))
    fa = scan.get("funding_analysis")
    if isinstance(fa, dict):
        add("funding_source_risk", _num(fa.get("risk_score")))
        add("funding_source", fa.get("funding_source") or fa.get("source"))

    # ── Holder distribution ──
    ho = scan.get("holder_overlap")
    if isinstance(ho, dict):
        add("holder_overlap_pct", _num(ho.get("overlap_pct") or ho.get("overlap_ratio")))
        add("holder_clusters", _num(ho.get("cluster_count")))
    wm = scan.get("wallet_metrics")
    if isinstance(wm, dict):
        add("top10_concentration", _num(wm.get("top10_pct") or wm.get("top10_concentration")))
        add("holders", _num(wm.get("holders")))

    # ── Bundle pattern ──
    ba = scan.get("bundle_analysis")
    if isinstance(ba, dict):
        add("bundle_detected", ba.get("detected", ba.get("bundle_detected")))
        add("bundle_score", _num(ba.get("score") or ba.get("bundle_score")))
        add("bundle_wallets", _num(ba.get("wallet_count") or ba.get("bundled_wallets")))

    # ── Volume authenticity ──
    add("volume_24h_usd", _num(scan.get("volume_24h_usd")))
    add("volume_authenticity", scan.get("volume_authenticity"))
    wt = scan.get("wash_trading") or {}
    if isinstance(wt, dict):
        add("wash_trading_score", _num(wt.get("score")))

    # ── Liquidity state at scan time ──
    add("liquidity_usd", _num(scan.get("liquidity_usd")))
    add("market_cap_usd", _num(scan.get("market_cap_usd")))
    add("age_hours", _num(scan.get("age_hours")))

    # ── Price action pattern ──
    add("price_usd", _num(scan.get("price_usd")))
    add("safety_score", _num(scan.get("safety_score")))
    add("risk_level", scan.get("risk_level"))
    flags = scan.get("risk_flags")
    if isinstance(flags, list):
        facts.append("risk_flags=" + ",".join(str(f)[:40] for f in flags[:8]))

    return " ".join(facts) if facts else "no_behavioral_data"


# ── Qdrant helpers (direct lane, mirrors rugbrain_client) ────────────


def _qdrant_base() -> str:
    return os.getenv("QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")).rstrip("/")


def _point_id(chain: str, address: str) -> str:
    """Deterministic UUID so re-learning the same token is an idempotent upsert."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rug_patterns:{chain}:{address.lower()}"))


async def _ensure_collection() -> bool:
    """Best-effort create the ``rug_patterns`` collection (2560-dim cosine)."""
    body = {"vectors": {"size": EMBED_DIM, "distance": "Cosine"}}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(f"{_qdrant_base()}/collections/{RUG_PATTERNS_COLLECTION}", json=body)
        return r.status_code in (200, 409)
    except (httpx.HTTPError, OSError, TimeoutError) as e:
        log.warning("rug_patterns_collection_ensure_failed err=%s", e)
        return False


async def _embed(text: str) -> list[float] | None:
    """Embed via the shared DeepInfra lane (same 2560-dim space as the corpus)."""
    from app.integrations.rugbrain_client import _deepinfra_embed

    return await _deepinfra_embed(text)


# ── Public API ───────────────────────────────────────────────────────


async def record_confirmed_rug(
    scan_result: dict[str, Any],
    chain: str,
    address: str,
    rug_type: str,
) -> bool:
    """Embed a confirmed rug in Qdrant ``rug_patterns``.

    Creates a behavioral fingerprint from the scan (deployer profile, holder
    distribution, bundle pattern, volume authenticity, liquidity state, price
    action) and stores it with metadata: address, chain, rug_type, timestamp,
    scan_data. Returns True on success; never raises.
    """
    fingerprint = build_fingerprint(scan_result)
    if fingerprint == "no_behavioral_data":
        log.warning("rug_learn_skipped_no_data chain=%s address=%s", chain, address[:16])
        return False

    vector = await _embed(fingerprint)
    if not vector:
        # Fallback: rugbrain ingest lane (it embeds server-side)
        from app.integrations.rugbrain_client import rugbrain_ingest

        return await rugbrain_ingest(
            content=fingerprint,
            collection=RUG_PATTERNS_COLLECTION,
            doc_id=f"rug:{chain}:{address[:48]}",
            metadata={
                "address": address,
                "chain": chain,
                "rug_type": rug_type,
                "confirmed_at": datetime.now(UTC).isoformat(),
                "scan_data": _slim_scan(scan_result),
            },
            source="rug_learning_loop",
            severity="critical",
            chain=chain,
        )

    await _ensure_collection()
    payload = {
        "content": fingerprint,
        "metadata": {
            "address": address,
            "chain": chain,
            "rug_type": rug_type,
            "confirmed_at": datetime.now(UTC).isoformat(),
            "scan_data": _slim_scan(scan_result),
        },
        "collection": RUG_PATTERNS_COLLECTION,
        "doc_id": f"rug:{chain}:{address[:48]}",
    }
    body = {
        "points": [
            {
                "id": _point_id(chain, address),
                "vector": vector,
                "payload": payload,
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.put(
                f"{_qdrant_base()}/collections/{RUG_PATTERNS_COLLECTION}/points?wait=true",
                json=body,
                headers={"Content-Type": "application/json"},
            )
        ok = r.status_code == 200
        if ok:
            log.info("rug_pattern_embedded chain=%s address=%s rug_type=%s", chain, address[:16], rug_type)
        else:
            log.warning("rug_pattern_upsert_failed status=%s body=%s", r.status_code, r.text[:200])
        return ok
    except (httpx.HTTPError, OSError, TimeoutError) as e:
        log.warning("rug_pattern_upsert_error chain=%s address=%s err=%s", chain, address[:16], e)
        return False


def _slim_scan(scan_result: dict[str, Any], max_bytes: int = 6000) -> dict[str, Any]:
    """Trim scan_data for Qdrant payload storage (payloads should stay small)."""
    slim = {
        k: v
        for k, v in scan_result.items()
        if k
        in (
            "price_usd",
            "liquidity_usd",
            "market_cap_usd",
            "volume_24h_usd",
            "age_hours",
            "safety_score",
            "risk_level",
            "risk_flags",
            "dev_reputation",
            "holder_overlap",
            "wallet_metrics",
            "bundle_analysis",
            "wallet_freshness",
            "funding_analysis",
            "scanned_at",
            "symbol",
            "name",
        )
    }
    out = json.dumps(slim, default=str)
    if len(out) > max_bytes:
        slim["risk_flags"] = (slim.get("risk_flags") or [])[:5]
        out = json.dumps(slim, default=str)
    if len(out) > max_bytes:
        return {"truncated": out[:max_bytes]}
    return slim


async def find_similar_rugs(
    scan_result: dict[str, Any],
    chain: str,
    address: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search Qdrant ``rug_patterns`` for similar behavioral fingerprints.

    Returns a list of similar confirmed rugs with similarity score, rug type,
    what happened, timeline from launch to rug, and key pattern matches.
    Empty list on any failure — never raises.
    """
    fingerprint = build_fingerprint(scan_result)
    if fingerprint == "no_behavioral_data":
        return []

    vector = await _embed(fingerprint)
    if not vector:
        return []

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{_qdrant_base()}/collections/{RUG_PATTERNS_COLLECTION}/points/search",
                json={"vector": vector, "limit": limit + 1, "with_payload": True},
                headers={"Content-Type": "application/json"},
            )
        if r.status_code != 200:
            log.warning("rug_similar_search_failed status=%s", r.status_code)
            return []
        points = r.json().get("result") or []
    except (httpx.HTTPError, OSError, TimeoutError) as e:
        log.warning("rug_similar_search_error err=%s", e)
        return []

    out: list[dict[str, Any]] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        pl = p.get("payload") or {}
        meta = pl.get("metadata") or {}
        meta_addr = str(meta.get("address") or "")
        # Skip the token itself (it may already be recorded as a rug)
        if meta_addr.lower() == address.lower() and str(meta.get("chain") or "") == chain:
            continue
        scan_data = meta.get("scan_data") or {}
        out.append(
            {
                "similarity": round(float(p.get("score") or 0.0), 4),
                "rug_type": meta.get("rug_type") or "unknown",
                "what_happened": _what_happened(meta.get("rug_type"), scan_data),
                "timeline": _timeline(scan_data),
                "key_pattern_matches": _key_pattern_matches(scan_result, scan_data),
                "address": meta_addr,
                "chain": meta.get("chain") or "",
                "confirmed_at": meta.get("confirmed_at") or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def _what_happened(rug_type: Any, scan_data: dict[str, Any]) -> str:
    """Human-readable outcome description from rug type + stored scan data."""
    descriptions = {
        "liquidity_pulled": "Liquidity was removed from the pool after launch",
        "price_crash": "Price collapsed 90%+ from its scanned level",
        "dev_exit": "Deployer sold their entire position and abandoned the project",
        "dev_abandoned": "Developer abandoned the project; trading activity died",
    }
    base = descriptions.get(str(rug_type or "").lower(), "Token exhibited confirmed rug behavior")
    age = _num(scan_data.get("age_hours"))
    if age is not None:
        base += f" (token was {age:.0f}h old at last scan)"
    return base


def _timeline(scan_data: dict[str, Any]) -> dict[str, Any]:
    """Launch-to-rug timeline reconstructed from stored scan data."""
    scanned_at = scan_data.get("scanned_at") or ""
    age = _num(scan_data.get("age_hours"))
    out: dict[str, Any] = {"scanned_at": scanned_at}
    if age is not None:
        out["launch_age_hours"] = age
    confirmed = scan_data.get("confirmed_at")
    if confirmed:
        out["confirmed_at"] = confirmed
    return out


def _key_facts(scan: dict[str, Any]) -> dict[str, Any]:
    """Extract the comparable behavioral dimensions from a scan dict."""
    raw = scan.get("raw")
    if isinstance(raw, str):
        with contextlib.suppress(TypeError, ValueError):
            scan = json.loads(raw)
    elif isinstance(raw, dict):
        scan = raw
    ba = scan.get("bundle_analysis") or {}
    ho = scan.get("holder_overlap") or {}
    wm = scan.get("wallet_metrics") or {}
    dev = scan.get("dev_reputation") or {}
    return {
        "liquidity_usd": _num(scan.get("liquidity_usd")),
        "volume_24h_usd": _num(scan.get("volume_24h_usd")),
        "age_hours": _num(scan.get("age_hours")),
        "safety_score": _num(scan.get("safety_score")),
        "bundle_score": _num(ba.get("score") if isinstance(ba, dict) else None),
        "holder_overlap_pct": _num(ho.get("overlap_pct") if isinstance(ho, dict) else None),
        "top10_concentration": _num(wm.get("top10_pct") if isinstance(wm, dict) else None),
        "deployer_risk": _num(dev.get("risk_score") if isinstance(dev, dict) else None),
    }


def _key_pattern_matches(current: dict[str, Any], historical: dict[str, Any]) -> list[str]:
    """Name the behavioral dimensions the two scans share (within tolerance)."""
    a = _key_facts(current)
    b = _key_facts(historical)
    matches: list[str] = []
    for key, av in a.items():
        bv = b.get(key)
        if av is None or bv is None:
            continue
        if av == 0 and bv == 0:
            matches.append(f"{key}: both zero")
        elif max(abs(av), abs(bv)) > 0:
            rel = abs(av - bv) / max(abs(av), abs(bv))
            if rel <= 0.25:
                matches.append(f"{key}: {av:.4g} ≈ {bv:.4g}")
    return matches[:6]


# ── Rug confirmation check ───────────────────────────────────────────


async def _latest_scan(chain: str, address: str) -> dict[str, Any] | None:
    """Fetch the most recent scan row for a token from Postgres (7-day window)."""
    try:
        from sqlalchemy import select

        from app.core.database import async_session_factory
        from app.models.scan import ScanResult as ScanResultORM

        cutoff = datetime.now(UTC) - timedelta(days=7)
        async with async_session_factory() as session:
            stmt = (
                select(ScanResultORM)
                .where(
                    ScanResultORM.chain == chain,
                    ScanResultORM.token_address == address,
                    ScanResultORM.scanned_at >= cutoff,
                )
                .order_by(ScanResultORM.scanned_at.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        extra = row.extra if isinstance(row.extra, dict) else {}
        raw = extra.get("raw")
        scan: dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                scan = json.loads(raw)
            except (TypeError, ValueError):
                scan = {}
        return {
            "token_address": row.token_address,
            "chain": row.chain,
            "symbol": row.symbol,
            "name": row.name,
            "scanned_at": row.scanned_at.isoformat() if row.scanned_at else "",
            "safety_score": row.safety_score,
            "risk_level": row.risk_level,
            "risk_flags": row.risk_flags or [],
            **scan,
        }
    except Exception as e:
        log.warning("rug_learning_latest_scan_failed chain=%s err=%s", chain, e)
        return None


async def _current_market(address: str) -> dict[str, Any] | None:
    """Current price/liquidity via DexScreener (free, no auth)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_DEXSCREENER}/search?q={address}")
        if r.status_code != 200:
            return None
        pairs = r.json().get("pairs") or []
        # Prefer the pair whose base token IS this token
        pair = next(
            (p for p in pairs if (p.get("baseToken") or {}).get("address", "").lower() == address.lower()), None
        )
        pair = pair or (pairs[0] if pairs else None)
        if not pair:
            return None
        return {
            "price_usd": _num(pair.get("priceUsd")),
            "liquidity_usd": _num((pair.get("liquidity") or {}).get("usd")),
            "pair_created_at": pair.get("pairCreatedAt"),
        }
    except (httpx.HTTPError, OSError, TimeoutError, ValueError) as e:
        log.debug("rug_learning_market_fetch_failed address=%s err=%s", address[:16], e)
        return None


async def check_rug_confirmed(chain: str, address: str) -> dict[str, Any] | None:
    """Check if a previously scanned token has been confirmed as a rug.

    Checks (vs the most recent stored scan within 7 days):
      - price dropped 90%+ since scan
      - liquidity removed (dropped 90%+ since scan)
      - deployer sold all (sold_pct >= 95% in stored dev data)

    Returns ``{"rug_type", "scan_result", "evidence"}`` when confirmed,
    ``None`` otherwise (including when there is no scan or market data).
    """
    scan = await _latest_scan(chain, address)
    if not scan:
        return None
    market = await _current_market(address)
    if not market:
        return None

    scan_price = _num(scan.get("price_usd"))
    scan_liq = _num(scan.get("liquidity_usd"))
    cur_price = market.get("price_usd")
    cur_liq = market.get("liquidity_usd")

    evidence: list[str] = []
    rug_type: str | None = None

    if scan_price and cur_price is not None and cur_price > 0:
        drop = max(1 - (cur_price / scan_price), 0.0)
        if drop >= PRICE_CRASH_PCT:
            rug_type = "price_crash"
            evidence.append(f"price {scan_price:.4g} -> {cur_price:.4g} (-{drop * 100:.0f}%)")

    if scan_liq and cur_liq is not None:
        liq_drop = 1 - (cur_liq / scan_liq) if scan_liq > 0 else 0.0
        if liq_drop >= LIQUIDITY_REMOVED_PCT:
            rug_type = rug_type or "liquidity_pulled"
            evidence.append(f"liquidity ${scan_liq:.0f} -> ${cur_liq:.0f} (-{liq_drop * 100:.0f}%)")

    # Dev sold all — from stored dev reputation data (no extra API call)
    dev = scan.get("dev_reputation") or {}
    if isinstance(dev, dict):
        sold = _num(dev.get("sold_pct") or dev.get("dev_sold_pct"))
        if sold is not None and sold >= DEV_SOLD_ALL_PCT:
            rug_type = rug_type or "dev_exit"
            evidence.append(f"deployer sold {sold * 100:.0f}% of position")

    if rug_type is None:
        return None

    return {
        "rug_type": rug_type,
        "scan_result": scan,
        "evidence": evidence,
    }


# ── Scheduled sweep ──────────────────────────────────────────────────


async def run_rug_learning_sweep(max_tokens: int = 100) -> dict[str, int]:
    """Sweep tokens scanned in the last 7 days; embed newly confirmed rugs.

    Called by the lifespan background loop every 30 minutes. Deduplicates
    via a Redis marker so each token is learned at most once.
    """
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.scan import ScanResult as ScanResultORM

    stats = {"checked": 0, "confirmed": 0, "learned": 0, "skipped": 0}
    try:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        async with async_session_factory() as session:
            stmt = (
                select(ScanResultORM.chain, ScanResultORM.token_address)
                .where(ScanResultORM.scanned_at >= cutoff)
                .group_by(ScanResultORM.chain, ScanResultORM.token_address)
                .order_by(func.max(ScanResultORM.scanned_at).desc())
                .limit(max_tokens)
            )
            rows = (await session.execute(stmt)).all()
    except Exception as e:
        log.warning("rug_learning_sweep_query_failed err=%s", e)
        return stats

    from app.domains.scanner.engines._exceptions import get_redis_async

    r = get_redis_async()
    for chain, address in rows:
        stats["checked"] += 1
        dedup_key = f"rug_learned:{chain}:{address.lower()}"
        try:
            if r is not None and await r.exists(dedup_key):
                stats["skipped"] += 1
                continue
        except Exception:
            r = None

        confirmed = await check_rug_confirmed(chain, address)
        if not confirmed:
            continue
        stats["confirmed"] += 1
        ok = await record_confirmed_rug(
            scan_result=confirmed["scan_result"],
            chain=chain,
            address=address,
            rug_type=confirmed["rug_type"],
        )
        if ok:
            stats["learned"] += 1
            if r is not None:
                with contextlib.suppress(Exception):
                    await r.setex(dedup_key, 30 * 86400, "1")
    log.info("rug_learning_sweep_complete stats=%s", stats)
    return stats


__all__ = [
    "RUG_PATTERNS_COLLECTION",
    "build_fingerprint",
    "check_rug_confirmed",
    "find_similar_rugs",
    "record_confirmed_rug",
    "run_rug_learning_sweep",
]
