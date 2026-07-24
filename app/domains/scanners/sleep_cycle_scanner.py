"""SENTINEL - Deployer Sleep Cycle Analysis
===========================================
Analyzes wallet deployment timestamps (hours of day, UTC) to detect
abnormal sleep schedules. Humans sleep - if a project deploys at 4 AM
local time for a suspected region and follows abnormal schedules, flag
it as overseas farm.

Logic:
  - Parse hour-of-day from ISO timestamps (UTC)
  - Check against known sleep patterns per region
  - If 70%+ deployments are 12am-5am UTC (Asia farming hours) → high score
  - If no 8h sleep gap cluster detected → suspicious (bots dont sleep)
  - Human devs show predictable 8h gap clusters in deployment activity

Pure computation module - no external API calls needed.
"""

from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger("sleep_cycle_scanner")

# ── Region sleep windows (UTC) ──────────────────────────────────────────
#
#    UTC hour  →  local time in each major region
#    ────────────────────────────────────────────
#    00-05 UTC = 08:00-13:00 Asia (normal working hours)
#               = 03:00-08:00 Eastern Europe (early morning / dawn)
#               = 04:00-09:00 Middle East (morning)
#               = 20:00-01:00 US East (deep night / sleep)
#               = 17:00-22:00 US West (evening)
#               = 10:00-15:00 Oceania (midday)

REGION_WINDOWS = {
    "Asia": (0, 5),  # 12am-5am UTC → 8am-1pm (productive)
    "Eastern Europe": (1, 6),  # 1am-6am UTC → 3am-8am (early work)
    "Middle East": (3, 8),  # 3am-8am UTC → 6am-11am (morning)
    "Oceania": (8, 14),  # 8am-2pm UTC → 6pm-12am (late evening)
    "West Africa": (6, 12),  # 6am-12pm UTC → 7am-1pm (morning)
    "Western Europe": (7, 17),  # 7am-5pm UTC → 8am-6pm (standard)
    "Americas": (12, 22),  # 12pm-10pm UTC → 8am-6pm EST / 5am-3pm PST
}

SLEEP_GAP_THRESHOLD_HOURS = 6  # minimum consecutive hours without deploy to count as "sleep"
CRITICAL_ABNORMAL_PCT = 70  # % in 12am-5am UTC that triggers max score

@dataclass
class SleepCycleReport:
    """Report from SleepCycleAnalyzer.

    Attributes:
        sleep_score:    0-100, higher = more suspicious (overseas farm / bot)
        likely_region:  Best-guess region based on deployment hour clustering
        abnormal_hours: Distinct UTC hours (0-23) where deploys occurred during
                        abnormal sleep windows (midnight-5am UTC)
        normal_hours:   Distinct UTC hours where deploys occurred during normal
                        waking hours
        risk_label:     CRITICAL / HIGH / MEDIUM / LOW
    """

    sleep_score: int
    likely_region: str = "unknown"
    abnormal_hours: list[int] = field(default_factory=list)
    normal_hours: list[int] = field(default_factory=list)
    risk_label: str = "LOW"

class SleepCycleAnalyzer:
    """Analyzes deployment timestamp patterns to detect abnormal sleep cycles.

    Compares deployment hour distribution against known human sleep patterns
    to identify potential overseas farms operating outside normal business hours
    for their suspected region.

    This is a pure computation module - no external API calls.
    """

    # UTC hours considered "abnormal" - midnight to 5am
    # These are sleep hours for Western timezones but productive hours in Asia
    ABNORMAL_HOURS = list(range(6))  # 12am-5am UTC  # noqa: RUF012

    def __init__(self) -> None:
        pass

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_hour(timestamp_str: str) -> int | None:
        """Parse an ISO-format timestamp string and return UTC hour (0-23).

        Handles common ISO 8601 formats including 'Z' suffix, '+00:00'
        offset, and numeric offsets like '+08:00'.
        """
        try:
            # Normalise 'Z' suffix to +00:00 which fromisoformat handles
            ts = (
                timestamp_str.replace("Z", "+00:00")
                if timestamp_str.endswith("Z")
                else timestamp_str
            )
            dt = datetime.fromisoformat(ts)
            dt_utc = dt.astimezone(UTC)
            return dt_utc.hour
        except (ValueError, TypeError) as exc:
            logger.warning("Could not parse timestamp '%s': %s", timestamp_str, exc)
            return None

    @staticmethod
    def _find_largest_sleep_gap(hours: list[int]) -> int:
        """Find the largest stretch of consecutive *missing* hours.

        This detects natural sleep windows - e.g., no deployments for 8
        consecutive hours (22:00-06:00).  Also checks wrap-around so that
        e.g. hours [0,1,2,3,4, 10,11,12,13,14] yields a gap of 5 (5→10).
        """
        if not hours:
            return 24

        unique = sorted(set(hours))
        if len(unique) <= 1:
            return 24

        max_gap = 0
        for i in range(1, len(unique)):
            gap = unique[i] - unique[i - 1]
            if gap > max_gap:
                max_gap = gap

        # Check wrap-around: last hour → midnight → first hour
        wrap_gap = (24 - unique[-1]) + unique[0]
        if wrap_gap > max_gap:
            max_gap = wrap_gap

        return max_gap

    @staticmethod
    def _determine_likely_region(hour_counts: Counter, total: int) -> str:
        """Score each region by how many deployments fall inside its UTC window."""
        best_region = "unknown"
        best_score = 0.0

        for region, (start, end) in REGION_WINDOWS.items():
            region_count = sum(hour_counts.get(h, 0) for h in range(start, end + 1))
            pct = (region_count / total) * 100 if total else 0
            if pct > best_score:
                best_score = pct
                best_region = region

        return best_region

    # ── Main analysis ──────────────────────────────────────────────────

    async def analyze(self, timestamps: list[str]) -> SleepCycleReport:
        """Analyze deployment timestamps for abnormal sleep patterns.

        Args:
            timestamps: List of ISO-format timestamp strings (UTC).

        Returns:
            SleepCycleReport with sleep_score, likely_region, abnormal/normal
            hours, and risk_label.
        """
        # ── Edge case: empty input ─────────────────────────────────
        if not timestamps:
            return SleepCycleReport(
                sleep_score=0,
                likely_region="unknown",
                abnormal_hours=[],
                normal_hours=list(range(24)),
                risk_label="LOW",
            )

        # ── Parse all timestamps ───────────────────────────────────
        hours: list[int] = []
        for ts in timestamps:
            h = self._parse_hour(ts)
            if h is not None:
                hours.append(h)

        if not hours:
            return SleepCycleReport(
                sleep_score=0,
                likely_region="unknown",
                abnormal_hours=[],
                normal_hours=list(range(24)),
                risk_label="LOW",
            )

        total = len(hours)
        hour_counts = Counter(hours)

        # ── 1. Abnormal-hour concentration (midnight-5am UTC) ──────
        abnormal_count = sum(hour_counts.get(h, 0) for h in self.ABNORMAL_HOURS)
        abnormal_pct = (abnormal_count / total) * 100

        # ── 2. Sleep gap detection ─────────────────────────────────
        largest_gap = self._find_largest_sleep_gap(hours)
        has_sleep_pattern = largest_gap >= SLEEP_GAP_THRESHOLD_HOURS

        # ── 3. Distribution breadth ────────────────────────────────
        distinct_hours = len(set(hours))

        # ── 4. Region guess ────────────────────────────────────────
        likely_region = self._determine_likely_region(hour_counts, total)

        # ── Score calculation (0-100) ──────────────────────────────
        score = 0

        # 4a. Abnormal-hour concentration (max 50 pts)
        if abnormal_pct >= CRITICAL_ABNORMAL_PCT:
            score += 50
        elif abnormal_pct >= 50:
            score += 40
        elif abnormal_pct >= 30:
            score += 25
        elif abnormal_pct >= 15:
            score += 10

        # 4b. No sleep pattern → bot-like behaviour (max 30 pts)
        if not has_sleep_pattern:
            if largest_gap <= 2:
                score += 30  # near-uniform distribution
            elif largest_gap <= 4:
                score += 20  # weak clustering
            else:
                score += 10  # marginal gap

        # 4c. Overly wide distribution - real humans concentrate (max 20 pts)
        if distinct_hours >= 18:
            score += 20  # deploying almost every hour
        elif distinct_hours >= 14:
            score += 10

        # 4d. Bonus: if abnormal_pct is very high *and* no sleep gap
        if abnormal_pct >= 60 and not has_sleep_pattern:
            score = min(score + 10, 100)

        score = min(100, score)

        # ── Risk label ─────────────────────────────────────────────
        if score >= 70:
            risk_label = "CRITICAL"
        elif score >= 50:
            risk_label = "HIGH"
        elif score >= 25:
            risk_label = "MEDIUM"
        else:
            risk_label = "LOW"

        # ── Build hour lists for report ────────────────────────────
        deployed_hours = set(hours)
        abnormal_hours_list = sorted(h for h in deployed_hours if h in self.ABNORMAL_HOURS)
        normal_hours_list = sorted(h for h in deployed_hours if h not in self.ABNORMAL_HOURS)

        return SleepCycleReport(
            sleep_score=score,
            likely_region=likely_region,
            abnormal_hours=abnormal_hours_list,
            normal_hours=normal_hours_list,
            risk_label=risk_label,
        )
