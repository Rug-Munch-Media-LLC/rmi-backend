"""Handles whose Telegram preview is closed (302), and who carries them instead.

Part of the source table in :mod:`app.databus.tg_news.verified_channels`; split
out to keep each module inside the repo's 200-line cap.
"""

from __future__ import annotations

#: Preview closed: measured ``302`` on ``t.me/s/<handle>`` (no follow). Each is
#: carried by :data:`FALLBACK` or listed in :data:`GAPS` — never silently dropped.
PREVIEW_OFF: frozenset[str] = frozenset(
    name.lower()
    for name in (
        # Security desks with no public preview (302) — web/X lane carries them.
        "SlowMist",
        "PeckShieldAlert",
        "CertiKAlert",
        "ScamSniffer",
        "BlockSecTeam",
        "blocksec_alert",
        "SEAL_911",
        "SEAL_Org",
        "MistTrack",
        "misttrack_io",
        "lookonchain",
        "gmgnai",
        "pump_sol_alert",
        "pump_sol_alerts",
        # News outlets whose preview is closed (unchanged by this probe).
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
        "cryptoquant_com",
        "GoPlusSecurity",
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
        "solanafloor",
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
        "marginfi",
        "tokenterminal",
        "l2beat",
        "ethresearch",
        "drift",
    )
)

#: Closed handle -> the lane that still ingests it. ``x:`` = X registry
#: (``rmi-backend/data/x_sources.json``), ``rss:``/``sitemap:`` =
#: ``rugbrain/config/security_feeds.txt``, ``tg:`` = the open channel that
#: superseded it. A closed handle absent from here MUST be in :data:`GAPS`.
FALLBACK: dict[str, tuple[str, ...]] = {
    "slowmist": ("rss:https://slowmist.medium.com/feed", "x:SlowMist_Team", "tg:slowmist_official"),
    "peckshieldalert": ("rss:https://peckshield.medium.com/feed", "x:PeckShieldAlert"),
    "certikalert": ("rss:https://certik.medium.com/feed", "x:CertiKAlert"),
    "scamsniffer": ("x:realScamSniffer", "tg:scamsnifferalert"),
    "blocksecteam": ("sitemap:https://blocksec.com/sitemap.xml", "x:BlockSecTeam"),
    "blocksec_alert": ("sitemap:https://blocksec.com/sitemap.xml",),
    "lookonchain": ("x:lookonchain", "tg:lookonchainchannel"),
    "misttrack": ("tg:misttrack_alert", "tg:misttrack_official"),
    "misttrack_io": ("tg:misttrack_alert",),
    "seal_911": ("x:SEAL_Org",),
    "seal_org": ("x:SEAL_Org",),
    "chainalysis": ("rss:https://www.chainalysis.com/blog/feed/",),
    "wublockchain": ("rss:https://wublockchain.medium.com/feed", "x:WuBlockchain"),
    "coindesk": ("rss:https://www.coindesk.com/arc/outboundfeeds/rss/", "x:CoinDesk"),
    "coindesknews": ("rss:https://www.coindesk.com/arc/outboundfeeds/rss/",),
    "theblock": ("x:TheBlock",),
    "theblock__": ("x:TheBlock",),
    "theblock_": ("x:TheBlock",),
    "gmgnai": ("x:gmgnai",),
    "spotonchain": ("x:spotonchain", "tg:spotonchain"),
    "arkhamintelligence": ("x:arkham", "tg:arkhamintelligence"),
}

#: The closed handles that matter to the security/scam run: every one of these
#: MUST be carried by a fallback lane or named in :data:`GAPS`. Outlets further
#: up have no preview either, but they were never security intel and are only
#: listed so the registry cannot re-add them as if they were open.
SECURITY_CLOSED: frozenset[str] = frozenset(
    name.lower()
    for name in (
        "SlowMist",
        "PeckShieldAlert",
        "CertiKAlert",
        "ScamSniffer",
        "BlockSecTeam",
        "blocksec_alert",
        "SEAL_911",
        "SEAL_Org",
        "MistTrack",
        "misttrack_io",
        "lookonchain",
        "gmgnai",
        "pump_sol_alert",
        "pump_sol_alerts",
    )
)

#: Closed AND uncovered (or covered only by an unverified X row). Reported, not
#: hidden: the web lane cannot invent a source that does not publish.
GAPS: frozenset[str] = frozenset(name.lower() for name in ("pump_sol_alert", "pump_sol_alerts", "gmgnai", "seal_911"))
