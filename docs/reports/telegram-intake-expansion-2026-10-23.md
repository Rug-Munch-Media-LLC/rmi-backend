# Telegram intake expansion — high-value channels (2026-10-23)

Probe method: unauthenticated `GET https://t.me/s/<handle>` with the public preview
parser in `app/databus/tg_news/parse.py`. 144 handles probed; **41** expose a public
preview, **103** 302-redirect to the plain channel page (preview disabled). No join,
no account session, no auth.

`role` = measured, not assumed: `primary` (own reporting/data, links the source out),
`aggregator` (restates others, ≤20 % outbound links), `shill` (promotion-first).
Cadence is derived from the preview page's own timestamps, so it reflects the last
page only — treat as an order of magnitude, not a rate.

## Candidates + evidence

| handle | cat | t.me/s status | subs | posts/pg | cadence/h | link% | fwd% | role | sample headlines |
|---|---|---|---|---|---|---|---|---|---|
| `binance_announcements` | market/exchange | 200 | 3,290,000 | 20 | 0.1 | 100% | 0% | primary | “Binance Alpha Trading Competition: Trade Orochi Network (ON) and Share” · “Binance Starter Carnival: Trade bStocks to Share Up to 60,000 USDT in” |
| `cointelegraph` | market news | 200 | 329,000 | 15 | 0.4 | 100% | 0% | primary | “🏀 WATCH: NBA champion Tristan Thompson reveals his biggest crypto trad” · “🇺🇸 BIG: Morgan Stanley, Bank of America, JPMorgan and Wells Fargo are” |
| `whale_alert_io` | on-chain | 200 | 297,000 | 20 | 2.0 | 100% | 0% | primary | “🚨 🚨 🚨 628 $BTC (51,164,502 USD) transferred from Coinbase Institutiona” · “🚨 🚨 🚨 628 $BTC (51,157,119 USD) transferred from Coinbase Institutiona” |
| `watcherguru` | market news | 200 | 628,000 | 12 | 0.6 | 0% | 0% | aggregator | “JUST IN: Gold and Silver are dumping. @WatcherGuru” · “JUST IN: 🇺🇸 Nvidia $NVDA CEO Jensen Huang supports California's propos” |
| `bwenews` | market news | 200 | 72,300 | 20 | 0.1 | 50% | 0% | primary | “Tree News: A16Z DROPS "CRYPTO" FROM FUND NAME, REBRANDS TO "A16Z FRONT” · “AggrNews: *VISA IN TALKS TO ACQUIRE CRYTPO CUSTODY FIRM FIREBLOCKS FOR” |
| `blockchainreporter` | market news | 200 | 54,200 | 20 | 0.5 | 0% | 0% | aggregator | “What Are the Top Benefits of Using Stablecoins in Remittance Services?” · “US Restrained $938M in Scam-Linked Crypto — Why 'Restrained' Isn't 'Re” |
| `glassnode` | research | 200 | 43,800 | 15 | 0.1 | 33% | 0% | aggregator | “BTC treasury companies are in profit but barely buying. They hold $BTC” · “$BTC is testing the ETF cost basis around $81K again. The same level c” |
| `TreeNewsFeed` | market news | 200 | 34,800 | 20 | 0.0 | 20% | 0% | aggregator | “Citi Partners With Coinbase to Enable Stablecoin Payments for Institut” · “U.S. CPI: +3.7% YEAR-OVER-YEAR (EST. +3.6%) U.S. CORE CPI: +2.6% YEAR-” |
| `coingape` | market news | 200 | 23,200 | 17 | 1.8 | 100% | 0% | primary | “🚨 Ripple Partners With Arf to Boost RLUSD Liquidity Arf will use RLUSD” · “🚨 SpaceX Stock Drops 5% as $200B in Shares Unlock SpaceX shares fell d” |
| `spotonchain` | on-chain | 200 | 21,300 | 20 | 0.0 | 90% | 0% | primary | “979× BTC short. +202.54% return on margin. This Maneki earned the shad” · “We opened Manekora’s private beta to 30 people two days ago, and it’s” |
| `cryptobriefing` | market news | 200 | 9,550 | 20 | 0.7 | 100% | 0% | primary | “SoFi launches tokenized bank deposits offering 4.4% yield and FDIC ins” · “Major US banks detect unauthorized third-party AI agents in customer a” |
| `coinedition` | market news | 200 | 2,410 | 20 | 2.6 | 90% | 0% | primary | “Oil Price Manipulation or War Premium? Why WTI Lags Brent Above $111 🏷” · “Italian Police Bust Mafia Laundering Ring Moving €68 Million in Crypto” |
| `intotheblock` | on-chain | 200 | 1,530 | 20 | 0.0 | 90% | 0% | primary | “https://x.com/SentoraHQ/status/2085004576084672759?s=20” · “Sentora just announced Earn vaults for xStocks on kraken https://x.com” |
| `newsbtc` | market news | 200 | 973 | 20 | 0.0 | 100% | 0% | primary | “🚨 LATEST: Senator Cynthia Lummis Leads Senate Push for Fair Digital As” · “🚨 LATEST: ¥1T SBI Shinsei Bank Launches Crypto Rewards for Depositors.” |
| `laevitas` | research | 200 | 678 | 20 | 5.5 | 95% | 0% | primary | “SHORT STRANGLE Deribit · BTC · BLOCK TRADE 22 Oct 2026 17:17:41 UTC 🔴” · “Opened SHORT PUT Deribit · BTC · BLOCK TRADE · PARADIGM 22 Oct 2026 17” |
| `coinpedia` | market news | 200 | 367 | 20 | 0.0 | 85% | 15% | primary | “https://www.pairbots.net/register?ref=wallet” · “https://www.pairbots.net/register?ref=wallet Three trading bots. 1.2–2” |
| `PANewsOfficial` | market news | not probed | — | — | — | — | — | unknown | — |
| `AirdropAlert` | shill | 200 | 2,760 | 20 | 0.2 | 90% | 0% | primary | “📢 IMPORTANT ANNOUNCEMENT 📢 Dear Community, We've decided to consolidat” · “Airdrop Alert pinned « 📢 IMPORTANT ANNOUNCEMENT 📢 Dear Community, We'v” |
| `immunefi` | security | 200 | 1,630 | 20 | 0.0 | 95% | 0% | primary | “New bug bounty: https://immunefi.com/bug-bounty/zksync-os/information/” · “Newly re-launched bug bounty from Bitflow is live: https://immunefi.co” |
| `rektnews` | security | 200 | 6,910 | 20 | 0.0 | 90% | 0% | primary | “Hello Julien. In an unreciprocated gesture of goodwill, I deleted the” · “Channel name was changed to « fka-rekt »” |
| `theblock` | market news | 200 | 79 | 20 | 0.0 | 0% | 0% | aggregator | “Tg username @Theblock for sale Dm @me23z Safety deal #theblock” · “Tg username @Theblock for sale Dm @me23z Safety deal #theblock” |
| `CertiKAlert` | security | 200 | 48 | 9 | 0.8 | 78% | 0% | primary | “🚨 Psyduck inu: Chain: ETH CONTRACT: 0x442d0bfc4e1450d3a8b29d4397e94633” · “🚨 Smolinu: Chain: ETH CONTRACT: 0xd21a75e8200e4fc2ff2929d150a3f7219b11” |
| `PeckShieldAlert` | security | 200 | 46 | 1 | 1.0 | 0% | 0% | aggregator | “Channel created” |
| `arkham_intel` | on-chain | 200 | 12 | 2 | 0.0 | 0% | 0% | aggregator | “Channel created” · “Channel name was changed to « * »” |
| `dlnews` | market news | 200 | 3 | 2 | 2.0 | 0% | 0% | aggregator | “Channel created” · “” |
| `whale_alert` | on-chain | 200 | 14,000 | 20 | 1.3 | 95% | 0% | primary | “5,000,000 #USDT (5,025,507 USD) transferred from unknown wallet to #Bi” · “6,500,000 #USDT (6,529,776 USD) transferred from unknown wallet to #Bi” |
| `SlowMist` | security | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: SlowMist) | — |
| `PeckShield` | security | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (PeckShield) | — |
| `BlockSecTeam` | security | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (BlockSec) | — |
| `CertiK` | security | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Mr. Mister😺) | — |
| `ScamSniffer` | security | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Scam Sniffer Official) | — |
| `SEAL_911` | security | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (SEAL 911) | — |
| `DefiLlama` | on-chain | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Alex - DefiLlama) | — |
| `dune` | on-chain | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: dune) | — |
| `Nansen` | on-chain | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: Nansen) | — |
| `kaiko` | research | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Saeed) | — |
| `chainalysis` | research | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: chainalysis) | — |
| `messaricrypto` | research | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Messari) | — |
| `lookonchain` | on-chain | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Lookonchain) | — |
| `coindesk` | market news | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Crypto news feed) | — |
| `WuBlockchain` | market news | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: WuBlockchain) | — |
| `JupiterExchange` | solana | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Jupiter Exchange (Official Support) | — |
| `DriftProtocol` | solana | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: DriftProtocol) | — |
| `HyperliquidX` | solana | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (Hyperliquid) | — |
| `jito_sol` | solana | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: jito_sol) | — |
| `EleanorTerrett` | regulation | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: EleanorTerrett) | — |
| `CFTC` | regulation | **302 → preview closed** | — | 0 | n/a | n/a | n/a | not ingestible (squat/empty: CFTC) | — |

Notable handle-squats found (preview closed, page is a personal account — never ingest):
`@CertiK` → “Mr. Mister😺”, `@kaiko` → “Saeed”, `@SECGov` → “k j”, `@coindesk` → a
159-sub “Crypto news feed”, `@theblock` (79 subs, dead since 2024-10) vs the real
`TheBlock__`, `@Bankless` → “placeholder by Bankless DACH”, `@JupiterExchange` →
“Official Support” (support-scam pattern).

## Trust weights (stored with the source)

`classify.KIND_WEIGHTS`: **primary 1.0 · aggregator 0.35 · shill 0.0 · unknown 0.5**.
Persisted per channel in `<channel>.state.json` (`trust_weight`) and attached to every
DataBus article as `source_weight`, with `rank_score = trust_score × source_weight`,
so Rugbrain ranks without re-deriving anything.

## Ingested (15, public-preview scraper, same state/dedupe path)

`binance_announcements`, `cointelegraph`, `whale_alert_io`, `bwenews`, `glassnode`,
`spotonchain`, `coingape`, `cryptobriefing`, `coinedition`, `intotheblock`, `newsbtc`,
`laevitas`, `coinpedia`, `watcherguru` (0.35), `TreeNewsFeed` (0.35).

Plus the existing cluster (`news_crypto` 0.35 + `Fin_Watch`, `ETF_FLOWS`, `BTC_Tick`,
`Bitcoin_FGI`) and `PANewsOfficial` (auto-discovered from the registry, 100 % linked).
`blockchainreporter` is reachable and classified aggregator (0.35) — ingested by the
default run, kept out of the top-15 because it publishes no outbound links.

## Skipped, and why

* **Preview closed (302)** — `SlowMist`, `PeckShield`, `BlockSecTeam`, `CertiK`,
  `ScamSniffer`, `SEAL_911`, `MistTrack`, `DefiLlama`, `dune`, `Nansen`, `kaiko`,
  `chainalysis`, `messaricrypto`, `lookonchain`, `JupiterExchange`, `DriftProtocol`,
  `HyperliquidX`, `jito_sol`, `coindesk`, `WuBlockchain`, `bitcoinmagazine`,
  `EleanorTerrett`, `CFTC`, `code4rena`, `GoPlusSecurity` … (72 total, in
  `verified_channels.PREVIEW_OFF`). Security-intel breadth is **not obtainable** from
  the public preview — those channels must stay on the userbot/registry path.
* **Dead on Telegram** — `rektnews` (last post 2024-06), `whale_alert` (2020),
  `CertiKAlert` (2021), `PeckShieldAlert` (2022), `immunefi` (2025-11), `AirdropAlert`
  (2025-04). Presence ≠ liveness; cadence check is what caught them.
* **Shill** — `AirdropAlert` (90 % promo links): `role=shill`, weight 0.0, held in
  `MONITORED_ONLY`, never ingested as news.

## Proof

* Run A (22 channels): **1,980 items** in the store, 81 appended, 22/22 ok.
* Run B (same set, state from A): **0 appended, 0 new, 23/23 `caught_up`** — the
  `last_id` cursor stops at known history after one page.
* 5 stored articles: `coingape` w=1.0 trust=40 rank=40 · `BlockchainReporter` w=0.35
  trust=40 rank=14 · `laevitas` w=1.0 trust=70 rank=70 · `cryptobriefing` w=1.0
  trust=70 rank=70 · `whale_alert_io` w=1.0 trust=65 rank=65.
* `ruff` clean, `mypy` clean (11 files), `pytest tests/test_tg_news_preview.py` 6 passed.

[REVIEW] Cadence for low-volume channels is computed from one preview page; treat
`cadence/h` as a ceiling. Weights are a proposal until Rugbrain's ranking shows a
measurable lift.
