# KOL-Tooling Sources — X/Browser Extensions That Track KOL Wallets & Shills

> Research date: 2026-08-13. Compiled via direct site fetch (websearch was down).
> Purpose: identify tools that overlay "this KOL shilled this token" / KOL PnL data on X,
> and document their data sources + methodology for potential integration into RugMunch.

## Tier 1 — X overlay / KOL-wallet extensions (closest to product fit)

| Tool | Type | What it does | Data source / methodology |
|------|------|--------------|---------------------------|
| **Frontrun** (frontrun.pro) | Chrome extension | Adds wallet intelligence directly to X/Twitter profiles: see which VCs, KOLs, whales and "smart money" follow a wallet; track contract-address (CA) history and bio changes on any X profile. 20M+ profiles tracked, 6M+ wallet labels, 50K+ Solana traders. Backed by Alliance + YZi Labs. | Labeled wallet registry (on-chain attribution) + X following-graph enrichment: overlays account↔wallet relations by analyzing who-follows-which-wallet and CA/bio change history. |
| **XHunt** | Chrome extension (open-source, AI) | Turns X into a crypto KOL intelligence & trading hub; KOL identification, wallet overlays. Rating 4.6/5, 28 votes (chrome-stats: gonmfafjcdkngkbhcpmcphlgfhabkeji). | AI-assisted parsing of X accounts + linked on-chain data. [REVIEW] store page not fetchable this session (403); description from search-engine snippet. |
| **OmniCrypto** | Chrome extension (AI) | Aggregates trending topics, project details, KOL identification in one X-sidebar tool. Rating 4.7/5, 15 votes (chrome-stats: anfhckfacchppnodiocnmhcjdgbhahmd). | Social-graph + news/project enrichment. [REVIEW] same fetch limitation. |

## Tier 2 — KOL wallet trackers (web/API, no X overlay)

| Tool | Type | What it does | Data source / methodology |
|------|------|--------------|---------------------------|
| **Kolscan** (kolscan.io) | Web/leaderboard | Solana wallet tracker monitoring top memecoin traders & KOLs: real-time transactions, token PnL, performance leaderboard. | On-chain tx parsing (Solana RPC/indexer) per labeled KOL wallet; realized/paper PnL computed from swap history. |
| **CabalSpy** (cabalspy.xyz) | REST/WS API + widget | Labeled wallet intelligence: KOL, Smart Money, Whale, Insider wallets across Solana, BNB, Base, ETH, Monad. 700+ KOL and 250+ Smart Money wallets on Solana. | Manual/curated label registry + on-chain activity tracking; "cabal" = coordinated groups of influencers/MMs/devs moving prices. |
| **GMGN KOL Monitor** (Apify actor) | API/actor | Tracks top crypto influencers' real-time token data, trades & wallet movements on BSC, Solana, Base. | X-account→wallet mapping + swap-indexer; per-token buy/sell timestamps. |
| **Cielo** (cielo.finance) | Web app + bots | On-chain wallet tracking: PnL profiles, wallet discovery, real-time Telegram/Discord alerts, 30+ chains, 4M+ wallets tracked. | Chain indexers + wallet profile scoring; no X overlay extension (alerts via bots). |
| **1322.io** | WebSocket feed | Real-time crypto-Twitter KOL monitoring (150–250ms), full post payloads, Discord/Telegram delivery. | X/Twitter stream ingestion. |
| **Shill** (shill.website) | Web | "Fastest crypto twitter tracker": monitor influencers, communities, custom feeds in real-time. | X stream ingestion. |
| **PumpTracker** (pumptracker.io) | Web (community) | Tracks pump trades of popular X Solana memecoin KOLs. | Community-curated KOL wallet list + swap indexer. |

## Tier 3 — KOL authenticity scoring & fraud signal

| Tool | Type | What it does | Data source / methodology |
|------|------|--------------|---------------------------|
| **KolClaw** (via Phemex News, 2026-02-07) | AI agent (Base) | Monitors 99+ Solana KOL wallets 24/7 and scores whether influencers genuinely invest in tokens they promote. Five on-chain metrics: **Consistency, Rug Avoidance, Timing, Holding, PnL Honesty** → composite 0–100 → "Verified Authentic"…"Likely Fraudulent". Telegram bot + Moltbook. | On-chain behavior analysis vs. X post timing; per-KOL metric extraction. |
| **Pocket Universe** | Chrome extension | Warns on malicious transactions; "Rug Detection — get warned about serial rugpullers" on popular trading platforms. | Signature simulation + labeled serial-rugpuller registry. |
| **Scam Sniffer** (scamsniffer.io) | Extension + API | Real-time blocklists: 258K+ phishing domains, wallet drainers, scammer addresses; verified pipeline (47M URLs scanned, 705K scams identified). | Multi-source detection → automated verification + human review → blocklist; queried by Chainalysis, Phantom, Rabby, Bybit, Revoke.cash. |
| **AegisWeb3** (PeckShield) | Chrome extension | Anti-phishing, tx simulation, approval revoke, smart-contract examination (honeypot/liquidity), 1.18M scam tokens identified. | ML scanning + contract analysis. |

## Useful research directories
- **kolscams.fun** — "KOLSCAM – Stay Safe in Crypto": directory of crypto influencers/traders involved in scams/fraud; e.g. references ZachXBT (Oct 2024) flagging an account that lied 3x about $UPTOBER allocation (wallet traced via Solana Saga airdrop link). Good seed list for registry expansion; entries need independent verification before `verified:true`.
- **ZachXBT Sep 2025 paid-promo leak**: 160–200+ influencers accepted undisclosed paid promos; leaked price sheet with Solana payment wallets, tiers $50–$60K/post. Source articles: disruptionbanking.com/2025/09/12/..., bitcoinist.com/crypto-influencers-insane-hidden-payouts-exposed/, parameter.io/zachxbt-exposes-massive-crypto-influencer-payment-scandal/. Individual names live in the leaked sheet, not machine-readable yet — [REVIEW] scrape needed.

## Methodology notes for RugMunch integration
1. **Label registries are the core primitive** — all tools start from a curated KOL→wallet map; the differentiator is PnL accuracy + X-post correlation.
2. **X-overlay gap**: Frontrun is currently the closest to "this KOL shilled this token" overlays; it's an extension, not an API — partnership/scraping considerations apply.
3. **Authenticity scoring** (KolClaw) is a proven 5-metric model we could replicate on our own KOL list (we already have the data sources: DexScreener/Birdeye/Helius + X ingestion).
4. None of these tools expose a public REST API for "KOL shill history" — a market gap RugMunch's databus can fill.
5. [REVIEW] Chrome Web Store pages were not fetchable this session (bot-wall); store-listed tools (XHunt, OmniCrypto, X-Alpha) verified only via chrome-stats/search snippets.
