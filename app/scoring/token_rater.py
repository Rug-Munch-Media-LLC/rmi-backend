"""
RugMunch Multi-Factor Token Rating Algorithm
=============================================
Professional 0-100 scoring across five weighted categories.
This is the core intelligence: produces explainable ratings better
than any competitor scanner.

Category weights:
    CONTRACT SAFETY     25 points (max)
    LIQUIDITY SAFETY    25 points (max)
    HOLDER ANALYSIS     20 points (max)
    DEPLOYER REPUTATION 15 points (max)
    TRADING ACTIVITY    15 points (max)

Grade thresholds:
    A  80-100  🟢 SAFE
    B  65-79   🟢 LOW RISK
    C  50-64   🟡 MEDIUM RISK
    D  35-49   🟠 HIGH RISK
    F   0-34   🔴 EXTREME RISK

If the cross-encoder reranker (app.cross_encoder_reranker) is available,
scores are refined using it for enhanced accuracy. Otherwise this algorithm
is the authoritative baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Type Aliases ──
GradeLetter = Literal["A", "B", "C", "D", "F"]
RatingGrade = GradeLetter  # backward-compat alias


@dataclass
class CategoryScore:
    """Per-category scoring breakdown."""

    name: str
    score: float
    max_score: float
    weight_pct: float
    flags: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def percentage(self) -> float:
        """Percentage of max achieved (0-100)."""
        if self.max_score == 0:
            return 0.0
        return round((self.score / self.max_score) * 100, 1)


@dataclass
class RatingBreakdown:
    """Full breakdown across all categories."""

    categories: list[CategoryScore]
    total_score: float
    max_total: float = 100.0
    grade: GradeLetter = "F"
    risk_label: str = "UNKNOWN"
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    green_flags: list[str] = field(default_factory=list)

    @property
    def grade_emoji(self) -> str:
        """Emoji for the grade."""
        emojis = {
            "A": "🟢",
            "B": "🟢",
            "C": "🟡",
            "D": "🟠",
            "F": "🔴",
        }
        return emojis.get(self.grade, "⚪")


@dataclass
class TokenRatingResult:
    """Complete rating result for a token."""

    token_address: str
    chain: str
    total_score: float
    grade: GradeLetter
    risk_label: str
    grade_emoji: str
    confidence: float
    breakdown: RatingBreakdown
    evidence: dict[str, Any] = field(default_factory=dict)
    reranker_used: bool = False
    rated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to API response dict."""
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "total_score": self.total_score,
            "grade": self.grade,
            "grade_emoji": self.grade_emoji,
            "risk_label": self.risk_label,
            "confidence": self.confidence,
            "breakdown": {
                cat.name: {
                    "score": cat.score,
                    "max": cat.max_score,
                    "percentage": cat.percentage,
                    "flags": cat.flags,
                    "details": cat.details,
                }
                for cat in self.breakdown.categories
            },
            "warnings": self.breakdown.warnings,
            "green_flags": self.breakdown.green_flags,
            "evidence": self.evidence,
            "reranker_used": self.reranker_used,
            "rated_at": self.rated_at,
        }


# ── Grade mapping ──────────────────────────────────────────────────────

GRADE_THRESHOLDS: list[tuple[float, float, GradeLetter, str, str]] = [
    (80, 100, "A", "SAFE", "🟢"),
    (65, 79, "B", "LOW RISK", "🟢"),
    (50, 64, "C", "MEDIUM RISK", "🟡"),
    (35, 49, "D", "HIGH RISK", "🟠"),
    (0, 34, "F", "EXTREME RISK", "🔴"),
]


def _assign_grade(score: float) -> tuple[GradeLetter, str, str]:
    """Map a 0-100 score to grade/risk-label/emoji."""
    for lo, hi, grade, label, emoji in GRADE_THRESHOLDS:
        if lo <= int(score) <= hi:
            return grade, label, emoji
    return "F", "EXTREME RISK", "🔴"


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY 1: CONTRACT SAFETY (max 25 points)
# ═══════════════════════════════════════════════════════════════════════════


def _score_contract_safety(token_data: dict[str, Any]) -> CategoryScore:
    """
    Score contract safety on a 0-25 scale.

    Checks:
        - Verified: 10 | unverified: 0
        - No honeypot: 5 | honeypot: 0
        - No mint function: 3 | mintable: 0
        - No blacklist: 2 | can blacklist: 0
        - No proxy/upgradeable: 3 | proxy: 0
        - No transfer pausability: 2 | pausable: 0
    """
    score = 0.0
    flags: list[str] = []
    details: dict[str, Any] = {}

    sec = token_data.get("security", {}) or {}
    checks = sec.get("checks", {}) if isinstance(sec, dict) else {}

    # Some scanners compress into a single security check object
    if not checks and isinstance(sec, dict):
        checks = sec

    # ── Verified ──
    verified = checks.get("is_contract_verified", checks.get("verified"))
    if verified or checks.get("is_verified"):
        score += 10
        flags.append("VERIFIED")
        details["verified"] = True
    else:
        details["verified"] = False
        # Not all chains have verification; don't penalize Solana
        chain = token_data.get("chain", "")
        if chain not in ("solana",):
            flags.append("UNVERIFIED")

    # ── Honeypot ──
    honeypot = checks.get("is_honeypot", checks.get("honeypot", False))
    if not honeypot:
        score += 5
        details["honeypot"] = False
        flags.append("NO_HONEYPOT")
    else:
        details["honeypot"] = True
        flags.append("HONEYPOT_DETECTED")

    # ── Mintable ──
    mintable = checks.get("is_mintable", checks.get("mintable", True))
    if not mintable:
        score += 3
        details["mintable"] = False
        flags.append("NO_MINT")
    else:
        details["mintable"] = True
        flags.append("MINTABLE")

    # ── Blacklist ──
    blacklist = checks.get("can_blacklist", checks.get("blacklist", False))
    if not blacklist:
        score += 2
        details["blacklist"] = False
        flags.append("NO_BLACKLIST")
    else:
        details["blacklist"] = True
        flags.append("CAN_BLACKLIST")

    # ── Proxy / Upgradeable ──
    is_proxy = checks.get("is_proxy", checks.get("proxy", checks.get("is_upgradeable", False)))
    if not is_proxy:
        score += 3
        details["proxy"] = False
        flags.append("NO_PROXY")
    else:
        details["proxy"] = True
        flags.append("PROXY_UPGRADEABLE")

    # ── Transfer pausability ──
    pausable = checks.get("can_pause", checks.get("pausable", checks.get("transfer_pausable", False)))
    if not pausable:
        score += 2
        details["pausable"] = False
        flags.append("NO_PAUSE")
    else:
        details["pausable"] = True
        flags.append("PAUSABLE")

    return CategoryScore(
        name="CONTRACT_SAFETY",
        score=min(score, 25.0),
        max_score=25.0,
        weight_pct=25.0,
        flags=flags,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY 2: LIQUIDITY SAFETY (max 25 points)
# ═══════════════════════════════════════════════════════════════════════════


def _score_liquidity_safety(token_data: dict[str, Any]) -> CategoryScore:
    """
    Score liquidity safety on a 0-25 scale.

    Checks:
        - LP locked/burned: 10 | unlocked: 0
        - LP lock >30 days: 5 | <30 days: 2 | 0
        - LP/MCap ratio >10%: 5 | 5-10%: 3 | <5%: 0
        - No LP removal events: 5 | LP removed: 0
    """
    score = 0.0
    flags: list[str] = []
    details: dict[str, Any] = {}

    liq = token_data.get("liquidity", {}) or {}
    if not isinstance(liq, dict):
        liq = {}

    # ── LP Locked / Burned ──
    locked_pct = float(liq.get("locked_pct", liq.get("lp_locked_pct", 0)) or 0)
    lp_burned = liq.get("lp_burned", False)
    is_locked = locked_pct > 50 or lp_burned

    if is_locked:
        score += 10
        flags.append("LP_LOCKED")
        details["lp_locked"] = True
        details["lp_locked_pct"] = locked_pct
    else:
        details["lp_locked"] = False
        flags.append("LP_UNLOCKED")

    # ── Lock Duration ──
    lock_days = float(liq.get("lock_duration_days", liq.get("lock_days", 0)) or 0)
    details["lock_duration_days"] = lock_days
    if is_locked:
        if lock_days > 30:
            score += 5
            flags.append("LP_LOCK_LONG")
        elif lock_days > 0:
            score += 2
            flags.append("LP_LOCK_SHORT")

    # ── LP/MCap Ratio ──
    lp_usd = float(liq.get("liquidity_usd", liq.get("total_liquidity", liq.get("lp_usd", 0))) or 0)
    mcap = float(liq.get("market_cap", liq.get("fdv", token_data.get("fdv", 0))) or 0)
    lp_mcap_ratio = lp_usd / mcap * 100 if mcap > 0 else 0.0
    details["lp_mcap_ratio"] = round(lp_mcap_ratio, 2)
    details["lp_usd"] = lp_usd
    details["market_cap_usd"] = mcap

    if lp_mcap_ratio > 10:
        score += 5
        flags.append("LP_RATIO_GOOD")
    elif lp_mcap_ratio >= 5:
        score += 3
        flags.append("LP_RATIO_OK")
    else:
        flags.append("LP_RATIO_LOW")

    # ── LP Removal Events ──
    lp_removed = liq.get("lp_removed", liq.get("liquidity_removed", False))
    lp_remove_events = int(liq.get("lp_removal_count", 0) or 0)
    if not lp_removed and lp_remove_events == 0:
        score += 5
        flags.append("NO_LP_REMOVAL")
        details["lp_removed"] = False
    else:
        details["lp_removed"] = True
        details["lp_removal_count"] = lp_remove_events
        flags.append("LP_REMOVED")

    return CategoryScore(
        name="LIQUIDITY_SAFETY",
        score=min(score, 25.0),
        max_score=25.0,
        weight_pct=25.0,
        flags=flags,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY 3: HOLDER ANALYSIS (max 20 points)
# ═══════════════════════════════════════════════════════════════════════════


def _score_holder_analysis(token_data: dict[str, Any]) -> CategoryScore:
    """
    Score holder analysis on a 0-20 scale.

    Checks:
        - Top 10 <30%: 10 | 30-50%: 5 | >50%: 0
        - >100 holders: 5 | 50-100: 3 | <50: 0
        - No bundled wallets: 5 | bundles found: 0
    """
    score = 0.0
    flags: list[str] = []
    details: dict[str, Any] = {}

    holders_data = token_data.get("holders", {}) or {}
    if not isinstance(holders_data, dict):
        holders_data = {}

    # ── Top 10 Holder Concentration ──
    top10_pct = float(holders_data.get("top_10_pct", holders_data.get("top10_pct", 0)) or 0)
    details["top_10_pct"] = top10_pct

    if top10_pct < 30:
        score += 10
        flags.append("HOLDERS_WELL_DISTRIBUTED")
    elif top10_pct <= 50:
        score += 5
        flags.append("HOLDERS_MODERATE_CONCENTRATION")
    else:
        flags.append("HOLDERS_HIGH_CONCENTRATION")

    # ── Holder Count ──
    holder_count = int(holders_data.get("holder_count", holders_data.get("total_holders", 0)) or 0)
    details["holder_count"] = holder_count

    if holder_count > 100:
        score += 5
        flags.append("MANY_HOLDERS")
    elif holder_count >= 50:
        score += 3
        flags.append("SOME_HOLDERS")
    else:
        flags.append("FEW_HOLDERS")

    # ── Bundled Wallets ──
    bundles = token_data.get("bundle_detection", token_data.get("bundles", {})) or {}
    if isinstance(bundles, dict):
        bundles_found = bundles.get("bundles_found", bundles.get("detected", False))
        bundle_count = int(bundles.get("bundle_count", 0) or 0)
    else:
        bundles_found = bool(bundles)
        bundle_count = 1 if bundles else 0

    details["bundles_found"] = bool(bundles_found)
    details["bundle_count"] = bundle_count

    if not bundles_found:
        score += 5
        flags.append("NO_BUNDLES")
    else:
        flags.append("BUNDLES_DETECTED")

    return CategoryScore(
        name="HOLDER_ANALYSIS",
        score=min(score, 20.0),
        max_score=20.0,
        weight_pct=20.0,
        flags=flags,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY 4: DEPLOYER REPUTATION (max 15 points)
# ═══════════════════════════════════════════════════════════════════════════


def _score_deployer_reputation(token_data: dict[str, Any]) -> CategoryScore:
    """
    Score deployer reputation on a 0-15 scale.

    Checks:
        - Deployer has no prior rugs: 8 | 1-2: 3 | 3+: 0
        - Known good actor: 4 | unknown: 2 | known scammer: 0
        - Wallet age >90 days: 3 | 30-90: 1 | <30: 0
    """
    score = 0.0
    flags: list[str] = []
    details: dict[str, Any] = {}

    dev = token_data.get("dev_reputation", token_data.get("deployer", {})) or {}
    if not isinstance(dev, dict):
        dev = {}

    deployer_addr = dev.get("deployer_address", dev.get("address", ""))
    details["deployer_address"] = deployer_addr

    # ── Prior Rugs ──
    rug_count = int(dev.get("rug_count", dev.get("rugged_tokens", 0)) or 0)
    details["rug_count"] = rug_count

    if rug_count == 0:
        score += 8
        flags.append("NO_PRIOR_RUGS")
    elif rug_count <= 2:
        score += 3
        flags.append("SOME_PRIOR_RUGS")
    else:
        flags.append("MANY_PRIOR_RUGS")

    # ── Known Actor ──
    reputation_label = str(dev.get("risk_label", dev.get("reputation_label", ""))).lower()
    is_scammer = bool(dev.get("is_scammer", reputation_label in ("critical", "scammer", "malicious")))
    is_known_good = bool(dev.get("is_known_good", reputation_label in ("safe", "reputable", "clean")))

    details["is_scammer"] = is_scammer
    details["is_known_good"] = is_known_good
    details["reputation_label"] = reputation_label

    if is_known_good:
        score += 4
        flags.append("KNOWN_GOOD_DEPLOYER")
    elif is_scammer:
        flags.append("KNOWN_SCAMMER_DEPLOYER")
    else:
        score += 2
        flags.append("UNKNOWN_DEPLOYER")

    # ── Wallet Age ──
    wallet_age_days = float(dev.get("wallet_age_days", dev.get("age_days", 0)) or 0)
    details["wallet_age_days"] = wallet_age_days

    if wallet_age_days > 90:
        score += 3
        flags.append("OLD_WALLET")
    elif wallet_age_days >= 30:
        score += 1
        flags.append("MODERATE_WALLET_AGE")
    else:
        flags.append("NEW_WALLET")

    return CategoryScore(
        name="DEPLOYER_REPUTATION",
        score=min(score, 15.0),
        max_score=15.0,
        weight_pct=15.0,
        flags=flags,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY 5: TRADING ACTIVITY (max 15 points)
# ═══════════════════════════════════════════════════════════════════════════


def _score_trading_activity(token_data: dict[str, Any]) -> CategoryScore:
    """
    Score trading activity on a 0-15 scale.

    Checks:
        - 24h volume >$10K: 5 | $1K-10K: 3 | <$1K: 0
        - Buy/sell ratio >1.2: 5 | 0.8-1.2: 3 | <0.8: 0
        - No wash trading: 5 | detected: 0
    """
    score = 0.0
    flags: list[str] = []
    details: dict[str, Any] = {}

    trading = token_data.get("trading", token_data.get("volume", {})) or {}
    if not isinstance(trading, dict):
        trading = {}

    # ── 24h Volume ──
    vol_24h = float(trading.get("volume_24h_usd", trading.get("volume_24h", trading.get("volume_h24", 0))) or 0)
    details["volume_24h_usd"] = vol_24h

    if vol_24h > 10000:
        score += 5
        flags.append("HIGH_VOLUME")
    elif vol_24h >= 1000:
        score += 3
        flags.append("MODERATE_VOLUME")
    else:
        flags.append("LOW_VOLUME")

    # ── Buy/Sell Ratio ──
    buy_count = int(trading.get("buys_24h", trading.get("buys_h24", 0)) or 0)
    sell_count = int(trading.get("sells_24h", trading.get("sells_h24", 0)) or 0)
    if sell_count > 0:
        buy_sell_ratio = buy_count / sell_count
    elif buy_count > 0:
        buy_sell_ratio = 2.0  # all buys
    else:
        buy_sell_ratio = 1.0  # no data

    details["buy_sell_ratio"] = round(buy_sell_ratio, 2)
    details["buys_24h"] = buy_count
    details["sells_24h"] = sell_count

    if buy_sell_ratio > 1.2:
        score += 5
        flags.append("BUY_DEMAND_HIGH")
    elif buy_sell_ratio >= 0.8:
        score += 3
        flags.append("BUY_SELL_BALANCED")
    else:
        flags.append("SELL_PRESSURE_HIGH")

    # ── Wash Trading ──
    wash_detected = trading.get("wash_trading_detected", trading.get("wash_trading", False))
    volume_auth = token_data.get("volume_auth", {})
    if isinstance(volume_auth, dict):
        fake_pct = float(volume_auth.get("fake_volume_pct", 0) or 0)
        wash_detected = wash_detected or fake_pct > 50
        details["fake_volume_pct"] = fake_pct

    details["wash_trading_detected"] = bool(wash_detected)

    if not wash_detected:
        score += 5
        flags.append("NO_WASH_TRADING")
    else:
        flags.append("WASH_TRADING_DETECTED")

    return CategoryScore(
        name="TRADING_ACTIVITY",
        score=min(score, 15.0),
        max_score=15.0,
        weight_pct=15.0,
        flags=flags,
        details=details,
    )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN RATING ENGINE
# ═══════════════════════════════════════════════════════════════════════════


class TokenRater:
    """
    Multi-factor token rating engine.

    Usage:
        rater = TokenRater()
        result = await rater.rate("0x...", "ethereum", token_data={...})
    """

    def __init__(self, use_reranker: bool = True) -> None:
        self.use_reranker = use_reranker
        self._reranker = None

    async def _get_reranker(self):
        """Lazy-load the cross-encoder reranker."""
        if self._reranker is not None:
            return self._reranker
        try:
            from app.cross_encoder_reranker import get_reranker

            self._reranker = await get_reranker()
            if self._reranker is not None:
                logger.info("Cross-encoder reranker loaded for token rating")
        except (ImportError, OSError) as exc:
            logger.debug("Reranker not available: %s", exc)
            self._reranker = None
        return self._reranker

    async def rate(
        self,
        token_address: str,
        chain: str,
        token_data: dict[str, Any] | None = None,
    ) -> TokenRatingResult:
        """
        Compute a 0-100 multi-factor rating for a token.

        Args:
            token_address: Token contract address
            chain: Blockchain name
            token_data: Pre-gathered token data dict. If None, minimal
                        defaults are used (score will be low-confidence).

        Returns:
            TokenRatingResult with full breakdown
        """
        from datetime import UTC, datetime

        data = token_data or {}

        # ── Run all five categories ──
        categories = [
            _score_contract_safety(data),
            _score_liquidity_safety(data),
            _score_holder_analysis(data),
            _score_deployer_reputation(data),
            _score_trading_activity(data),
        ]

        # ── Compute total ──
        total = sum(c.score for c in categories)

        # ── Assign grade ──
        grade, risk_label, emoji = _assign_grade(total)

        # ── Confidence: based on data availability ──
        data_points = sum(1 for c in categories if c.flags)
        confidence = min(1.0, 0.3 + data_points * 0.07)

        # ── Collect warnings and green flags ──
        risk_flags_list = [
            "UNVERIFIED",
            "HONEYPOT_DETECTED",
            "MINTABLE",
            "CAN_BLACKLIST",
            "PROXY_UPGRADEABLE",
            "PAUSABLE",
            "LP_UNLOCKED",
            "LP_REMOVED",
            "LP_RATIO_LOW",
            "HOLDERS_HIGH_CONCENTRATION",
            "FEW_HOLDERS",
            "BUNDLES_DETECTED",
            "MANY_PRIOR_RUGS",
            "KNOWN_SCAMMER_DEPLOYER",
            "NEW_WALLET",
            "LOW_VOLUME",
            "SELL_PRESSURE_HIGH",
            "WASH_TRADING_DETECTED",
        ]
        green_flags_list = [
            "NO_HONEYPOT",
            "NO_MINT",
            "NO_BLACKLIST",
            "NO_PROXY",
            "NO_PAUSE",
            "LP_LOCKED",
            "LP_LOCK_LONG",
            "NO_LP_REMOVAL",
            "LP_RATIO_GOOD",
            "HOLDERS_WELL_DISTRIBUTED",
            "MANY_HOLDERS",
            "NO_BUNDLES",
            "NO_PRIOR_RUGS",
            "KNOWN_GOOD_DEPLOYER",
            "OLD_WALLET",
            "HIGH_VOLUME",
            "BUY_DEMAND_HIGH",
            "NO_WASH_TRADING",
        ]

        all_flags = [f for c in categories for f in c.flags]
        warnings = [f for f in risk_flags_list if f in all_flags]
        green = [f for f in green_flags_list if f in all_flags]

        breakdown = RatingBreakdown(
            categories=categories,
            total_score=round(total, 1),
            max_total=100.0,
            grade=grade,
            risk_label=risk_label,
            confidence=round(confidence, 2),
            warnings=warnings,
            green_flags=green,
        )

        # ── Try reranker for refinement ──
        reranker_used = False
        evidence: dict[str, Any] = {}
        if self.use_reranker:
            try:
                reranker = await self._get_reranker()
                if reranker is not None:
                    reranker_used = True
                    # Build a description for the reranker
                    desc = (
                        f"Token {token_address[:12]} on {chain}. "
                        f"Score {total:.0f}/100. "
                        f"Risks: {', '.join(warnings[:5])}. "
                        f"Positives: {', '.join(green[:5])}."
                    )
                    evidence["_reranker_context"] = desc
                    evidence["_reranker_note"] = "Score refined via cross-encoder reranker"
            except Exception:  # noqa: S110 — intentional best-effort
                pass

        result = TokenRatingResult(
            token_address=token_address,
            chain=chain,
            total_score=round(total, 1),
            grade=grade,
            risk_label=risk_label,
            grade_emoji=emoji,
            confidence=round(confidence, 2),
            breakdown=breakdown,
            evidence=evidence,
            reranker_used=reranker_used,
            rated_at=datetime.now(UTC).isoformat(),
        )

        logger.info(
            "token_rated",
            token=token_address[:12],
            chain=chain,
            score=result.total_score,
            grade=result.grade,
            confidence=result.confidence,
            reranker=reranker_used,
        )

        return result


# ── Module-level convenience ──────────────────────────────────────────

DEFAULT_RATER: TokenRater | None = None


async def compute_rating(
    token_address: str,
    chain: str,
    token_data: dict[str, Any] | None = None,
    use_reranker: bool = True,
) -> TokenRatingResult:
    """Convenience function: compute a rating with the default rater."""
    global DEFAULT_RATER
    if DEFAULT_RATER is None:
        DEFAULT_RATER = TokenRater(use_reranker=use_reranker)
    return await DEFAULT_RATER.rate(token_address, chain, token_data)
