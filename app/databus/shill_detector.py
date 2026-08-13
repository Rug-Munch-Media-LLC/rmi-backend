"""KOL Shill Detector — on-chain-verified shill pattern detection.

Given a KOL's bullish post and their attributed wallets (``kol_wallets``)
plus wallet transaction history (``kol_wallet_tx``), flags shill patterns:

1. **shill_then_dump (CRITICAL)** — bullish post, then an attributed wallet
   sells the same token 1-72h later (especially when the wallet bought the
   token ≤7d before posting).
2. **buy_then_sell (HIGH)** — front-run: wallet buys the token within ±24h of
   the post (before = classic front-run, after = shill-then-buy), then sells
   ≤7d later.
3. **coordinated (HIGH/CRITICAL)** — ≥3 KOLs shill the same token in a <2h
   window (≥5 KOLs → CRITICAL). Mirrors ``social_intel.detect_shill_campaigns``
   but operates on arbitrary post input (deterministic, testable).
4. **known_scammer (CRITICAL)** — the KOL is in ``known_scammers.json``.

All rule functions are pure and deterministic (pass ``now`` for tests).
Thresholds are config-driven via env vars so false positives can be tuned
without code deploys (see ``SHILL_*`` / ``FRONT_RUN_*`` / ``COORDINATED_*``).

Guardrails (from docs/KOL-TRACKING-DESIGN.md §5):
- Sells below ``MIN_SELL_USD`` are ignored.
- Wallet confidence < ``strong_heuristic`` requires 2 corroborating sells
  before CRITICAL.
- ``known_scammers.json`` is optional — absence yields an empty list and the
  detector still works (explicit lists may be passed for tests/callers).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("shill_detector")

# ═══════════════════════════════════════════════════════════════════════
# Config (env-driven, tunable without code deploys)
# ═══════════════════════════════════════════════════════════════════════

_SHILL_SELL_WINDOW_H = float(os.getenv("SHILL_SELL_WINDOW_H", "72"))  # sell window after post (h)
_MIN_SELL_USD = float(os.getenv("MIN_SELL_USD", "100"))  # ignore sells below this USD
_SHILL_PRE_BUY_WINDOW_D = float(os.getenv("SHILL_PRE_BUY_WINDOW_D", "7"))  # bought ≤7d before post
_FRONT_RUN_WINDOW_H = float(os.getenv("FRONT_RUN_WINDOW_H", "24"))  # buy ±24h around post
_FRONT_RUN_SELL_WINDOW_D = float(os.getenv("FRONT_RUN_SELL_WINDOW_D", "7"))  # sell ≤7d after buy
_COORDINATED_WINDOW_H = float(os.getenv("COORDINATED_WINDOW_H", "2"))  # <2h cluster
_COORDINATED_MIN_KOLS = int(os.getenv("COORDINATED_MIN_KOLS", "3"))  # ≥3 KOLs
_KNOWN_SCAMMERS_PATH = os.getenv("KNOWN_SCAMMERS_PATH", os.path.join(os.path.dirname(__file__), "known_scammers.json"))

# Confidence tiers, weakest → strongest (matches label store 4-tier).
_WEAK_CONFIDENCE: frozenset[str] = frozenset({"probabilistic", "crowdsourced_unverified", ""})

# Severity ordering for caps.
_SEVERITY_RANK: dict[str, int] = {"critical": 3, "high": 2, "medium": 1}

_SEVERITIES: set[str] = set(_SEVERITY_RANK)


# ═══════════════════════════════════════════════════════════════════════
# Small helpers
# ═══════════════════════════════════════════════════════════════════════


def _to_ts(value: Any) -> float:
    """Parse a timestamp into epoch seconds; 0.0 when unparseable.

    Accepts int/float epoch (seconds; milliseconds when > 1e11) and ISO-8601
    strings (with optional trailing Z).
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if value > 1e11:  # unix milliseconds
            return value / 1000.0
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit() and len(s) >= 9:
            try:
                return float(s)
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
    logger.debug("Unparseable timestamp %r, using 0", value)
    return 0.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _normalize_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _normalize_address(addr: Any) -> str:
    """Normalize an address for matching (EVM case-insensitive, base58 exact)."""
    s = str(addr or "").strip()
    return s.lower() if s.startswith("0x") else s


def _text_of(post: dict[str, Any]) -> str:
    for key in ("text", "content", "description", "title", "summary"):
        val = post.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _post_author(post: dict[str, Any]) -> str:
    for key in ("author", "username", "handle", "source", "kol_id", "screen_name"):
        val = post.get(key)
        if isinstance(val, str) and val.strip():
            return _normalize_handle(val)
    return "unknown"


def _post_ts(post: dict[str, Any]) -> float:
    return _to_ts(post.get("ts") or post.get("timestamp") or post.get("published") or post.get("posted_at"))


def _post_is_bullish(post: dict[str, Any]) -> bool:
    """Bullish if explicit sentiment says so, or call_type is buy/shill."""
    sentiment = str(post.get("sentiment") or "").lower()
    if sentiment in ("bullish", "buy", "shill", "long", "alpha"):
        return True
    call_type = str(post.get("call_type") or "").lower()
    if call_type in ("buy", "shill"):
        return True
    # Lightweight keyword fallback for when no classifier ran.
    text = _text_of(post).lower()
    return any(k in text for k in ("buy", "long", "gem", "alpha", "moon", "so early", "undervalued"))


def _post_token(post: dict[str, Any]) -> dict[str, str]:
    """Extract (symbol, address, chain) from a post. Address wins for matching."""
    symbol = str(post.get("token_symbol") or post.get("token") or "").strip().lstrip("$#").upper()
    address = str(post.get("token_address") or post.get("ca") or "").strip()
    chain = str(post.get("token_chain") or post.get("chain") or "").strip().lower()
    if not address:
        # Best-effort: grab the first cashtag / contract-ish token from text.
        for marker in ("$", "#"):
            idx = _text_of(post).find(marker)
            if idx != -1:
                symbol = symbol or _text_of(post)[idx + 1 :].split()[0].rstrip(".,;:!?")[:32].upper()
                break
    return {"symbol": symbol, "address": _normalize_address(address), "chain": chain}


def _token_matches(tx: dict[str, Any], token: dict[str, str]) -> bool:
    """Match a tx against a post's token by address (preferred) or symbol."""
    tx_addr = _normalize_address(tx.get("token_address") or "")
    if token["address"] and tx_addr:
        return tx_addr == token["address"]
    tx_sym = str(tx.get("token_symbol") or "").strip().lstrip("$#").upper()
    return bool(token["symbol"] and tx_sym and tx_sym == token["symbol"])


def _tx_amount_usd(tx: dict[str, Any]) -> float:
    try:
        return float(tx.get("amount_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _tx_hash(tx: dict[str, Any]) -> str:
    return str(tx.get("tx_hash") or tx.get("hash") or tx.get("id") or "")


# ═══════════════════════════════════════════════════════════════════════
# Known scammers
# ═══════════════════════════════════════════════════════════════════════


def load_known_scammers(path: str | None = None) -> set[str]:
    """Load known-scammer handles/addresses from JSON.

    The JSON may be a list of strings, or a dict of ``{"handles": [...],
    "addresses": [...], "entries": [{"handle": ..., "reason": ...}]}``.
    Returns an empty set when the file is absent (never raises).
    """
    path = path or _KNOWN_SCAMMERS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        logger.debug("known_scammers file %s unavailable; continuing with empty set", path)
        return set()
    result: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                result.add(_normalize_handle(item))
            elif isinstance(item, dict):
                result.add(_normalize_handle(item.get("handle") or item.get("address")))
    elif isinstance(raw, dict):
        for key in ("handles", "addresses"):
            for item in raw.get(key, []) or []:
                result.add(_normalize_handle(item))
        for item in raw.get("entries", []) or []:
            if isinstance(item, dict):
                result.add(_normalize_handle(item.get("handle") or item.get("address")))
    return {h for h in result if h}


# ═══════════════════════════════════════════════════════════════════════
# Wallet helpers
# ═══════════════════════════════════════════════════════════════════════


def _active_wallet_addresses(kol_wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return active (non-revoked) wallet rows with normalized address."""
    result: list[dict[str, Any]] = []
    for w in kol_wallets or []:
        status = str(w.get("status") or "active").lower()
        if status in ("revoked", "unverified"):
            continue
        address = _normalize_address(w.get("address") or "")
        if not address:
            continue
        result.append(
            {
                "address": address,
                "chain": str(w.get("chain") or "").lower(),
                "confidence": str(w.get("confidence") or "").lower(),
                "attribution_method": str(w.get("attribution_method") or ""),
                "evidence": str(w.get("evidence") or ""),
            }
        )
    return result


def _wallet_confidence_weak(wallet: dict[str, Any]) -> bool:
    return wallet["confidence"] in _WEAK_CONFIDENCE


def _wallet_txs_for(wallet_address: str, wallet_txs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter wallet txs down to a specific wallet address."""
    addr = _normalize_address(wallet_address)
    return [tx for tx in wallet_txs or [] if _normalize_address(tx.get("wallet") or "") == addr]


# ═══════════════════════════════════════════════════════════════════════
# Alert record builder
# ═══════════════════════════════════════════════════════════════════════


def _alert_id(kol_id: str, token: dict[str, str], pattern: str) -> str:
    key = f"{kol_id}|{token['address'] or token['symbol']}|{pattern}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _build_alert(
    *,
    kol_id: str,
    token: dict[str, str],
    pattern: str,
    severity: str,
    evidence: list[dict[str, Any]],
    signal_ids: list[str],
    wallet_tx_ids: list[str],
    sell_after_post_24h: float = 0.0,
    buy_after_post: bool = False,
    created_at: float,
) -> dict[str, Any]:
    """Assemble a ``shill_alert``-shaped record (matches the design-doc table)."""
    severity = severity if severity in _SEVERITIES else "high"
    return {
        "id": _alert_id(kol_id, token, pattern),
        "kol_id": kol_id,
        "token_symbol": token["symbol"],
        "token_address": token["address"],
        "token_chain": token["chain"],
        "pattern": pattern,
        "severity": severity,
        "signal_ids": signal_ids,
        "wallet_tx_ids": wallet_tx_ids,
        "buy_after_post": buy_after_post,
        "sell_after_post_24h": round(sell_after_post_24h, 2),
        "status": "open",
        "evidence": evidence,
        "created_at": _iso(created_at),
    }


def _cap_severity(current: str, incoming: str) -> str:
    """Severity caps at critical."""
    return current if _SEVERITY_RANK[current] >= _SEVERITY_RANK[incoming] else incoming


# ═══════════════════════════════════════════════════════════════════════
# Core rules
# ═══════════════════════════════════════════════════════════════════════


def detect_shill(
    post: dict[str, Any],
    kol_wallets: list[dict[str, Any]],
    wallet_txs: list[dict[str, Any]],
    known_scammers: set[str] | list[str] | None = None,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Detect shill patterns for a single bullish KOL post.

    Args:
        post: normalized post dict with ``sentiment``/``call_type``,
            ``token_address``/``token_symbol``, ``timestamp`` and author key.
        kol_wallets: rows from the ``kol_wallet`` table for this KOL
            (``address``, ``chain``, ``confidence``, ``status``...).
        wallet_txs: rows from ``kol_wallet_tx`` for the attributed wallets
            (``wallet``, ``tx_type`` buy|sell, ``token_address``,
            ``token_symbol``, ``amount_usd``, ``ts``).
        known_scammers: optional extra handle/address set to auto-flag.
        now: epoch seconds for deterministic windows (defaults to now).

    Returns a list of ``shill_alert`` records (usually 0..2 per post).
    """
    ts = now if now is not None else datetime.now(UTC).timestamp()
    post_ts = _post_ts(post)
    if post_ts <= 0:
        logger.debug("skipping post with unparseable timestamp")
        return []

    kol_id = _post_author(post)
    token = _post_token(post)
    if not token["symbol"] and not token["address"]:
        return []
    if not _post_is_bullish(post):
        return []

    known = set(known_scammers) if known_scammers else load_known_scammers()
    wallets = _active_wallet_addresses(kol_wallets)
    alerts: list[dict[str, Any]] = []

    # ── Pattern 4: known scammer (auto-flag, no on-chain evidence needed) ──
    if kol_id in known or any(w["address"] in known for w in wallets):
        alerts.append(
            _build_alert(
                kol_id=kol_id,
                token=token,
                pattern="known_scammer",
                severity="critical",
                evidence=[{"kind": "known_scammer", "detail": "KOL or attributed wallet in known_scammers"}],
                signal_ids=[str(post.get("id") or "")],
                wallet_tx_ids=[],
                created_at=ts,
            )
        )
        return alerts  # known bad actor — stop here

    sell_window_h = max(1.0, _SHILL_SELL_WINDOW_H)
    pre_buy_window_d = max(1.0, _SHILL_PRE_BUY_WINDOW_D)
    front_run_h = max(1.0, _FRONT_RUN_WINDOW_H)
    front_run_sell_d = max(1.0, _FRONT_RUN_SELL_WINDOW_D)
    min_sell_usd = max(0.0, _MIN_SELL_USD)

    for wallet in wallets:
        txs = _wallet_txs_for(wallet["address"], wallet_txs)
        if not txs:
            continue

        sells_after: list[dict[str, Any]] = []
        sells_24h: list[dict[str, Any]] = []
        buys_before_7d: list[dict[str, Any]] = []
        front_run_buys: list[dict[str, Any]] = []

        for tx in txs:
            if not _token_matches(tx, token):
                continue
            tx_ts = _to_ts(tx.get("ts") or tx.get("timestamp"))
            if tx_ts <= 0:
                continue
            tx_type = str(tx.get("tx_type") or tx.get("type") or "").lower()

            if tx_type == "sell":
                delta_h = (tx_ts - post_ts) / 3600.0
                if 1.0 <= delta_h <= sell_window_h:
                    sells_after.append(tx)
                    if delta_h <= 24.0:
                        sells_24h.append(tx)
            elif tx_type == "buy":
                delta_h = (post_ts - tx_ts) / 3600.0
                if 0 <= delta_h <= pre_buy_window_d * 24.0:
                    buys_before_7d.append(tx)
                # Front-run candidates: bought within ±front_run_h of the post.
                if -front_run_h <= delta_h <= front_run_h:
                    front_run_buys.append(tx)

        # ── Pattern 1: shill_then_dump (CRITICAL when bought ≤7d pre-post) ──
        if sells_after:
            sell_total = sum(_tx_amount_usd(t) for t in sells_after)
            if sell_total >= min_sell_usd:
                bought_pre = len(buys_before_7d) > 0
                weak_conf = _wallet_confidence_weak(wallet)
                # Guardrail: weak attribution needs 2 corroborating sells for CRITICAL.
                corroborated = len(sells_after) >= 2 or not weak_conf
                severity = "critical" if bought_pre and corroborated else "high"
                evidence = [
                    {
                        "kind": "sell_after_post",
                        "wallet": wallet["address"],
                        "confidence": wallet["confidence"],
                        "sell_usd": round(sell_total, 2),
                        "sell_count": len(sells_after),
                        "bought_before_post_7d": bought_pre,
                        "detail": f"sold {len(sells_after)} tx(s) {sell_total:.0f} USD within {sell_window_h:g}h of bullish post",
                    }
                ]
                if weak_conf and not corroborated:
                    evidence.append(
                        {
                            "kind": "guardrail",
                            "detail": "wallet confidence below strong_heuristic; capped at high pending 2nd sell",
                        }
                    )
                alerts.append(
                    _build_alert(
                        kol_id=kol_id,
                        token=token,
                        pattern="shill_then_dump",
                        severity=severity,
                        evidence=evidence,
                        signal_ids=[str(post.get("id") or "")],
                        wallet_tx_ids=[_tx_hash(t) for t in sells_after if _tx_hash(t)],
                        sell_after_post_24h=sum(_tx_amount_usd(t) for t in sells_24h),
                        created_at=ts,
                    )
                )

        # ── Pattern 2: buy_then_sell front-run (HIGH) ──
        for buy in front_run_buys:
            buy_ts = _to_ts(buy.get("ts") or buy.get("timestamp"))
            sell_after_buy = [
                t
                for t in txs
                if _token_matches(t, token)
                and str(t.get("tx_type") or t.get("type") or "").lower() == "sell"
                and 0 < _to_ts(t.get("ts") or t.get("timestamp")) - buy_ts <= front_run_sell_d * 24.0 * 3600.0
            ]
            if not sell_after_buy:
                continue
            sell_total = sum(_tx_amount_usd(t) for t in sell_after_buy)
            if sell_total < min_sell_usd:
                continue
            bought_after_post = buy_ts > post_ts  # design doc: bought within 24h after post
            evidence = [
                {
                    "kind": "buy_then_sell",
                    "wallet": wallet["address"],
                    "confidence": wallet["confidence"],
                    "buy_ts": _iso(buy_ts),
                    "sell_usd": round(sell_total, 2),
                    "bought_after_post": bought_after_post,
                    "detail": "front-run: buy within ±24h of bullish post, sell within 7d of buy",
                }
            ]
            alerts.append(
                _build_alert(
                    kol_id=kol_id,
                    token=token,
                    pattern="buy_then_sell",
                    severity="high",
                    evidence=evidence,
                    signal_ids=[str(post.get("id") or "")],
                    wallet_tx_ids=[_tx_hash(buy), *(_tx_hash(t) for t in sell_after_buy if _tx_hash(t))],
                    sell_after_post_24h=sum(
                        _tx_amount_usd(t) for t in sells_24h if _to_ts(t.get("ts") or t.get("timestamp")) >= buy_ts
                    ),
                    buy_after_post=bought_after_post,
                    created_at=ts,
                )
            )

    return alerts


def detect_coordinated_shills(
    posts_by_kol: dict[str, list[dict[str, Any]]],
    *,
    now: float | None = None,
    extra_campaigns: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Detect coordinated shills: ≥3 KOLs bullish on the same token in <2h.

    Mirrors ``social_intel.detect_shill_campaigns`` (same thresholds and
    severity ladder: ≥5 distinct KOLs → CRITICAL) but operates on arbitrary
    post input instead of the in-memory ``KOL_DATABASE``, so it is
    deterministic and testable. Callers that already ran
    ``social_intel.detect_shill_campaigns`` may pass its campaign dicts via
    ``extra_campaigns``; those are normalized into alert records too.
    """
    ts = now if now is not None else datetime.now(UTC).timestamp()
    window_h = max(0.5, _COORDINATED_WINDOW_H)
    min_kols = max(2, _COORDINATED_MIN_KOLS)
    alerts: list[dict[str, Any]] = []

    # Cluster bullish posts per token.
    token_posts: dict[str, list[dict[str, Any]]] = {}
    for kol_id, posts in (posts_by_kol or {}).items():
        for post in posts or []:
            if not _post_is_bullish(post):
                continue
            token = _post_token(post)
            key = token["address"] or token["symbol"]
            if not key:
                continue
            token_posts.setdefault(key, []).append({**post, "kol_id": kol_id, "_token": token})

    for _key, posts in token_posts.items():
        token = posts[0]["_token"]
        timestamps = sorted(_post_ts(p) for p in posts if _post_ts(p) > 0)
        if len(timestamps) < min_kols:
            continue
        span_h = (timestamps[-1] - timestamps[0]) / 3600.0
        if span_h >= window_h:
            continue
        kols = sorted({str(p.get("kol_id") or _post_author(p)) for p in posts})
        if len(kols) < min_kols:
            continue
        alerts.append(
            _build_alert(
                kol_id=",".join(kols),
                token=token,
                pattern="coordinated",
                severity="critical" if len(kols) >= 5 else "high",
                evidence=[
                    {
                        "kind": "coordinated",
                        "kols_involved": kols,
                        "post_count": len(posts),
                        "time_window_h": round(span_h, 1),
                        "detail": f"{len(kols)} KOLs bullish on same token within {span_h:.1f}h",
                    }
                ],
                signal_ids=[str(p.get("id") or "") for p in posts if p.get("id")],
                wallet_tx_ids=[],
                created_at=ts,
            )
        )

    # Bridge: normalize already-detected campaigns from social_intel, if any.
    for camp in extra_campaigns or []:
        if str(camp.get("type") or "") != "coordinated_shill":
            continue
        token = {"symbol": str(camp.get("token") or "").upper(), "address": "", "chain": ""}
        kols = list(camp.get("kols_involved") or [])
        alerts.append(
            _build_alert(
                kol_id=",".join(str(k) for k in kols),
                token=token,
                pattern="coordinated",
                severity=str(camp.get("severity") or "high").lower(),
                evidence=[{"kind": "coordinated", "source": "social_intel.detect_shill_campaigns", **camp}],
                signal_ids=[],
                wallet_tx_ids=[],
                created_at=ts,
            )
        )
    return alerts


# ═══════════════════════════════════════════════════════════════════════
# Orchestration + dedupe
# ═══════════════════════════════════════════════════════════════════════


def dedupe_alerts(alerts: list[dict[str, Any]], seen_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Dedupe alerts by (kol, token, pattern) id — 7d rolling window key.

    Highest severity wins when the same id re-appears.
    """
    seen: set[str] = set(seen_ids or set())
    best: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        aid = str(alert.get("id") or "")
        if aid in seen:
            continue
        existing = best.get(aid)
        if existing is None or _SEVERITY_RANK.get(str(alert.get("severity")), 0) > _SEVERITY_RANK.get(
            str(existing.get("severity")), 0
        ):
            best[aid] = alert
    return list(best.values())


def analyze_shill_signals(
    posts_by_kol: dict[str, list[dict[str, Any]]],
    kol_wallets_by_kol: dict[str, list[dict[str, Any]]],
    wallet_txs_by_wallet: dict[str, list[dict[str, Any]]],
    known_scammers: set[str] | list[str] | None = None,
    *,
    now: float | None = None,
    include_coordinated: bool = True,
) -> dict[str, Any]:
    """Run the full detector over many KOLs and return deduped alerts.

    Args:
        posts_by_kol: handle → list of post dicts (bullish posts become
            candidates; non-bullish are skipped).
        kol_wallets_by_kol: handle → ``kol_wallet`` rows.
        wallet_txs_by_wallet: wallet address → ``kol_wallet_tx`` rows
            (only wallets attributed to the KOLs are queried by the caller).
        known_scammers: optional override; None → load from file.

    Returns a summary dict with ``alerts`` (deduped), per-pattern counts and
    severity totals — shaped for the ``/shill-alerts`` feed.
    """
    ts = now if now is not None else datetime.now(UTC).timestamp()
    known = set(known_scammers) if known_scammers else load_known_scammers()

    alerts: list[dict[str, Any]] = []
    for kol_id, posts in (posts_by_kol or {}).items():
        wallets = kol_wallets_by_kol.get(kol_id, [])
        # Flatten txs for all of this KOL's wallets (caller may pass per-wallet map).
        wallet_addresses = [str(w.get("address") or "") for w in wallets]
        flat_txs: list[dict[str, Any]] = []
        for w in wallet_addresses:
            flat_txs.extend(wallet_txs_by_wallet.get(w, []) or [])
        for post in posts or []:
            alerts.extend(
                detect_shill(
                    post,
                    wallets,
                    flat_txs,
                    known,
                    now=ts,
                )
            )

    if include_coordinated:
        alerts.extend(detect_coordinated_shills(posts_by_kol, now=ts))

    deduped = dedupe_alerts(alerts)

    pattern_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for a in deduped:
        pattern_counts[a["pattern"]] = pattern_counts.get(a["pattern"], 0) + 1
        severity_counts[a["severity"]] = severity_counts.get(a["severity"], 0) + 1

    return {
        "alerts": deduped,
        "total_alerts": len(deduped),
        "patterns": pattern_counts,
        "severity": severity_counts,
        "critical": severity_counts.get("critical", 0),
        "high": severity_counts.get("high", 0),
        "medium": severity_counts.get("medium", 0),
        "generated_at": _iso(ts),
        "source": "shill_detector",
    }
