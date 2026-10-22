"""Channels proven reachable through the public preview, with their role.

Probed 2026-10-23 against ``t.me/s/<handle>`` (no auth, no join). ``role`` is
what the channel *is*, measured not assumed:

* ``primary``    — own reporting/data, links the primary source out
* ``aggregator`` — restates/reposts, little or no outbound link
* ``shill``      — promotion-first; monitored, never ranked as news

``subs``/``link_share`` are the evidence behind each row; ``classify`` turns
``role`` into the numeric weight stored with the source for Rugbrain ranking.
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


VERIFIED: tuple[VerifiedChannel, ...] = (
    VerifiedChannel("binance_announcements", "primary", 3_290_000, 1.0, "exchange notices"),
    VerifiedChannel("cointelegraph", "primary", 329_000, 1.0, "outlet, links articles"),
    VerifiedChannel("whale_alert_io", "primary", 297_000, 1.0, "on-chain transfers"),
    VerifiedChannel("bwenews", "primary", 72_300, 0.5, "market news"),
    VerifiedChannel("glassnode", "primary", 43_800, 0.33, "research desk"),
    VerifiedChannel("coingape", "primary", 23_200, 1.0, "outlet"),
    VerifiedChannel("spotonchain", "primary", 21_300, 0.9, "on-chain data"),
    VerifiedChannel("cryptobriefing", "primary", 9_550, 1.0, "outlet"),
    VerifiedChannel("coinedition", "primary", 2_410, 0.9, "outlet"),
    VerifiedChannel("intotheblock", "primary", 1_530, 0.9, "on-chain analytics"),
    VerifiedChannel("newsbtc", "primary", 973, 1.0, "outlet"),
    VerifiedChannel("laevitas", "primary", 678, 0.95, "derivatives research"),
    VerifiedChannel("coinpedia", "primary", 367, 0.85, "outlet"),
    VerifiedChannel("watcherguru", "aggregator", 628_000, 0.0, "fast restatement, no links"),
    VerifiedChannel("blockchainreporter", "aggregator", 54_200, 0.0, "no outbound links"),
    VerifiedChannel("TreeNewsFeed", "aggregator", 34_800, 0.2, "headline screener"),
)

#: t.me/s/ 302-redirects to the plain channel page => public preview closed.
#: Verified 2026-10-23; skipped here, still monitored by the userbot/registry.
PREVIEW_OFF: frozenset[str] = frozenset(
    name.lower()
    for name in (
        "coindesk",
        "CoinDeskNews",
        "wublockchain",
        "WuBlockchain",
        "cryptoslate",
        "beincrypto",
        "theblock",
        "TheBlock__",
        "TheBlock_",
        "cryptonews",
        "thedefiant",
        "ambcrypto",
        "AMBCrypto",
        "bitcoinmagazine",
        "zachxbt",
        "SlowMist",
        "PeckShield",
        "BlockSecTeam",
        "CertiK",
        "ScamSniffer",
        "SEAL_911",
        "MistTrack",
        "DefiLlama",
        "dune",
        "DuneAnalytics",
        "Nansen",
        "nansen_ai",
        "kaiko",
        "KaikoResearch",
        "chainalysis",
        "messaricrypto",
        "Messari",
        "lookonchain",
        "cryptoquant_com",
        "GoPlusSecurity",
        "HypernativeLabs",
        "safe_global",
        "RugCheck",
        "code4rena",
        "spearbit_dao",
        "ConsenSysDiligence",
        "JupiterExchange",
        "DriftProtocol",
        "HyperliquidX",
        "jito_sol",
        "SolanaFloor",
        "solana",
        "heliuslabs",
        "EleanorTerrett",
        "CFTC",
        "nic_carter",
        "TheDailyGwei",
        "bellcurve_pod",
        "asxn",
        "coingecko",
        "coinmarketcap",
        "dexscreener",
        "krakenfx",
        "okx",
        "binance",
        "cryptobanter",
        "BitcoinNewsCom",
        "cryptopolitan",
        "financemagnates",
        "zycrypto",
        "dropstab",
        "cryptorank",
        "coinglass",
        "solanafloor",
        "marginfi",
        "tokenterminal",
        "l2beat",
        "ethresearch",
        "drift",
    )
)

#: Promotion-first channels: classified + weighted 0.0 but NEVER ingested as
#: news (task rule: shill = monitored source only).
MONITORED_ONLY: tuple[VerifiedChannel, ...] = (
    VerifiedChannel("AirdropAlert", "shill", 2_760, 0.9, "airdrop promos, stale since 2025-04"),
)

ROLES = {channel.username.lower(): channel.role for channel in VERIFIED + MONITORED_ONLY}
