"""Shared helpers and curated account data for the X/CT intelligence pipeline."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.databus.vault import resolve_key
from app.databus.x_intel.cache import load_influential_accounts

logger = get_logger("x_intel")

# ── TOP CT ACCOUNTS - Curated, diverse, high-signal ────────────────

CT_ACCOUNTS: dict[str, list[dict[str, Any]]] = {
    # ── Tier 1: Must-track (breaking news, high signal) ──
    "crypto_news": [
        {"handle": "WatcherGuru", "name": "Watcher.Guru", "category": "breaking", "weight": 1.0},
        {"handle": "dbnewswire", "name": "DB News Wire", "category": "breaking", "weight": 1.0},
        {"handle": "CoinDesk", "name": "CoinDesk", "category": "journalism", "weight": 0.95},
        {"handle": "TheBlock__", "name": "The Block", "category": "journalism", "weight": 0.95},
        {
            "handle": "Cointelegraph",
            "name": "CoinTelegraph",
            "category": "journalism",
            "weight": 0.9,
        },
        {"handle": "Blockworks_", "name": "Blockworks", "category": "journalism", "weight": 0.9},
        {"handle": "DLNewsInfo", "name": "DL News", "category": "journalism", "weight": 0.85},
        {"handle": "SBF_FTX", "name": "SBF Tracker", "category": "drama", "weight": 0.7},
    ],
    # ── Tier 2: Analysis & Research ──
    "analysts": [
        {"handle": "zachxbt", "name": "ZachXBT", "category": "investigation", "weight": 1.0},
        {"handle": "0xfoobar", "name": "foobar", "category": "defi_analysis", "weight": 0.9},
        {"handle": "hasufl", "name": "Hasu", "category": "research", "weight": 0.9},
        {"handle": "jwpeirce", "name": "Hester Peirce", "category": "regulation", "weight": 0.85},
        {"handle": "sassal0x", "name": "Anthony Sassano", "category": "ethereum", "weight": 0.85},
        {"handle": "DoveyWan", "name": "Dovey Wan", "category": "macro", "weight": 0.85},
        {"handle": "0xCygaar", "name": "Cygaar", "category": "dev", "weight": 0.8},
        {"handle": "crypto_condom", "name": "Crypto Condom", "category": "memes", "weight": 0.6},
    ],
    # ── Tier 3: DeFi & Trading ──
    "defi_traders": [
        {"handle": "DeFi_Dad", "name": "DeFi Dad", "category": "defi", "weight": 0.8},
        {
            "handle": "gabrielhaines",
            "name": "Gabriel Haines",
            "category": "entertainment",
            "weight": 0.7,
        },
        {"handle": "cobie", "name": "Cobie", "category": "trading", "weight": 0.9},
        {"handle": "ThinkingUSD", "name": "ThinkingUSD", "category": "trading", "weight": 0.8},
        {"handle": "Rewkang", "name": "Rekt Fencer", "category": "trading", "weight": 0.75},
        {"handle": "lightcrypto", "name": "Light", "category": "research", "weight": 0.85},
        {"handle": "0x_Lens", "name": "0xLens", "category": "onchain", "weight": 0.8},
        {"handle": "lookonchain", "name": "Lookonchain", "category": "onchain", "weight": 0.9},
    ],
    # ── Tier 4: Solana & Memecoins ──
    "solana_meme": [
        {"handle": "aeyakovenko", "name": "Anatoly Yakovenko", "category": "solana", "weight": 0.9},
        {"handle": "0xMert_", "name": "Mert", "category": "solana", "weight": 0.8},
        {
            "handle": "SolanaLegend",
            "name": "Solana Legend",
            "category": "solana_memes",
            "weight": 0.7,
        },
        {"handle": "blknoiz06", "name": "blknoiz06", "category": "solana_defi", "weight": 0.75},
        {"handle": "theunipcs", "name": "Unipcs", "category": "memecoins", "weight": 0.7},
        {"handle": "MustStopMurad", "name": "Murad", "category": "memecoins", "weight": 0.8},
        {"handle": "0xENAS", "name": "ENAS", "category": "memecoins", "weight": 0.65},
    ],
    # ── Tier 5: Macro & VC ──
    "macro_vc": [
        {"handle": "RaoulGMI", "name": "Raoul Pal", "category": "macro", "weight": 0.85},
        {"handle": "APompliano", "name": "Pomp", "category": "bitcoin", "weight": 0.8},
        {"handle": "balajis", "name": "Balaji", "category": "tech", "weight": 0.9},
        {"handle": "nic__carter", "name": "Nic Carter", "category": "bitcoin", "weight": 0.85},
        {"handle": "ecoinometrics", "name": "Ecoinometrics", "category": "data", "weight": 0.8},
        {"handle": "LynAldenContact", "name": "Lyn Alden", "category": "macro", "weight": 0.9},
        {
            "handle": "michael_saylor",
            "name": "Michael Saylor",
            "category": "bitcoin",
            "weight": 0.95,
        },
        {"handle": "CathieDWood", "name": "Cathie Wood", "category": "macro", "weight": 0.85},
        {"handle": "APompliano", "name": "Anthony Pompliano", "category": "bitcoin", "weight": 0.8},
        {"handle": "cburniske", "name": "Chris Burniske", "category": "vc", "weight": 0.85},
        {"handle": "CryptoHayes", "name": "Arthur Hayes", "category": "macro", "weight": 0.9},
    ],
    # ── Tier 6: Extended - more voices, all verified ──
    "extended": [
        {"handle": "matt_hougan", "name": "Matt Hougan", "category": "etf", "weight": 0.8},
        {"handle": "EricBalchunas", "name": "Eric Balchunas", "category": "etf", "weight": 0.85},
        {"handle": "JSeyff", "name": "James Seyffart", "category": "etf", "weight": 0.8},
        {"handle": "biancoresearch", "name": "Jim Bianco", "category": "macro", "weight": 0.85},
        {"handle": "LucasNuzzi", "name": "Lucas Nuzzi", "category": "onchain", "weight": 0.8},
        {"handle": "nansen_ai", "name": "Nansen", "category": "onchain", "weight": 0.85},
        {"handle": "DuneAnalytics", "name": "Dune Analytics", "category": "data", "weight": 0.8},
        {"handle": "glassnode", "name": "Glassnode", "category": "onchain", "weight": 0.9},
        {"handle": "intotheblock", "name": "IntoTheBlock", "category": "onchain", "weight": 0.85},
        {"handle": "MessariCrypto", "name": "Messari", "category": "research", "weight": 0.9},
        {
            "handle": "Delphi_Digital",
            "name": "Delphi Digital",
            "category": "research",
            "weight": 0.9,
        },
        {"handle": "PanteraCapital", "name": "Pantera Capital", "category": "vc", "weight": 0.8},
        {"handle": "a16zcrypto", "name": "a16z Crypto", "category": "vc", "weight": 0.85},
        {"handle": "paradigm", "name": "Paradigm", "category": "vc", "weight": 0.85},
        {"handle": "1confirmation", "name": "1confirmation", "category": "vc", "weight": 0.7},
        {"handle": "ElectricCapital", "name": "Electric Capital", "category": "vc", "weight": 0.8},
        {"handle": "VariantFund", "name": "Variant Fund", "category": "vc", "weight": 0.75},
        {"handle": "dragonfly_xyz", "name": "Dragonfly", "category": "vc", "weight": 0.75},
        {"handle": "MulticoinCap", "name": "Multicoin Capital", "category": "vc", "weight": 0.8},
        {"handle": "polychaincap", "name": "Polychain Capital", "category": "vc", "weight": 0.8},
        {"handle": "zhusu", "name": "Zhu Su", "category": "trading", "weight": 0.7},
        {"handle": "KyleSamani", "name": "Kyle Samani", "category": "vc", "weight": 0.8},
        {"handle": "Hoskinson_IO", "name": "IOHK", "category": "protocol", "weight": 0.7},
        {
            "handle": "VitalikButerin",
            "name": "Vitalik Buterin",
            "category": "ethereum",
            "weight": 1.0,
        },
        {"handle": "drakefjustin", "name": "Justin Drake", "category": "ethereum", "weight": 0.85},
        {"handle": "timbeiko", "name": "Tim Beiko", "category": "ethereum", "weight": 0.8},
        {"handle": "StaniKulechov", "name": "Stani Kulechov", "category": "defi", "weight": 0.85},
        {"handle": "RuneKek", "name": "Rune Christensen", "category": "defi", "weight": 0.8},
        {"handle": "haydenzadams", "name": "Hayden Adams", "category": "defi", "weight": 0.85},
        {"handle": "VanceSpencer", "name": "Vance Spencer", "category": "vc", "weight": 0.75},
        {"handle": "lopp", "name": "Jameson Lopp", "category": "bitcoin", "weight": 0.85},
        {"handle": "adam3us", "name": "Adam Back", "category": "bitcoin", "weight": 0.9},
        {"handle": "peterktodd", "name": "Peter Todd", "category": "bitcoin", "weight": 0.8},
        {
            "handle": "udiWertheimer",
            "name": "Udi Wertheimer",
            "category": "bitcoin",
            "weight": 0.75,
        },
        {"handle": "ercwl", "name": "Eric Wall", "category": "bitcoin", "weight": 0.75},
        {"handle": "CryptoKaleo", "name": "Kaleo", "category": "trading", "weight": 0.65},
        {
            "handle": "CryptoCapo_",
            "name": "il Capo of Crypto",
            "category": "trading",
            "weight": 0.6,
        },
        {"handle": "rektcapital", "name": "Rekt Capital", "category": "trading", "weight": 0.65},
        {"handle": "CryptoCred", "name": "Crypto Cred", "category": "trading", "weight": 0.7},
        {"handle": "TechDev_52", "name": "TechDev", "category": "trading", "weight": 0.65},
        {"handle": "saylor", "name": "Saylor Tracker", "category": "bitcoin", "weight": 0.7},
        {
            "handle": "DocumentingBTC",
            "name": "Documenting Bitcoin",
            "category": "bitcoin",
            "weight": 0.7,
        },
        {
            "handle": "BitcoinMagazine",
            "name": "Bitcoin Magazine",
            "category": "bitcoin",
            "weight": 0.85,
        },
        {"handle": "TheCryptoLark", "name": "Lark Davis", "category": "influencer", "weight": 0.6},
        {
            "handle": "AltcoinGordon",
            "name": "Altcoin Gordon",
            "category": "influencer",
            "weight": 0.55,
        },
        {
            "handle": "CryptoWendyO",
            "name": "Crypto Wendy O",
            "category": "influencer",
            "weight": 0.6,
        },
        {
            "handle": "girlgone_crypto",
            "name": "Girl Gone Crypto",
            "category": "influencer",
            "weight": 0.6,
        },
        {
            "handle": "aantonop",
            "name": "Andreas Antonopoulos",
            "category": "education",
            "weight": 0.9,
        },
        {"handle": "gavofyork", "name": "Gavin Wood", "category": "protocol", "weight": 0.9},
        {"handle": "ethereumJoseph", "name": "Joseph Lubin", "category": "ethereum", "weight": 0.8},
        {"handle": "Melt_Dem", "name": "Meltem Demirors", "category": "vc", "weight": 0.8},
        {"handle": "laurashin", "name": "Laura Shin", "category": "journalism", "weight": 0.85},
        {
            "handle": "iamjosephyoung",
            "name": "Joseph Young",
            "category": "journalism",
            "weight": 0.75,
        },
        {
            "handle": "ForbesCrypto",
            "name": "Forbes Crypto",
            "category": "journalism",
            "weight": 0.8,
        },
        {
            "handle": "BloombergCrypto",
            "name": "Bloomberg Crypto",
            "category": "journalism",
            "weight": 0.9,
        },
        {"handle": "WSJmarkets", "name": "WSJ Markets", "category": "journalism", "weight": 0.9},
        {"handle": "FT", "name": "Financial Times", "category": "journalism", "weight": 0.9},
        {"handle": "Reuters", "name": "Reuters", "category": "journalism", "weight": 0.95},
        {"handle": "DefiantNews", "name": "The Defiant", "category": "journalism", "weight": 0.85},
        {
            "handle": "unchained_pod",
            "name": "Unchained Podcast",
            "category": "journalism",
            "weight": 0.8,
        },
        {"handle": "BanklessHQ", "name": "Bankless", "category": "media", "weight": 0.8},
        {
            "handle": "trustmachinesco",
            "name": "Trust Machines",
            "category": "bitcoin",
            "weight": 0.7,
        },
        {"handle": "Stacks", "name": "Stacks", "category": "bitcoin", "weight": 0.75},
        {"handle": "SolanaFndn", "name": "Solana Foundation", "category": "solana", "weight": 0.85},
        {"handle": "SolanaStatus", "name": "Solana Status", "category": "solana", "weight": 0.8},
        {"handle": "base", "name": "Base", "category": "layer2", "weight": 0.8},
        {"handle": "arbitrum", "name": "Arbitrum", "category": "layer2", "weight": 0.8},
        {"handle": "optimismFND", "name": "Optimism", "category": "layer2", "weight": 0.8},
        {"handle": "0xPolygon", "name": "Polygon", "category": "layer2", "weight": 0.8},
        {"handle": "zksync", "name": "zkSync", "category": "layer2", "weight": 0.8},
        {"handle": "Starknet", "name": "Starknet", "category": "layer2", "weight": 0.8},
        {"handle": "Uniswap", "name": "Uniswap", "category": "defi", "weight": 0.85},
        {"handle": "AaveAave", "name": "Aave", "category": "defi", "weight": 0.8},
        {"handle": "LidoFinance", "name": "Lido", "category": "defi", "weight": 0.8},
        {"handle": "MakerDAO", "name": "MakerDAO", "category": "defi", "weight": 0.8},
        {"handle": "chainlink", "name": "Chainlink", "category": "oracle", "weight": 0.85},
        {"handle": "1inch", "name": "1inch", "category": "defi", "weight": 0.75},
        {"handle": "pendle_fi", "name": "Pendle", "category": "defi", "weight": 0.75},
        {"handle": "eigenlayer", "name": "EigenLayer", "category": "defi", "weight": 0.8},
        {"handle": "CelestiaOrg", "name": "Celestia", "category": "infra", "weight": 0.8},
        {"handle": "Avax", "name": "Avalanche", "category": "protocol", "weight": 0.8},
        {"handle": "SuiNetwork", "name": "Sui", "category": "protocol", "weight": 0.75},
        {"handle": "Aptos", "name": "Aptos", "category": "protocol", "weight": 0.75},
        {"handle": "NEARProtocol", "name": "NEAR", "category": "protocol", "weight": 0.75},
        {"handle": "cosmos", "name": "Cosmos", "category": "protocol", "weight": 0.8},
        {"handle": "injective", "name": "Injective", "category": "protocol", "weight": 0.7},
        {"handle": "SeiNetwork", "name": "Sei", "category": "protocol", "weight": 0.7},
        {"handle": "monad_xyz", "name": "Monad", "category": "protocol", "weight": 0.75},
        {"handle": "berachain", "name": "Berachain", "category": "protocol", "weight": 0.7},
        {"handle": "Crypto_News", "name": "Crypto News", "category": "aggregator", "weight": 0.7},
        {"handle": "crypto_banter", "name": "Crypto Banter", "category": "media", "weight": 0.6},
        {"handle": "AltcoinDailyio", "name": "Altcoin Daily", "category": "media", "weight": 0.6},
        {"handle": "Coinbureau", "name": "Coin Bureau", "category": "education", "weight": 0.75},
    ],
}


def _merge_influential_accounts(accounts: list[dict[str, Any]]) -> None:
    """Merge registry accounts into CT_ACCOUNTS, dedup on lowercased handle.

    Existing curated accounts keep their metadata (name/category/weight);
    the registry only fills in optional enrichments (farcaster, tier,
    platforms) for them. New handles are appended under the
    ``influencer_registry`` tier.
    """
    if not accounts:
        return
    existing = {a["handle"].lower(): a for tier in CT_ACCOUNTS.values() for a in tier}
    registry_tier = CT_ACCOUNTS.setdefault("influencer_registry", [])
    added = 0
    for acc in accounts:
        handle = str(acc.get("handle", "")).lower().lstrip("@")
        if not handle:
            continue
        cur = existing.get(handle)
        if cur is not None:
            # Keep curated metadata; absorb optional registry enrichments.
            for k in ("farcaster", "tier", "platforms"):
                if k not in cur and acc.get(k) is not None:
                    cur[k] = acc[k]
            continue
        entry: dict[str, Any] = {
            "handle": acc.get("handle"),
            "name": acc.get("name") or acc.get("handle"),
            "category": acc.get("category") or "influencer",
            "weight": float(acc.get("weight") or 0.7),
        }
        for k in ("farcaster", "tier", "platforms", "verified"):
            if acc.get(k) is not None:
                entry[k] = acc[k]
        registry_tier.append(entry)
        existing[handle] = entry
        added += 1
    if added:
        logger.info(f"Merged {added} influencer-registry accounts into CT tracking")


INFLUENTIAL_ACCOUNTS = load_influential_accounts()
_merge_influential_accounts(INFLUENTIAL_ACCOUNTS)

ALL_CT = [a for tier in CT_ACCOUNTS.values() for a in tier]
CT_HANDLES = [a["handle"].lower() for a in ALL_CT]

# Merged tracked-account list (curated + registry) shared with
# social_scraper / social_intel for post-scraping.
TRACKED_ACCOUNTS: list[dict[str, Any]] = [dict(a) for a in ALL_CT]

GROQ_KEY = resolve_key("GROQ_API_KEY")
