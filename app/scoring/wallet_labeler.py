"""
Wallet Labeler — Enhanced Wallet Labeling System
==================================================
Labels wallets with behavioral categories and stores profiles in Redis.
Shared between bot, website, and scanner via Redis.

Labels:
    exchange   — CEX hot wallets, known exchange addresses
    bot        — MEV bots, sniper bots, automated trading
    sniper     — Snipe-first-buy patterns
    whale      — Large holders (>1% supply or >$100K position)
    dev        — Token developers / team wallets
    rugger     — Known rug pull deployers
    mev        — MEV extractors
    fresh      — Wallet < 30 days old with few txns
    bundler    — Bundled supply wallets (coordinated buyers)
    mixer      — Tornado Cash / privacy mixer funded

Redis keys:
    wallet:labels:{chain}:{address}     — JSON array of labels
    wallet:profile:{chain}:{address}    — JSON wallet profile
    wallet:deployer:{chain}:{address}   — deployer-specific metadata
    deployer:known_scammers             — set of known scammer addresses
    deployer:known_good                 — set of known good actor addresses
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.logging import get_logger
from app.core.redis import get_redis_async

logger = get_logger(__name__)

LabelCategory = Literal[
    "exchange",
    "bot",
    "sniper",
    "whale",
    "dev",
    "rugger",
    "mev",
    "fresh",
    "bundler",
    "mixer",
    "unknown",
]

WALLET_LABEL_TTL = 60 * 86400  # 60 days


@dataclass
class WalletLabel:
    """A single wallet label entry."""

    category: LabelCategory
    confidence: float  # 0.0-1.0
    source: str  # e.g. "scanner", "manual", "dex_analysis", "onchain"
    first_seen: str = ""
    last_seen: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "source": self.source,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WalletLabel:
        return cls(
            category=data.get("category", "unknown"),
            confidence=data.get("confidence", 0.5),
            source=data.get("source", "unknown"),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
            evidence=data.get("evidence", {}),
        )


# ── Known entity data (in-code registry, extended via Redis at runtime) ──

# Well-known scammer addresses
_KNOWN_SCAMMERS: set[str] = set()

# Well-known good actors
_KNOWN_GOOD: set[str] = set()

# CEX wallet patterns (address prefixes / known addresses)
_CEX_PATTERNS: dict[str, str] = {
    "binance": "binance",
    "coinbase": "coinbase",
    "kraken": "kraken",
    "okx": "okx",
    "bybit": "bybit",
    "kucoin": "kucoin",
    "huobi": "huobi",
    "gate.io": "gateio",
    "mexc": "mexc",
    "bitfinex": "bitfinex",
    "crypto.com": "cryptocom",
    "bitget": "bitget",
    "upbit": "upbit",
    "bithumb": "bithumb",
    "gemini": "gemini",
}


async def _get_redis() -> Any | None:
    """Get async Redis client."""
    try:
        return get_redis_async()
    except Exception:
        return None


def _labels_key(chain: str, address: str) -> str:
    return f"wallet:labels:{chain}:{address}"


def _profile_key(chain: str, address: str) -> str:
    return f"wallet:profile:{chain}:{address}"


# ═══════════════════════════════════════════════════════════════════════════
# WALLET LABEL LOOKUP
# ═══════════════════════════════════════════════════════════════════════════


async def get_wallet_labels(chain: str, address: str) -> list[WalletLabel]:
    """Get all labels for a wallet address.

    Checks in order:
        1. Redis labels (authoritative)
        2. Known scammer / good lists
        3. CEX pattern matching
        4. OFAC sanctions
        5. CSV-based wallet labels
    """
    r = await _get_redis()
    labels: list[WalletLabel] = []
    now = datetime.now(UTC).isoformat()

    # ── 1. Redis labels ──
    if r:
        try:
            raw = await r.get(_labels_key(chain, address))
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    labels.extend(WalletLabel.from_dict(d) for d in data)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # ── 2. Known scammer / good lists ──
    addr_lower = address.lower()
    if addr_lower in _KNOWN_SCAMMERS:
        labels.append(
            WalletLabel(
                category="rugger",
                confidence=0.9,
                source="known_scammers_list",
                first_seen=now,
                last_seen=now,
            )
        )

    if addr_lower in _KNOWN_GOOD:
        labels.append(
            WalletLabel(
                category="dev",
                confidence=0.8,
                source="known_good_list",
                first_seen=now,
                last_seen=now,
            )
        )

    # ── 3. CEX pattern matching ──
    if r:
        try:
            cex_labels = await _check_cex_patterns(chain, address, r)
            labels.extend(cex_labels)
        except Exception:
            pass

    # ── 4. OFAC sanctions ──
    if r:
        try:
            ofac_raw = await r.get(f"rmi:label:ofac:{addr_lower}")
            if ofac_raw:
                labels.append(
                    WalletLabel(
                        category="rugger",
                        confidence=1.0,
                        source="ofac_sanctions",
                        first_seen=now,
                        last_seen=now,
                        evidence={"sanctions_data": json.loads(ofac_raw)},
                    )
                )
        except Exception:
            pass

    # ── 5. CSV wallet labels from wallet_label_loader ──
    if r:
        try:
            for ch in [chain, "solana", "ethereum", "bsc"]:
                label_raw = await r.get(f"rmi:label:{ch}:{address}")
                if label_raw:
                    label_data = json.loads(label_raw)
                    labels.append(
                        WalletLabel(
                            category=_map_csv_label_to_category(
                                label_data.get("label_type", ""),
                                label_data.get("tag", ""),
                            ),
                            confidence=0.85,
                            source=f"csv_labels:{ch}",
                            first_seen=now,
                            last_seen=now,
                            evidence=label_data,
                        )
                    )
                    break  # Found a label, stop searching
        except Exception:
            pass

    # ── Deduplicate by category ──
    seen = set()
    unique: list[WalletLabel] = []
    for lbl in labels:
        if lbl.category not in seen:
            seen.add(lbl.category)
            unique.append(lbl)
    return unique


async def _check_cex_patterns(chain: str, address: str, r: Any) -> list[WalletLabel]:
    """Check if address matches known CEX patterns."""
    labels: list[WalletLabel] = []
    addr_lower = address.lower()
    now = datetime.now(UTC).isoformat()

    # Check if address is in CEX label DB
    for ch in [chain, "solana", "ethereum", "bsc"]:
        cex_raw = await r.get(f"rmi:label:{ch}:{addr_lower}")
        if cex_raw:
            cex_data = json.loads(cex_raw)
            label_type = str(cex_data.get("label_type", "")).lower()
            name = str(cex_data.get("name", "")).lower()

            if label_type == "cex" or any(kw in name for kw in _CEX_PATTERNS):
                labels.append(
                    WalletLabel(
                        category="exchange",
                        confidence=0.9,
                        source=f"cex_labels:{ch}",
                        first_seen=now,
                        last_seen=now,
                        evidence={"exchange_name": name},
                    )
                )
            break

    return labels


def _map_csv_label_to_category(label_type: str, tag: str) -> LabelCategory:
    """Map CSV/DB label type to our category enum."""
    lt = label_type.lower()
    t = tag.lower()

    if "exchange" in lt or "cex" in lt or "exchange" in t:
        return "exchange"
    if "bot" in lt or "bot" in t or "mev" in lt:
        return "bot"
    if "sniper" in lt or "sniper" in t:
        return "sniper"
    if "whale" in lt or "whale" in t:
        return "whale"
    if "dev" in lt or "dev" in t or "deployer" in lt:
        return "dev"
    if "scam" in lt or "rug" in lt or "phish" in lt or "scam" in t:
        return "rugger"
    if "mixer" in lt or "tornado" in lt or "mixer" in t:
        return "mixer"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# WALLET LABEL / PROFILE STORAGE
# ═══════════════════════════════════════════════════════════════════════════


async def save_wallet_labels(chain: str, address: str, labels: list[WalletLabel]) -> bool:
    """Persist wallet labels to Redis."""
    r = await _get_redis()
    if r is None:
        return False

    try:
        data = [lbl.to_dict() for lbl in labels]
        await r.setex(_labels_key(chain, address), WALLET_LABEL_TTL, json.dumps(data))
        return True
    except Exception as exc:
        logger.warning("wallet_labels_save_failed", error=str(exc)[:100])
        return False


async def add_wallet_label(chain: str, address: str, label: WalletLabel) -> bool:
    """Add a label to an existing wallet's labels."""
    existing = await get_wallet_labels(chain, address)

    # Update or append
    found = False
    for i, lbl in enumerate(existing):
        if lbl.category == label.category:
            existing[i] = label
            found = True
            break

    if not found:
        existing.append(label)

    return await save_wallet_labels(chain, address, existing)


async def tag_as_scammer(chain: str, address: str, evidence: dict[str, Any] | None = None) -> bool:
    """Explicitly tag an address as a known scammer."""
    _KNOWN_SCAMMERS.add(address.lower())

    now = datetime.now(UTC).isoformat()
    label = WalletLabel(
        category="rugger",
        confidence=1.0,
        source="explicit_tag",
        first_seen=now,
        last_seen=now,
        evidence=evidence or {},
    )

    r = await _get_redis()
    if r:
        await r.sadd("deployer:known_scammers", address.lower())

    return await add_wallet_label(chain, address, label)


async def tag_as_good_actor(chain: str, address: str) -> bool:
    """Tag an address as a known good actor."""
    _KNOWN_GOOD.add(address.lower())

    now = datetime.now(UTC).isoformat()
    label = WalletLabel(
        category="dev",
        confidence=0.8,
        source="explicit_tag",
        first_seen=now,
        last_seen=now,
    )

    r = await _get_redis()
    if r:
        await r.sadd("deployer:known_good", address.lower())

    return await add_wallet_label(chain, address, label)


async def is_known_scammer(address: str) -> bool:
    """Check if an address is a known scammer."""
    addr_lower = address.lower()
    if addr_lower in _KNOWN_SCAMMERS:
        return True

    r = await _get_redis()
    if r:
        return bool(await r.sismember("deployer:known_scammers", addr_lower))
    return False


async def is_known_good(address: str) -> bool:
    """Check if an address is a known good actor."""
    addr_lower = address.lower()
    if addr_lower in _KNOWN_GOOD:
        return True

    r = await _get_redis()
    if r:
        return bool(await r.sismember("deployer:known_good", addr_lower))
    return False


# ═══════════════════════════════════════════════════════════════════════════
# DEPLOYER PROFILE UPDATE (called by token_rater + deployer_tracker)
# ═══════════════════════════════════════════════════════════════════════════


async def update_deployer_profile(
    chain: str,
    deployer_address: str,
    *,
    token_address: str | None = None,
    token_rugged: bool = False,
    risk_score: float | None = None,
    labels: list[str] | None = None,
) -> None:
    """
    Update deployer profile with scan results.
    This feeds both the deployer_tracker and the rating system.
    """
    # Check if scammer
    if risk_score is not None and risk_score > 80:
        await tag_as_scammer(
            chain,
            deployer_address,
            evidence={
                "risk_score": risk_score,
                "token_address": token_address,
                "source": "scanner_auto_label",
            },
        )
    elif risk_score is not None and risk_score < 20:
        await tag_as_good_actor(chain, deployer_address)

    # Assign behavioral labels
    now = datetime.now(UTC).isoformat()
    if labels:
        for lbl in labels:
            category: LabelCategory = "unknown"
            if "scammer" in lbl or "rug" in lbl:
                category = "rugger"
            elif "dev" in lbl or "deployer" in lbl or "factory" in lbl:
                category = "dev"
            elif "fresh" in lbl:
                category = "fresh"
            elif "whale" in lbl:
                category = "whale"

            if category != "unknown":
                await add_wallet_label(
                    chain,
                    deployer_address,
                    WalletLabel(
                        category=category,
                        confidence=0.7,
                        source="auto_labeler",
                        first_seen=now,
                        last_seen=now,
                        evidence={"label": lbl, "token": token_address},
                    ),
                )


# ── Module-level convenience ──


class WalletLabeler:
    """High-level wallet labeler."""

    async def get_labels(self, chain: str, address: str) -> list[WalletLabel]:
        return await get_wallet_labels(chain, address)

    async def get_deployer_labels(self, chain: str, address: str) -> dict[str, Any]:
        """Get deployer labels + profile combined."""
        labels = await get_wallet_labels(chain, address)

        # Also try deployer profile
        try:
            from app.scoring.deployer_tracker import get_deployer_profile

            profile = await get_deployer_profile(chain, address)
        except ImportError:
            profile = None

        return {
            "address": address,
            "chain": chain,
            "labels": [lbl.to_dict() for lbl in labels],
            "is_scammer": await is_known_scammer(address),
            "is_good_actor": await is_known_good(address),
            "deployer_profile": profile.to_dict() if profile else None,
            "queried_at": datetime.now(UTC).isoformat(),
        }
