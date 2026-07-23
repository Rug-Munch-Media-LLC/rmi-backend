"""
SENTINEL - Social Sentiment Velocity Analysis
==============================================
Tracks the speed of the first 50 mentions to distinguish between
organic retail engagement and coordinated bot farm activity.

Key insight: Bot farms drop all mentions in a single second.
Authentic retail engagement grows organically over minutes.

Detection heuristics:
  - Inter-arrival time distribution (tight clustering = bots)
  - Near-simultaneous mention clustering (< 1s apart)
  - Time-to-N-mentions: if >25 in <3s → CRITICAL bot farm
  - Time-to-N-mentions: if first 50 spread over 5+ minutes → organic

Input: list of mention dicts
  [{timestamp: str|int|float, platform: str, text: str}, ...]
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.core.logging import get_logger

logger = get_logger("social_velocity")


def _parse_timestamp(ts) -> float:
    """Parse a timestamp value into epoch seconds (float).

    Accepts:
      - ISO 8601 strings: "2025-01-15T12:34:56Z", "2025-01-15T12:34:56.123+00:00"
      - Unix seconds (int or float)
      - Unix milliseconds (int > 1e11)
    """
    if isinstance(ts, (int, float)):
        # Heuristic: unix ms ( > year 5138 in seconds = ~1.62e11)
        if ts > 1e11:
            return ts / 1000.0
        return float(ts)
    if isinstance(ts, str):
        try:
            # Try ISO 8601 with timezone
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
        try:
            # Fallback: parse as float
            return float(ts)
        except (ValueError, TypeError):
            pass
    logger.warning(f"Unparseable timestamp {ts!r}, using 0")
    return 0.0


@dataclass
class VelocityTimelinePoint:
    timestamp: float  # epoch seconds
    cumulative_count: int


@dataclass
class SocialVelocityReport:
    velocity_score: float = 0.0  # 0-100, higher = more bot-like
    bot_farm_confidence: float = 0.0  # 0-1
    mention_timeline: list[dict] = field(default_factory=list)  # [{timestamp, cumulative_count}]
    mention_sources: dict[str, int] = field(default_factory=dict)
    risk_label: str = "UNKNOWN"  # ORGANIC, SUSPICIOUS, CRITICAL
    total_mentions_analyzed: int = 0
    largest_cluster_size: int = 0
    largest_cluster_duration_sec: float = 0.0
    first_50_spread_sec: float = 0.0
    median_interarrival_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)


class SocialVelocityAnalyzer:
    """Analyzes social mention velocity to detect bot farms and coordinated campaigns.

    Examines the timing of the first 50 mentions. Bot farms deposit all mentions
    nearly simultaneously (<1s apart), while organic growth spreads over minutes.
    """

    # Max mentions to analyze
    MAX_MENTIONS = 50

    # Cluster threshold: mentions arriving within this many seconds are
    # considered simultaneous (bot-like)
    CLUSTER_WINDOW_SEC = 1.0

    # Bot farm threshold: if >N mentions arrive within a rolling window of
    # BOT_FARM_WINDOW_SEC seconds, it's almost certainly a bot farm
    BOT_FARM_THRESHOLD = 25
    BOT_FARM_WINDOW_SEC = 3.0

    # Organic threshold: if the first 50 mentions are spread over this many
    # seconds (or more), it's likely organic
    ORGANIC_SPREAD_SEC = 300.0  # 5 minutes

    def __init__(self) -> None:
        pass

    async def analyze(self, mentions: list) -> SocialVelocityReport:
        """Analyze the velocity of social mentions.

        Args:
            mentions: list of dicts, each with at minimum:
                - timestamp: str | int | float (ISO 8601 or unix epoch)
                - platform: str (e.g. "twitter", "telegram", "discord")
                - text: str

        Returns:
            SocialVelocityReport with velocity scoring and risk assessment.
        """
        if not mentions:
            return SocialVelocityReport(
                velocity_score=0.0,
                bot_farm_confidence=0.0,
                mention_timeline=[],
                mention_sources={},
                risk_label="UNKNOWN",
                total_mentions_analyzed=0,
                warnings=["No mentions provided"],
            )

        # ── Step 1: Parse and sort mentions ──────────────────────────────
        parsed = []
        for m in mentions:
            ts_sec = _parse_timestamp(m.get("timestamp", 0))
            platform = str(m.get("platform", "unknown"))
            text = str(m.get("text", ""))
            parsed.append(
                {
                    "timestamp": ts_sec,
                    "platform": platform,
                    "text": text,
                }
            )

        # Sort by timestamp ascending
        parsed.sort(key=lambda x: x["timestamp"])

        # Limit to first MAX_MENTIONS
        first_n = parsed[: self.MAX_MENTIONS]
        total = len(first_n)

        if total == 0:
            return SocialVelocityReport(
                velocity_score=0.0,
                bot_farm_confidence=0.0,
                mention_timeline=[],
                mention_sources={},
                risk_label="UNKNOWN",
                total_mentions_analyzed=0,
                warnings=["No valid timestamps after parsing"],
            )

        # ── Step 2: Count sources ────────────────────────────────────────
        mention_sources: dict[str, int] = defaultdict(int)
        for m in first_n:
            mention_sources[m["platform"]] += 1

        # ── Step 3: Calculate inter-arrival times ────────────────────────
        timestamps = [m["timestamp"] for m in first_n]
        interarrivals = []
        for i in range(1, len(timestamps)):
            interarrivals.append(timestamps[i] - timestamps[i - 1])

        # Median inter-arrival time in milliseconds
        sorted_inter = sorted(interarrivals) if interarrivals else [0.0]
        n_inter = len(sorted_inter)
        if n_inter == 0:
            median_ia = 0.0
        elif n_inter % 2 == 1:
            median_ia = sorted_inter[n_inter // 2]
        else:
            median_ia = (sorted_inter[n_inter // 2 - 1] + sorted_inter[n_inter // 2]) / 2.0
        median_interarrival_ms = round(median_ia * 1000, 2)

        # Total spread of first 50 (or fewer) mentions
        first_50_spread = timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0.0

        # ── Step 4: Cluster near-simultaneous mentions ───────────────────
        # Cluster: group consecutive mentions where each gap is < CLUSTER_WINDOW_SEC
        clusters = []
        current_cluster = [timestamps[0]]
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            if gap < self.CLUSTER_WINDOW_SEC:
                current_cluster.append(timestamps[i])
            else:
                if len(current_cluster) >= 2:
                    clusters.append(list(current_cluster))
                current_cluster = [timestamps[i]]
        if len(current_cluster) >= 2:
            clusters.append(list(current_cluster))

        # ── Step 5: Rolling window analysis for bot farm detection ───────
        # Find the maximum number of mentions in any BOT_FARM_WINDOW_SEC window
        max_in_window = 0
        for i in range(len(timestamps)):
            window_end = timestamps[i] + self.BOT_FARM_WINDOW_SEC
            count = sum(1 for t in timestamps if timestamps[i] <= t <= window_end)
            if count > max_in_window:
                max_in_window = count
                timestamps[i]

        # Largest cluster stats
        largest_cluster_size = max(len(c) for c in clusters) if clusters else 1
        if clusters:
            largest_cluster = max(clusters, key=len)
            largest_cluster_duration = largest_cluster[-1] - largest_cluster[0]
        else:
            largest_cluster_duration = 0.0

        # ── Step 6: Score calculation ────────────────────────────────────
        # Score dimension 1: Bot farm burst (0-60 points)
        # If > BOT_FARM_THRESHOLD mentions in BOT_FARM_WINDOW_SEC → near max
        if max_in_window >= self.BOT_FARM_THRESHOLD:
            burst_score = 60.0
        elif max_in_window >= 10:
            burst_score = 30.0 + (max_in_window - 10) / (self.BOT_FARM_THRESHOLD - 10) * 30.0
        elif max_in_window >= 5:
            burst_score = (max_in_window - 5) / 5.0 * 30.0
        else:
            burst_score = 0.0

        # Score dimension 2: Inter-arrival tightness (0-25 points)
        if median_interarrival_ms < 100:  # < 100ms between mentions → very bot-like
            tightness_score = 25.0
        elif median_interarrival_ms < 1000:  # < 1s
            tightness_score = 10.0 + (1000 - median_interarrival_ms) / 900.0 * 15.0
        elif median_interarrival_ms < 5000:  # 1-5s
            tightness_score = 5.0
        else:
            tightness_score = 0.0

        # Score dimension 3: Spread of first 50 mentions (0-15 points)
        if total >= 10:
            if first_50_spread < 3.0:  # All mentions in <3s → very bot-like
                spread_score = 15.0
            elif first_50_spread < self.ORGANIC_SPREAD_SEC:
                # Linear from 15 to 0 as spread increases
                spread_score = 15.0 * (1.0 - first_50_spread / self.ORGANIC_SPREAD_SEC)
                spread_score = max(0.0, spread_score)
            else:
                spread_score = 0.0
        else:
            # Fewer than 10 mentions → less data, cap spread score
            spread_score = 5.0

        velocity_score = round(burst_score + tightness_score + spread_score, 1)
        velocity_score = max(0.0, min(100.0, velocity_score))

        # ── Step 7: Bot farm confidence ──────────────────────────────────
        # Confidence that this is a bot farm (0-1)
        if velocity_score >= 80:
            bot_farm_confidence = round(velocity_score / 100.0, 2)
        elif max_in_window >= self.BOT_FARM_THRESHOLD:
            bot_farm_confidence = 0.9
        elif largest_cluster_size >= 10 and largest_cluster_duration < 2.0:
            bot_farm_confidence = 0.7
        elif largest_cluster_size >= 5:
            bot_farm_confidence = 0.4
        elif velocity_score < 20 and first_50_spread >= self.ORGANIC_SPREAD_SEC:
            bot_farm_confidence = 0.05
        else:
            bot_farm_confidence = round(velocity_score / 100.0, 2)

        # ── Step 8: Risk label ──────────────────────────────────────────
        if max_in_window >= self.BOT_FARM_THRESHOLD:
            risk_label = "CRITICAL"
        elif velocity_score >= 60 or largest_cluster_size >= 10:
            risk_label = "HIGH"
        elif velocity_score >= 30 or largest_cluster_size >= 5:
            risk_label = "SUSPICIOUS"
        elif first_50_spread >= self.ORGANIC_SPREAD_SEC and total >= 10:
            risk_label = "ORGANIC"
        else:
            risk_label = "LOW"

        # ── Step 9: Build timeline (sampled for the report) ──────────────
        mention_timeline = []
        # Sample at most 50 points for the timeline
        step = max(1, len(timestamps) // 50)
        for i in range(0, len(timestamps), step):
            mention_timeline.append(
                {
                    "timestamp": timestamps[i],
                    "cumulative_count": i + 1,
                }
            )
        # Always include the final point
        if (
            len(timestamps) > 1
            and mention_timeline
            and mention_timeline[-1]["cumulative_count"] < len(timestamps)
        ):
            mention_timeline.append(
                {
                    "timestamp": timestamps[-1],
                    "cumulative_count": len(timestamps),
                }
            )

        # ── Step 10: Warnings ────────────────────────────────────────────
        warnings = []
        if max_in_window >= self.BOT_FARM_THRESHOLD:
            warnings.append(
                f"CRITICAL: {max_in_window} mentions arrived within {self.BOT_FARM_WINDOW_SEC}s - bot farm detected"
            )
        if largest_cluster_size >= 5:
            cluster_types = ", ".join(
                f"{len(c)} mentions in {c[-1] - c[0]:.2f}s" for c in clusters if len(c) >= 5
            )
            warnings.append(f"COORDINATED CLUSTERS: {cluster_types}")
        if median_interarrival_ms < 100:
            warnings.append(
                f"BOT-LIKE VELOCITY: median inter-arrival time is {median_interarrival_ms:.0f}ms"
            )
        if total < 10:
            warnings.append(
                f"LOW VOLUME: only {total} mentions available (need ≥10 for reliable velocity analysis)"
            )

        return SocialVelocityReport(
            velocity_score=velocity_score,
            bot_farm_confidence=bot_farm_confidence,
            mention_timeline=mention_timeline,
            mention_sources=dict(mention_sources),
            risk_label=risk_label,
            total_mentions_analyzed=total,
            largest_cluster_size=largest_cluster_size,
            largest_cluster_duration_sec=round(largest_cluster_duration, 3),
            first_50_spread_sec=round(first_50_spread, 3),
            median_interarrival_ms=median_interarrival_ms,
            warnings=warnings,
        )
