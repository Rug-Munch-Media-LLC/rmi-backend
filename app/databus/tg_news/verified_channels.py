"""Channels measured against the Telegram public preview, with their role.

Every row below was probed on 2026-10-23 from Talos with ``GET t.me/s/<handle>``
*without* following redirects, so the recorded status is the preview gate
itself (``200`` + message nodes = open, ``302`` = preview closed) rather than
the channel landing page a redirect would hide behind. ``title``/``subs``/
``newest`` are what the page actually said at probe time — that is what proves a
handle is the real desk and not a squatter holding the name.

Findings that changed the sweep:

* ``@zachxbt`` is OPEN (109K, newest post 2026-10-21) — never closed.
* ``@scamsnifferalert`` (706), ``@lookonchainchannel`` (18.9K),
  ``@arkhamintelligence`` (16.5K) and ``@misttrack_alert`` (2.8K, ~20 posts/hour)
  are open and live; the capitalized handles in the task list are 302.
* ``@certikalert`` ("Certik Alert (Meme)", 48 subs), ``@peckshieldalert``
  ("Cool", 46 subs, 1 post), ``@hypernativelabs`` ("For sale", 3 subs) and
  ``@rektnews`` ("fka-rekt", repurposed into a personal dispute) are squats, not
  desks — feeding them as 0.9 security intel was poisoning the ranking.
* ``@slowmistteam`` (2022), ``@slowmist_official`` (2022) and
  ``@misttrack_official`` (2025-01) are open but dormant: swept for history,
  weighted down, freshness asserted by ``newest``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedChannel:
    username: str
    role: str
    subs: int | None = None
    link_share: float = 0.0
    note: str = ""
    #: Explicit source weight; ``None`` falls back to ``classify.KIND_WEIGHTS[role]``.
    weight: float | None = None
    #: ISO date of the newest previewed post at probe time (freshness proof).
    newest: str | None = None


#: Open preview, publishing now. These are the picked set the sweep must reach.
VERIFIED: tuple[VerifiedChannel, ...] = (
    VerifiedChannel(
        "zachxbt",
        "security",
        109_000,
        0.9,
        "on-chain investigation; DPRK/Tornado posts",
        0.95,
        "2026-10-21",
    ),
    VerifiedChannel(
        "misttrack_alert",
        "security",
        2_800,
        0.8,
        "SlowMist AML flow alerts, firehose",
        0.9,
        "2026-10-22",
    ),
    VerifiedChannel(
        "scamsnifferalert",
        "security",
        706,
        0.8,
        "wallet-drainer alerts, links x.com/realScamSniffer",
        0.9,
        "2026-09-24",
    ),
    VerifiedChannel(
        "lookonchainchannel",
        "security",
        18_900,
        0.95,
        "on-chain flows; outbound tx links",
        0.85,
        "2026-10-22",
    ),
    VerifiedChannel(
        "arkhamintelligence",
        "security",
        16_500,
        0.7,
        "Arkham Updates; entity/attribution",
        0.85,
        "2026-10-20",
    ),
    VerifiedChannel(
        "binance_announcements", "primary", 3_290_000, 1.0, "exchange notices", None, None
    ),
    VerifiedChannel("cointelegraph", "primary", 329_000, 1.0, "outlet, links articles", None, None),
    VerifiedChannel("whale_alert_io", "primary", 297_000, 1.0, "on-chain transfers", None, None),
    VerifiedChannel("bwenews", "primary", 72_300, 0.5, "market news", None, None),
    VerifiedChannel("glassnode", "primary", 43_800, 0.33, "research desk", None, None),
    VerifiedChannel("coingape", "primary", 23_200, 1.0, "outlet", None, None),
    VerifiedChannel("spotonchain", "primary", 21_300, 0.9, "on-chain data", None, None),
    VerifiedChannel("cryptobriefing", "primary", 9_550, 1.0, "outlet", None, None),
    VerifiedChannel("coinedition", "primary", 2_410, 0.9, "outlet", None, None),
    VerifiedChannel("intotheblock", "primary", 1_530, 0.9, "on-chain analytics", None, None),
    VerifiedChannel("newsbtc", "primary", 973, 1.0, "outlet", None, None),
    VerifiedChannel("laevitas", "primary", 678, 0.95, "derivatives research", None, None),
    VerifiedChannel("coinpedia", "primary", 367, 0.85, "outlet", None, None),
    VerifiedChannel(
        "watcherguru", "aggregator", 628_000, 0.0, "fast restatement, no links", 0.35, None
    ),
    VerifiedChannel(
        "blockchainreporter", "aggregator", 54_200, 0.0, "no outbound links", 0.35, None
    ),
    VerifiedChannel("TreeNewsFeed", "aggregator", 34_800, 0.2, "headline screener", 0.35, None),
)

#: Open preview, nothing published for >180 days. Swept once for history, then
#: the state file's ``last_id`` short-circuits the run; weight halved because a
#: dormant desk cannot be a live alert source. The Medium/X lane carries them.
STALE: tuple[VerifiedChannel, ...] = (
    VerifiedChannel(
        "slowmist_official",
        "security",
        244,
        0.6,
        "SlowMist Official; dormant since 2022-09",
        0.5,
        "2022-09-27",
    ),
    VerifiedChannel(
        "slowmistteam",
        "security",
        493,
        0.6,
        "superseded 'old' SlowMist channel; 2022-04",
        0.4,
        "2022-04-29",
    ),
    VerifiedChannel(
        "misttrack_official",
        "security",
        246,
        0.6,
        "MistTrack Official; last post 2025-01",
        0.5,
        "2025-01-17",
    ),
)

#: Lookalikes / squats / repurposed handles proved by page title + subs. NEVER
#: swept, never weighted, never counted as intel — a 48-sub "Certik Alert (Meme)"
#: is not CertiK, and a 3-sub "For sale" page is a parked name.
IMPOSTOR: frozenset[str] = frozenset(
    name.lower()
    for name in (
        "certikalert",
        "CertiKAlert",
        "peckshieldalert",
        "PeckShieldAlert",
        "hypernativelabs",
        "HypernativeLabs",
        "rektnews",
        "RektNews",
        "rekt_news",
        "SEALCommunity",
        "PumpFunAlerts",
        "zachxbt_official",
        "goplus_security",
    )
)

from app.databus.tg_news.preview_off import FALLBACK, GAPS, PREVIEW_OFF  # noqa: F401  (re-exported)

#: Promotion-first channels: classified + weighted 0.0 but NEVER ingested as
#: news (task rule: shill = monitored source only).
MONITORED_ONLY: tuple[VerifiedChannel, ...] = (
    VerifiedChannel("AirdropAlert", "shill", 2_760, 0.9, "airdrop promos, stale since 2025-04"),
)

ROLES = {channel.username.lower(): channel.role for channel in VERIFIED + STALE + MONITORED_ONLY}

#: Explicit per-channel weight overrides (falls back to ``KIND_WEIGHTS[role]``).
WEIGHTS = {channel.username.lower(): channel.weight for channel in VERIFIED + STALE if channel.weight is not None}

#: Everything a sweep must reach, live first then dormant.
SWEPT = VERIFIED + STALE
