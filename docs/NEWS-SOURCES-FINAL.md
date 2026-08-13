# Crypto News Sources — Final Registry

- **Generated:** 2026-08-13 12:20 UTC
- **Registry:** `app/databus/unified_news_sources.json`
- **Previous probe (10:39 UTC):** 255 sources → 94 VERIFIED / 144 DEAD / 17 STALE

## Summary

| Metric | Count |
|--------|------:|
| **Total live sources (final)** | **217** |
| Verified RSS/feed sources | 164 |
| JS-crawl sources (kept, `crawl: true`) | 53 |
| Pruned (truly dead) | 48 |
| Original probed | 255 |

Live total = verified + JS-crawl (both kept in registry). 48 dead sources removed.

## Categories (live)

| Category | Sources |
|----------|--------:|
| crypto_media | 48 |
| crypto_news | 38 |
| social | 33 |
| general_finance | 27 |
| security | 19 |
| defi | 16 |
| global_news | 15 |
| onchain | 11 |
| regulation | 6 |
| nft_gaming | 4 |

## JS-Crawl List (`crawl: true`)

Sources that return HTML or are bot-blocked (no directly parseable RSS) — kept with a `crawl: true` flag so the proxy/Crawl4AI ladder can render them. Count: 53

| id | reason |
|----|--------|
| binance-com | HTTP 202 HTML page (JS-only) |
| blockchain-berkeley-edu | HTTP 200 HTML page (JS-only) |
| blog-base-org | HTTP 403 bot-blocked |
| blog-bybit-com | HTTP 403 bot-blocked |
| blog-coinbase-com | HTTP 403 bot-blocked |
| blog-infura-io | HTTP 200 HTML page (JS-only) |
| blog-injective-com | HTTP 200 HTML page (JS-only) |
| blog-ledger-com | HTTP 401 bot-blocked |
| blog-makerdao-com | HTTP 200 HTML page (JS-only) |
| blog-sui-io | HTTP 200 HTML page (JS-only) |
| blog-tenderly-co | HTTP 429 bot-blocked |
| cfpb | HTTP 403 bot-blocked |
| cftc-gov | HTTP 403 bot-blocked |
| chainlink-blog | HTTP 200 HTML page (JS-only) |
| coinbase-com | HTTP 403 bot-blocked |
| coingecko-buzz | HTTP 403 bot-blocked |
| coinmetrics-io | HTTP 200 HTML page (JS-only) |
| consensys-io | HTTP 403 bot-blocked |
| cryptoadventure | HTTP 403 bot-blocked |
| defillama | HTTP 403 bot-blocked |
| dragonfly-xyz | HTTP 200 HTML page (JS-only) |
| dune-analytics | HTTP 403 bot-blocked |
| fatf-gafi-org | HTTP 403 bot-blocked |
| gate-io | HTTP 403 bot-blocked |
| gemini-com | HTTP 200 HTML page (JS-only) |
| marketwatch-crypto | HTTP 403 bot-blocked |
| messari | HTTP 429 bot-blocked |
| near-org | HTTP 200 HTML page (JS-only) |
| playtoearn-net | HTTP 403 bot-blocked |
| polkadot-blog | HTTP 200 HTML page (JS-only) |
| reddit-bitcoin | HTTP 429 bot-blocked |
| reddit-buttcoin | HTTP 429 bot-blocked |
| reddit-crypto_general | HTTP 429 bot-blocked |
| reddit-cryptocurrency | HTTP 429 bot-blocked |
| reddit-cryptocurrencytrading | HTTP 429 bot-blocked |
| reddit-cryptogaming | HTTP 429 bot-blocked |
| reddit-cryptomarkets | HTTP 429 bot-blocked |
| reddit-cryptomoonshots | HTTP 429 bot-blocked |
| reddit-cryptoscams | HTTP 429 bot-blocked |
| reddit-cryptotechnology | HTTP 429 bot-blocked |
| reddit-cryptotrading | HTTP 429 bot-blocked |
| reddit-defi | HTTP 429 bot-blocked |
| reddit-ethdev | HTTP 429 bot-blocked |
| reddit-ethereum | HTTP 429 bot-blocked |
| reddit-ethfinance | HTTP 429 bot-blocked |
| reddit-layer2 | HTTP 429 bot-blocked |
| reddit-nft | HTTP 429 bot-blocked |
| reddit-satoshistreetbets | HTTP 429 bot-blocked |
| reddit-solana | HTTP 429 bot-blocked |
| reddit-web3 | HTTP 429 bot-blocked |
| starknet-io | HTTP 200 HTML page (JS-only) |
| variant-fund | HTTP 403 bot-blocked |
| wired-crypto | HTTP 400 bot-blocked |

## Pruned (truly dead, removed from registry)

No RSS/feed exists at all: HTTP 404/410, DNS-dead hosts, repeated timeouts, SSL host-mismatch, or nitter 404. Count: 48

| id | reason |
|----|--------|
| 1confirmation-com | HTTP 404 - no feed/site |
| a16z-crypto | HTTP 404 - no feed/site |
| ap-crypto | HTTP 404 - no feed/site |
| avax-network | HTTP 404 - no feed/site |
| blockanalytica-substack-com | HTTP 404 - no feed/site |
| blog-1inch-io | HTTP 410 - no feed/site |
| blog-alchemy-com | HTTP 404 - no feed/site |
| blog-cosmos-network | connection timeout (2x) |
| blog-curve-fi | DNS dead host (no address) |
| blog-dydx-exchange | DNS dead host (no address) |
| blog-eigenlayer-xyz | SSL cert mismatch (host moved) |
| blog-gmx-io | DNS dead host (no address) |
| blog-kucoin-com | DNS dead host (no address) |
| blog-linea-build | DNS dead host (no address) |
| blog-optimism-io | DNS dead host (no address) |
| blog-pendle-finance | DNS dead host (no address) |
| blog-scroll-io | DNS dead host (no address) |
| blog-solidified-io | DNS dead host (no address) |
| blog-spartangroup-io | DNS dead host (no address) |
| cbr-stanford-edu | HTTP 404 - no feed/site |
| cmsholdings-co | DNS dead host (no address) |
| coincodex | HTTP 404 - no feed/site |
| coinmarketcap | HTTP 404 - no feed/site |
| crypto-com | HTTP 404 - no feed/site |
| cyber-stanford-edu | HTTP 404 - no feed/site |
| dci-mit-edu | HTTP 404 - no feed/site |
| defi-pulse | connection timeout (2x) |
| electriccapital-com | HTTP 404 - no feed/site |
| fincen-gov | HTTP 404 - no feed/site |
| forbes-digital-assets | HTTP 404 - no feed/site |
| kaiko | HTTP 404 - no feed/site |
| lightning-blog | HTTP 404 - no feed/site |
| nansen-ai | HTTP 404 - no feed/site |
| okx-com | HTTP 404 - no feed/site |
| paradigm-xyz | HTTP 404 - no feed/site |
| placeholder-vc | HTTP 404 - no feed/site |
| polygon-technology | HTTP 404 - no feed/site |
| reuters-crypto | HTTP 404 - no feed/site |
| reuters-tech | HTTP 404 - no feed/site |
| src-ac-uk | HTTP 404 - no feed/site |
| the-crypto-times | DNS dead host (no address) |
| thetie-is | DNS dead host (no address) |
| tokenterminal-com | HTTP 404 - no feed/site |
| tradingview-com | HTTP 500 (no feed) |
| twitter-bankless | HTTP 404 - no feed/site |
| twitter-duneanalytics | HTTP 404 - no feed/site |
| twitter-theblock | HTTP 404 - no feed/site |
| uniswap-org | HTTP 404 - no feed/site |

## Verification Method

- Live re-probe of all 265 URLs (browser UA, 12s timeout, 10 parallel) on 2026-08-13 against **current** registry URLs.
- Cross-checked against 10:39 UTC probe status + Webshare proxy re-probe from the URL-fix commit (`03420e25`).
- 200+XML → verified; 200+HTML or 4xx/429/202 blocked → `crawl: true`; 404/410/DNS/timeout/SSL/500 → pruned.

## Notes / Review

- 12 of 15 nitter URLs returned valid `application/rss+xml` on re-probe (and at 10:39 UTC). Only 3 nitter accounts 404 — pruned. [REVIEW] 'nitter dead platform-wide' was not observed; keep nitter unless platform is confirmed dead.
- `paragraph-xyz` feed is live (paragraph.com moved; lastBuildDate 2026-08-13) — kept, not pruned.
- `bitcoinmagazine` returns 403 on direct probe but was VERIFIED with fresh items at 10:39 UTC — kept verified.
- `cfpb`/`cftc-gov`/`variant-fund` were marked verified by the proxy re-probe but are 403/HTML on direct fetch — re-flagged `crawl: true`.
