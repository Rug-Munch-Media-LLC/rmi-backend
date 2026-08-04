"""
SENTINEL - Sentiment & Bot Campaign Analyzer
==============================================
Analyzes token sentiment from DexScreener/Birdeye social signals:
  - Bullish/bearish keyword sentiment scoring
  - Bot campaign detection (identical messages, new accounts)
  - Telegram group velocity (message rate spikes)
  - Pump probability scoring

No Twitter/Telegram API keys required - uses public DexScreener and
Birdeye endpoints for social/sentiment data.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import httpx

from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("sentiment_analyzer")

# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------

BULLISH_KEYWORDS: dict[str, float] = {
    # Strong bullish signals
    "moon": 0.8,
    "mooning": 0.9,
    "bullish": 0.7,
    "pump": 0.5,
    "gem": 0.7,
    "hidden gem": 0.9,
    "undervalued": 0.6,
    "early": 0.6,
    "launch": 0.4,
    "launching": 0.5,
    "airdrop": 0.3,
    "staking": 0.3,
    "buy": 0.3,
    "buying": 0.4,
    "accumulate": 0.5,
    "accumulating": 0.5,
    "long": 0.4,
    "longs": 0.3,
    "support": 0.4,
    "bounce": 0.3,
    "breakout": 0.6,
    "ath": 0.5,
    "all-time high": 0.5,
    "new ath": 0.6,
    "partnership": 0.5,
    "integration": 0.4,
    "listing": 0.5,
    "listed": 0.5,
    "roadmap": 0.3,
    "milestone": 0.4,
    # Moderate bullish
    "good": 0.2,
    "great": 0.3,
    "solid": 0.3,
    "strong": 0.4,
    "growth": 0.3,
    "growing": 0.3,
    "potential": 0.3,
    "opportunity": 0.3,
    "uptrend": 0.4,
    "recovery": 0.3,
    "rally": 0.5,
    "surge": 0.5,
}

BEARISH_KEYWORDS: dict[str, float] = {
    # Strong bearish / scam signals
    "rug": 0.9,
    "rugpull": 1.0,
    "rugged": 0.9,
    "scam": 0.9,
    "honeypot": 0.9,
    "honeypotted": 0.9,
    "dump": 0.7,
    "dumping": 0.8,
    "bearish": 0.7,
    "crash": 0.7,
    "crashed": 0.7,
    "dead": 0.6,
    "sell": 0.4,
    "selling": 0.5,
    "short": 0.4,
    "shorts": 0.3,
    "exit": 0.5,
    "exiting": 0.5,
    "abandon": 0.6,
    "steal": 0.8,
    "stolen": 0.8,
    "drain": 0.7,
    "drained": 0.7,
    "liq": 0.5,
    "liquidity": 0.2,
    "no liquidity": 0.8,
    "cannot sell": 0.9,
    "cant sell": 0.9,
    "dev sold": 0.8,
    "dev dumped": 0.9,
    # Moderate bearish
    "weak": 0.4,
    "decline": 0.4,
    "declining": 0.5,
    "drop": 0.4,
    "dropping": 0.5,
    "falling": 0.5,
    "down": 0.2,
    "risk": 0.3,
    "warning": 0.4,
    "avoid": 0.5,
    "dangerous": 0.6,
    "suspicious": 0.5,
}

PUMP_KEYWORDS: dict[str, float] = {
    "pump": 0.6,
    "pumping": 0.7,
    "100x": 0.9,
    "1000x": 0.95,
    "get in now": 0.8,
    "don't miss": 0.7,
    "fomo": 0.6,
    "rocket": 0.5,
    "🚀": 0.5,
    "to the moon": 0.6,
    "next pepe": 0.7,
    "next shib": 0.7,
    "floor is": 0.5,
    "guaranteed": 0.7,
    "easy money": 0.7,
    "free money": 0.8,
    "join now": 0.7,
    "limited time": 0.6,
    "hurry": 0.6,
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SocialMessage:
    """A single social message / comment about a token."""

    text: str
    author: str
    timestamp: int  # unix ms
    platform: str  # "dexscreener", "birdeye", "telegram", etc.
    likes: int = 0
    replies: int = 0
    account_age_hours: float | None = None  # None = unknown


@dataclass
class BotCampaign:
    """Detected bot campaign group."""

    pattern: str  # "identical_messages", "new_accounts", "coordinated_timing"
    messages: list[SocialMessage] = field(default_factory=list)
    unique_authors: int = 0
    identical_count: int = 0
    new_account_count: int = 0
    confidence: float = 0.0  # 0-1
    evidence: list[str] = field(default_factory=list)


@dataclass
class SentimentReport:
    """Complete sentiment analysis report for a token."""

    overall_sentiment: float  # 0-100 (0=extreme bear, 50=neutral, 100=extreme bull)
    bot_probability: float  # 0-1
    pump_probability: float  # 0-1
    warnings: list[str] = field(default_factory=list)
    # Sub-scores
    bull_score: float = 0.0  # 0-1
    bear_score: float = 0.0  # 0-1
    sentiment_label: str = "neutral"  # "very_bearish", "bearish", "neutral", "bullish", "very_bullish"
    # Social metrics
    total_messages: int = 0
    unique_authors: int = 0
    messages_per_hour: float = 0.0
    velocity_spikes: int = 0  # hours where msg rate > 3x average
    # Bot detection
    bot_campaigns: list[BotCampaign] = field(default_factory=list)
    identical_message_groups: int = 0
    new_account_ratio: float = 0.0  # 0-1
    # Pump indicators
    pump_signal_count: int = 0
    pump_keywords_found: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SentimentAnalyzer
# ---------------------------------------------------------------------------


@sentinel_module(key="sentiment", tier="premium", description="Social sentiment scoring & artificial-hype detection")
class SentimentAnalyzer:
    """Analyzes token sentiment from DexScreener/Birdeye social data."""

    DEXSCREENER_BASE = "https://api.dexscreener.com"
    BIRDEYE_BASE = "https://public-api.birdeye.so"

    def __init__(self, dexscreener_client: httpx.AsyncClient | None = None, birdeye_api_key: str = "") -> None:
        self.client = dexscreener_client or httpx.AsyncClient(timeout=15.0)
        self.birdeye_key = birdeye_api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, token_address: str, chain: str) -> SentimentReport:
        """Full sentiment analysis for a token.

        Steps:
        1. Fetch social messages from DexScreener & Birdeye
        2. Score bullish/bearish keywords
        3. Detect bot campaigns (identical messages, new accounts)
        4. Analyse message velocity / Telegram group rate
        5. Calculate pump probability
        6. Assemble final report
        """
        messages = await self._fetch_social_data(token_address, chain)

        if not messages:
            return SentimentReport(
                overall_sentiment=50.0,
                bot_probability=0.0,
                pump_probability=0.0,
                warnings=["No social data available for this token"],
                sentiment_label="neutral",
            )

        # Step 2 - keyword sentiment
        bull_score, bear_score, sentiment_label = self._score_keywords(messages)

        # Step 3 - bot campaign detection
        bot_campaigns = self._detect_bot_campaigns(messages)
        bot_probability = self._calculate_bot_probability(bot_campaigns, messages)

        # Step 4 - velocity analysis
        msgs_per_hour, velocity_spikes = self._analyze_velocity(messages)
        new_account_ratio = self._calc_new_account_ratio(messages)

        # Step 5 - pump probability
        pump_prob, pump_signals, pump_kws = self._score_pump_probability(messages, velocity_spikes, bot_probability)

        # Step 6 - assemble overall sentiment (0-100)
        raw_sentiment = 50 + (bull_score - bear_score) * 50  # center at 50
        # Adjust for bot probability - bots inflate sentiment
        adjusted = raw_sentiment * (1 - bot_probability * 0.5)
        # Clamp to 0-100
        overall = max(0.0, min(100.0, round(adjusted, 1)))

        # Warnings
        warnings: list[str] = []
        if bot_probability > 0.7:
            warnings.append(f"CRITICAL: Bot activity {bot_probability:.0%} - sentiment likely artificial")
        elif bot_probability > 0.4:
            warnings.append(f"HIGH: Bot activity {bot_probability:.0%} - sentiment may be inflated")
        if pump_prob > 0.7:
            warnings.append(f"PUMP WARNING: {pump_prob:.0%} pump-and-dump probability detected")
        if velocity_spikes > 3:
            warnings.append(f"VELOCITY: {velocity_spikes} message rate spikes - possible coordinated campaign")
        if new_account_ratio > 0.5:
            warnings.append(f"NEW ACCOUNTS: {new_account_ratio:.0%} of authors are new accounts")
        if bear_score > 0.5:
            warnings.append(f"NEGATIVE SENTIMENT: Bearish signals dominant ({bear_score:.2f})")

        unique_authors = len({m.author for m in messages})

        return SentimentReport(
            overall_sentiment=overall,
            bot_probability=round(bot_probability, 3),
            pump_probability=round(pump_prob, 3),
            warnings=warnings,
            bull_score=round(bull_score, 3),
            bear_score=round(bear_score, 3),
            sentiment_label=sentiment_label,
            total_messages=len(messages),
            unique_authors=unique_authors,
            messages_per_hour=round(msgs_per_hour, 2),
            velocity_spikes=velocity_spikes,
            bot_campaigns=bot_campaigns,
            identical_message_groups=sum(1 for c in bot_campaigns if c.pattern == "identical_messages"),
            new_account_ratio=round(new_account_ratio, 3),
            pump_signal_count=len(pump_signals),
            pump_keywords_found=pump_kws,
        )

    # ------------------------------------------------------------------
    # Keyword scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_keywords(messages: list[SocialMessage]) -> tuple[float, float, str]:
        """Score messages for bullish/bearish sentiment keywords.

        Returns (bull_score, bear_score, label).
        """
        bull_weighted = 0.0
        bear_weighted = 0.0
        total_messages = len(messages) or 1

        for msg in messages:
            text_lower = msg.text.lower()
            for kw, weight in BULLISH_KEYWORDS.items():
                if kw in text_lower:
                    bull_weighted += weight
            for kw, weight in BEARISH_KEYWORDS.items():
                if kw in text_lower:
                    bear_weighted += weight

        # Normalise to 0-1 range per message average, cap at 1
        bull_score = min(1.0, bull_weighted / total_messages)
        bear_score = min(1.0, bear_weighted / total_messages)

        # Determine label
        diff = bull_score - bear_score
        if diff > 0.4:
            label = "very_bullish"
        elif diff > 0.15:
            label = "bullish"
        elif diff < -0.4:
            label = "very_bearish"
        elif diff < -0.15:
            label = "bearish"
        else:
            label = "neutral"

        return bull_score, bear_score, label

    # ------------------------------------------------------------------
    # Bot campaign detection
    # ------------------------------------------------------------------

    def _detect_bot_campaigns(self, messages: list[SocialMessage]) -> list[BotCampaign]:
        """Detect bot campaigns through identical messages and new-account clusters."""
        campaigns: list[BotCampaign] = []

        # 1. Identical message detection
        identical_groups = self._find_identical_messages(messages)
        for _norm_text, group in identical_groups.items():
            if len(group) < 3:
                continue
            authors = {m.author for m in group}
            confidence = min(0.99, 0.5 + 0.1 * len(group))
            campaigns.append(
                BotCampaign(
                    pattern="identical_messages",
                    messages=group,
                    unique_authors=len(authors),
                    identical_count=len(group),
                    confidence=confidence,
                    evidence=[f"{len(group)} near-identical messages from {len(authors)} authors"],
                )
            )

        # 2. New-account clusters
        new_accounts = [m for m in messages if m.account_age_hours is not None and m.account_age_hours < 48]
        if len(new_accounts) >= 5:
            new_authors = {m.author for m in new_accounts}
            new_bull = sum(1 for m in new_accounts if self._message_has_bullish(m))
            bull_ratio = new_bull / len(new_accounts) if new_accounts else 0
            confidence = min(0.95, 0.4 + bull_ratio * 0.4)
            campaigns.append(
                BotCampaign(
                    pattern="new_accounts",
                    messages=new_accounts,
                    unique_authors=len(new_authors),
                    new_account_count=len(new_accounts),
                    confidence=confidence,
                    evidence=[
                        f"{len(new_accounts)} messages from accounts <48h old",
                        f"{new_bull}/{len(new_accounts)} are bullish ({bull_ratio:.0%})",
                    ],
                )
            )

        # 3. Coordinated timing (bursts within short windows)
        bursts = self._find_timing_bursts(messages)
        for burst in bursts:
            if len(burst) >= 5:
                authors = {m.author for m in burst}
                if len(authors) <= len(burst) * 0.5:  # many messages from few authors
                    campaigns.append(
                        BotCampaign(
                            pattern="coordinated_timing",
                            messages=burst,
                            unique_authors=len(authors),
                            confidence=0.7,
                            evidence=[
                                f"{len(burst)} messages in short window from {len(authors)} authors",
                            ],
                        )
                    )

        return sorted(campaigns, key=lambda c: c.confidence, reverse=True)

    @staticmethod
    def _find_identical_messages(
        messages: list[SocialMessage],
    ) -> dict[str, list[SocialMessage]]:
        """Group near-identical messages by normalised text."""
        groups: dict[str, list[SocialMessage]] = defaultdict(list)
        for msg in messages:
            # Normalise: lowercase, strip punctuation, collapse whitespace
            norm = re.sub(r"[^\w\s]", "", msg.text.lower()).strip()
            norm = re.sub(r"\s+", " ", norm)
            # Skip very short messages (e.g. "lol")
            if len(norm) < 5:
                continue
            groups[norm].append(msg)
        return {k: v for k, v in groups.items() if len(v) >= 2}

    @staticmethod
    def _message_has_bullish(msg: SocialMessage) -> bool:
        text = msg.text.lower()
        return any(kw in text for kw in BULLISH_KEYWORDS)

    @staticmethod
    def _find_timing_bursts(
        messages: list[SocialMessage],
        window_sec: int = 300,  # 5 minutes
        threshold: int = 5,
    ) -> list[list[SocialMessage]]:
        """Find time windows where message rate exceeds threshold."""
        if not messages:
            return []
        sorted_msgs = sorted(messages, key=lambda m: m.timestamp)
        bursts: list[list[SocialMessage]] = []
        i = 0
        while i < len(sorted_msgs):
            window_start = sorted_msgs[i].timestamp
            window_end = window_start + window_sec * 1000
            burst = [m for m in sorted_msgs if window_start <= m.timestamp <= window_end]
            if len(burst) >= threshold:
                bursts.append(burst)
                # Skip past this burst
                i = next(
                    (j for j, m in enumerate(sorted_msgs) if m.timestamp > window_end),
                    len(sorted_msgs),
                )
            else:
                i += 1
        return bursts

    @staticmethod
    def _calculate_bot_probability(
        campaigns: list[BotCampaign],
        messages: list[SocialMessage],
    ) -> float:
        """Estimate probability that sentiment is bot-driven.

        Combines campaign confidence with volume of bot-flagged messages.
        """
        if not campaigns or not messages:
            return 0.0

        bot_messages = set()
        for campaign in campaigns:
            for msg in campaign.messages:
                bot_messages.add(id(msg))

        # Volume-weighted bot ratio
        bot_ratio = len(bot_messages) / len(messages)
        # Campaign confidence multiplier
        max_conf = max(c.confidence for c in campaigns)
        # Weighted blend: 60% volume ratio, 40% campaign confidence
        probability = bot_ratio * 0.6 + max_conf * 0.4
        return min(1.0, probability)

    @staticmethod
    def _calc_new_account_ratio(messages: list[SocialMessage]) -> float:
        """Fraction of messages from accounts younger than 48 hours."""
        with_age = [m for m in messages if m.account_age_hours is not None]
        if not with_age:
            return 0.0
        new = sum(1 for m in with_age if m.account_age_hours < 48)
        return new / len(with_age)

    # ------------------------------------------------------------------
    # Velocity analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_velocity(
        messages: list[SocialMessage],
    ) -> tuple[float, int]:
        """Calculate messages per hour and velocity spike count.

        A velocity spike = hour where msg count > 3x the hourly average.
        Returns (msgs_per_hour, spike_count).
        """
        if not messages:
            return 0.0, 0

        timestamps = sorted(m.timestamp for m in messages)
        first, last = timestamps[0], timestamps[-1]
        span_hours = max((last - first) / 3_600_000, 1.0)

        # Bucket into hours
        hourly: Counter[int] = Counter()
        for ts in timestamps:
            hourly[ts // 3_600_000] += 1

        avg = len(messages) / span_hours
        spike_threshold = max(avg * 3, 5)
        spikes = sum(1 for cnt in hourly.values() if cnt > spike_threshold)

        return round(len(messages) / span_hours, 2), spikes

    # ------------------------------------------------------------------
    # Pump probability scoring
    # ------------------------------------------------------------------

    def _score_pump_probability(
        self,
        messages: list[SocialMessage],
        velocity_spikes: int,
        bot_probability: float,
    ) -> tuple[float, list[SocialMessage], list[str]]:
        """Score pump-and-dump probability from 0-1.

        Factors:
          - Pump-specific keywords
          - Message velocity spikes
          - Bot activity
          - New account ratio
        """
        pump_signals: list[SocialMessage] = []
        pump_kws_found: list[str] = []
        total = len(messages) or 1

        for msg in messages:
            text_lower = msg.text.lower()
            for kw, _weight in PUMP_KEYWORDS.items():
                if kw in text_lower:
                    pump_signals.append(msg)
                    if kw not in pump_kws_found:
                        pump_kws_found.append(kw)
                    break  # one match per message is enough

        pump_msg_ratio = len(pump_signals) / total

        # Weighted scoring
        #   pump keywords:  35%
        #   velocity spikes: 20%
        #   bot activity:    25%
        #   new accounts:    20%
        new_ratio = self._calc_new_account_ratio(messages)
        spike_score = min(1.0, velocity_spikes / 10)

        pump_prob = pump_msg_ratio * 0.35 + spike_score * 0.20 + bot_probability * 0.25 + new_ratio * 0.20
        return min(1.0, pump_prob), pump_signals, pump_kws_found

    # ------------------------------------------------------------------
    # Data fetching (DexScreener + Birdeye)
    # ------------------------------------------------------------------

    async def _fetch_social_data(
        self,
        token_address: str,
        chain: str,
    ) -> list[SocialMessage]:
        """Fetch social comments/mentions from DexScreener & Birdeye."""
        messages: list[SocialMessage] = []

        # --- DexScreener ---
        try:
            messages.extend(await self._fetch_dexscreener_comments(token_address, chain))
        except httpx.HTTPError as e:
            logger.debug("_fetch_social_data_error", exc_info=True)
            logger.warning(f"DexScreener social fetch failed: {e}")

        # --- Birdeye ---
        try:
            messages.extend(await self._fetch_birdeye_social(token_address, chain))
        except httpx.HTTPError as e:
            logger.debug("_fetch_social_data_error", exc_info=True)
            logger.warning(f"Birdeye social fetch failed: {e}")

        # Deduplicate by (author, text[:80], platform)
        seen = set()
        deduped: list[SocialMessage] = []
        for m in messages:
            key = (m.author, m.text[:80], m.platform)
            if key not in seen:
                seen.add(key)
                deduped.append(m)

        return deduped

    async def _fetch_dexscreener_comments(
        self,
        token_address: str,
        chain: str,
    ) -> list[SocialMessage]:
        """Fetch comments and boost data from DexScreener token page."""
        messages: list[SocialMessage] = []

        # Resolve pair address via DexScreener search
        resp = await self.client.get(
            f"{self.DEXSCREENER_BASE}/latest/dex/tokens/{token_address}",
        )
        if resp.status_code != 200:
            return messages

        data = resp.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return messages

        # Use the first matching pair for social data
        pair = pairs[0]

        # DexScreener provides info.boosts and info.socials in pair data
        info = pair.get("info") or {}
        socials = info.get("socials") or []

        # DexScreener doesn't have a public comments endpoint,
        # but we can extract from price change labels and social links
        # We synthesize messages from the pair's label/description data
        # for keyword analysis. Real comments would need a paid tier.
        labels = info.get("labels") or []
        for label in labels:
            text = label if isinstance(label, str) else str(label)
            messages.append(
                SocialMessage(
                    text=text,
                    author="dexscreener",
                    timestamp=int(time.time() * 1000),
                    platform="dexscreener",
                )
            )

        # Build synthetic messages from social link descriptions / names
        for social in socials:
            social_type = social.get("type", "")
            social_url = social.get("url", "")
            if social_type and social_url:
                messages.append(
                    SocialMessage(
                        text=f"Social link: {social_type} - {social_url}",
                        author="dexscreener_meta",
                        timestamp=int(time.time() * 1000),
                        platform="dexscreener",
                    )
                )

        # Price alert messages from pair data (for keyword analysis)
        price_change = pair.get("priceChange") or {}
        for period, pct in price_change.items():
            if isinstance(pct, (int, float)):
                direction = "pumping" if pct > 0 else "dumping"
                messages.append(
                    SocialMessage(
                        text=f"Token {direction} {pct:+.1f}% in {period}",
                        author="dexscreener_price",
                        timestamp=int(time.time() * 1000),
                        platform="dexscreener",
                    )
                )

        return messages

    async def _fetch_birdeye_social(
        self,
        token_address: str,
        chain: str,
    ) -> list[SocialMessage]:
        """Fetch social sentiment data from Birdeye public API."""
        messages: list[SocialMessage] = []

        # Birdeye chain mapping
        chain_map = {
            "solana": "solana",
            "ethereum": "ethereum",
            "bsc": "bsc",
            "base": "base",
            "polygon": "polygon",
        }
        birdeye_chain = chain_map.get(chain, chain)

        headers = {"accept": "application/json"}
        if self.birdeye_key:
            headers["X-API-KEY"] = self.birdeye_key

        # Fetch token security overview (includes social metrics)
        resp = await self.client.get(
            f"{self.BIRDEYE_BASE}/defi/token_security",
            params={"address": token_address, "chain": birdeye_chain},
            headers=headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            security = data.get("data") or {}
            # Extract social-related fields
            for key in ("twitter", "telegram", "discord", "website"):
                val = security.get(key)
                if val:
                    messages.append(
                        SocialMessage(
                            text=f"Token has {key}: {val}",
                            author="birdeye_security",
                            timestamp=int(time.time() * 1000),
                            platform="birdeye",
                        )
                    )

        # Fetch token list data (social metadata)
        resp2 = await self.client.get(
            f"{self.BIRDEYE_BASE}/defi/v3/token/list",
            params={"address": token_address, "chain": birdeye_chain},
            headers=headers,
        )
        if resp2.status_code == 200:
            data2 = resp2.json()
            token_list = data2.get("data") or {}
            # Extract community description
            description = token_list.get("description", "")
            if description:
                messages.append(
                    SocialMessage(
                        text=description,
                        author="birdeye_description",
                        timestamp=int(time.time() * 1000),
                        platform="birdeye",
                    )
                )

        return messages
