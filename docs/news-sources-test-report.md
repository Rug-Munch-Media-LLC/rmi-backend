# Crypto News Sources — Test Report

- **Generated:** 2026-08-13T10:39:44.598033+00:00
- **Source file:** `app/databus/unified_news_sources.json`
- **Total sources probed:** 255
- **Freshness window:** 30 days

## Summary

- **VERIFIED** (returns fresh items): **94**
- **DEAD** (connection/parse error): **144**
- **STALE** (returns items, but old): **17**

Status map: `app/databus/news_sources_status.json`

## VERIFIED Sources

94 sources returned fresh items. IDs: ambcrypto, bankless-com, bbc-business, bbc-tech, beincrypto, bi-crypto, bing-blockchain, bing-crypto, bitcoin-com, bitcoinist, bitcoinmagazine, bleepingcomputer-com, blockonomi-com, blog-arbitrum-io, blog-bitfinex-com, blog-celestia-org, blog-chainalysis-com, blog-kraken-com, blog-lido-fi, blog-mexc-com, bloomberg-tech, certik-medium-com, chainalysis, cnbc-crypto, coindesk, coinjournal, coinpedia, coinspeaker-com, cointelegraph, collective-flashbots-net, crypto-daily, crypto-news, crypto-news-flash, crypto-reporter, cryptobriefing, cryptonews, cryptopolitan, cryptopotato, cryptoslate, dailycoin-com, dailyhodl, darkreading-com, decrypt, economist-fintech, ethereum-blog, ethereum-magicians-org, ethresear-ch, feeds-feedburner-com, finbold-com, forum-makerdao-com, forum-openzeppelin-com, ft-tech, glassnode, google-crypto, google-defi, google-nft, google-regulation, gov-uniswap-org, guardian-crypto, krebsonsecurity-com, newsbtc, nftevening-com, nftplazas-com, nulltx-com, nyt-business, nyt-technology, protos, reddit-altcoin, scmp-crypto, sec-gov, slowmist, solana-news, techcrunch-com, techcrunch-crypto, the-defiant, trailofbits, trustnodes, twitter-coindesk, twitter-cointelegraph, twitter-cryptokoryo, twitter-cryptorank, twitter-decryptmedia, twitter-defiignas, twitter-defillama, twitter-glassnode, twitter-lookonchain, twitter-messari, twitter-watanglass, twitter-whale_alert, u-today, unchained, venturebeat-com, web3isgoinggreat-com, zycrypto

## DEAD Sources (144)

| id | type | item_count | last_good | error |
|----|------|-----------:|-----------|-------|
| 1confirmation-com | rss | 0 | - | HTTP 404 |
| a16z-crypto | rss | 0 | - | HTTP 404 |
| aave-com | rss | 0 | - | HTTP 404 |
| aljazeera-tech | rss | 0 | - | HTTP 404 |
| ap-crypto | rss | 0 | - | HTTP 404 |
| ars-crypto | rss | 0 | - | HTTP 404 |
| avax-network | rss | 0 | - | HTTP 404 |
| bankless | rss | 0 | - | fetch error: ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'newsletter.banklesshq.com'. (_ssl.c:1000) |
| banklessdao-substack-com | rss | 0 | - | HTTP 403 |
| binance-com | rss | 0 | - | HTTP 202 |
| bitcoin-ops | rss | 0 | - | HTTP 404 |
| blockanalytica-substack-com | rss | 0 | - | HTTP 403 |
| blockchain-berkeley-edu | rss | 0 | - | parse error: no entries in feed |
| blog-1inch-io | rss | 0 | - | HTTP 410 |
| blog-alchemy-com | rss | 0 | - | HTTP 404 |
| blog-balancer-fi | rss | 0 | - | fetch error: ConnectError: [Errno -3] Temporary failure in name resolution |
| blog-base-org | rss | 0 | - | HTTP 403 |
| blog-bitquery-io | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| blog-bybit-com | rss | 0 | - | fetch error: ReadTimeout: The read operation timed out |
| blog-chainstack-com | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-coinbase-com | rss | 0 | - | HTTP 403 |
| blog-compound-finance | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-cosmos-network | rss | 0 | - | fetch error: ConnectTimeout: timed out |
| blog-curve-fi | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-dydx-exchange | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-eigenlayer-xyz | rss | 0 | - | fetch error: ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'blog.eigencloud.xyz'. (_ssl.c:1000) |
| blog-gmx-io | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-hexens-io | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-infura-io | rss | 0 | - | parse error: no entries in feed |
| blog-injective-com | rss | 0 | - | parse error: no entries in feed |
| blog-intotheblock-com | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| blog-kucoin-com | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| blog-ledger-com | rss | 0 | - | HTTP 401 |
| blog-linea-build | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-makerdao-com | rss | 0 | - | parse error: no entries in feed |
| blog-morpho-org | rss | 0 | - | fetch error: ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1000) |
| blog-optimism-io | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-pendle-finance | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-santiment-net | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| blog-scroll-io | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| blog-solidified-io | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| blog-spartangroup-io | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| blog-sui-io | rss | 0 | - | parse error: no entries in feed |
| blog-tenderly-co | rss | 0 | - | HTTP 429 |
| blog-venus-io | rss | 0 | - | fetch error: ConnectError: [Errno -5] No address associated with hostname |
| bloomberg-crypto | rss | 0 | - | HTTP 404 |
| cbr-stanford-edu | rss | 0 | - | HTTP 404 |
| cfpb | rss | 0 | - | parse error: no entries in feed |
| cftc-gov | rss | 0 | - | parse error: no entries in feed |
| chainlink-blog | rss | 0 | - | parse error: no entries in feed |
| cmsholdings-co | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| coinbase-com | rss | 0 | - | HTTP 403 |
| coincodex | rss | 0 | - | HTTP 404 |
| coingecko-buzz | rss | 0 | - | HTTP 403 |
| coinmarketcap | rss | 0 | - | HTTP 404 |
| coinmetrics-io | rss | 0 | - | parse error: no entries in feed |
| consensys-io | rss | 0 | - | HTTP 403 |
| crypto-com | rss | 0 | - | HTTP 404 |
| cryptoadventure | rss | 0 | - | HTTP 403 |
| cryptohayes-substack-com | rss | 0 | - | HTTP 403 |
| cryptosecurity-substack-com | rss | 0 | - | HTTP 403 |
| cyber-stanford-edu | rss | 0 | - | HTTP 404 |
| dappradar-com | rss | 0 | - | parse error: no entries in feed |
| dci-mit-edu | rss | 0 | - | HTTP 404 |
| dedaub-com | rss | 0 | - | HTTP 404 |
| defi-pulse | rss | 0 | - | fetch error: ConnectTimeout: timed out |
| defieducation-substack-com | rss | 0 | - | HTTP 403 |
| defillama | rss | 0 | - | HTTP 403 |
| doseofdefi-substack-com | rss | 0 | - | HTTP 403 |
| dragonfly-xyz | rss | 0 | - | parse error: no entries in feed |
| dune-analytics | rss | 0 | - | HTTP 403 |
| electriccapital-com | rss | 0 | - | HTTP 404 |
| esma-europa-eu | rss | 0 | - | HTTP 404 |
| fatf-gafi-org | rss | 0 | - | HTTP 403 |
| fincen-gov | rss | 0 | - | fetch error: ReadTimeout: The read operation timed out |
| forbes-crypto | rss | 0 | - | HTTP 404 |
| forbes-digital-assets | rss | 0 | - | HTTP 404 |
| fortune-crypto | rss | 0 | - | HTTP 404 |
| ft-markets | rss | 0 | - | HTTP 404 |
| gate-io | rss | 0 | - | HTTP 404 |
| gemini-com | rss | 0 | - | parse error: no entries in feed |
| halborn-com | rss | 0 | - | HTTP 404 |
| initc3-org | rss | 0 | - | parse error: no entries in feed |
| kaiko | rss | 0 | - | HTTP 404 |
| lightning-blog | rss | 0 | - | HTTP 404 |
| live-bitcoin-news | rss | 0 | - | HTTP 403 |
| marketwatch-crypto | rss | 0 | - | HTTP 403 |
| messari | rss | 0 | - | HTTP 429 |
| multicoin-capital | rss | 0 | - | HTTP 404 |
| nansen-ai | rss | 0 | - | HTTP 404 |
| near-org | rss | 0 | - | parse error: no entries in feed |
| okx-com | rss | 0 | - | HTTP 404 |
| openzeppelin | rss | 0 | - | HTTP 404 |
| osec-io | rss | 0 | - | HTTP 404 |
| panteracapital-com | rss | 0 | - | parse error: no entries in feed |
| paradigm-xyz | rss | 0 | - | HTTP 404 |
| placeholder-vc | rss | 0 | - | HTTP 404 |
| playtoearn-net | rss | 0 | - | HTTP 404 |
| polkadot-blog | rss | 0 | - | parse error: no entries in feed |
| polygon-technology | rss | 0 | - | HTTP 404 |
| quantstamp-com | rss | 0 | - | HTTP 404 |
| reddit-bitcoin | reddit | 0 | - | HTTP 429 |
| reddit-buttcoin | reddit | 0 | - | HTTP 429 |
| reddit-crypto_general | reddit | 0 | - | HTTP 429 |
| reddit-cryptocurrency | reddit | 0 | - | HTTP 429 |
| reddit-cryptocurrencytrading | reddit | 0 | - | HTTP 429 |
| reddit-cryptogaming | reddit | 0 | - | HTTP 429 |
| reddit-cryptomarkets | reddit | 0 | - | HTTP 429 |
| reddit-cryptomoonshots | reddit | 0 | - | HTTP 429 |
| reddit-cryptoscams | reddit | 0 | - | HTTP 429 |
| reddit-cryptotechnology | reddit | 0 | - | HTTP 429 |
| reddit-cryptotrading | reddit | 0 | - | HTTP 429 |
| reddit-defi | reddit | 0 | - | HTTP 429 |
| reddit-ethdev | reddit | 0 | - | HTTP 429 |
| reddit-ethereum | reddit | 0 | - | HTTP 429 |
| reddit-ethfinance | reddit | 0 | - | HTTP 429 |
| reddit-layer2 | reddit | 0 | - | HTTP 429 |
| reddit-nft | reddit | 0 | - | HTTP 429 |
| reddit-satoshistreetbets | reddit | 0 | - | HTTP 429 |
| reddit-solana | reddit | 0 | - | HTTP 429 |
| reddit-web3 | reddit | 0 | - | HTTP 429 |
| rekt-substack-com | rss | 0 | - | HTTP 403 |
| reuters-crypto | rss | 0 | - | HTTP 404 |
| reuters-tech | rss | 0 | - | HTTP 404 |
| sec-crypto | rss | 0 | - | HTTP 404 |
| solana-blog | rss | 0 | - | HTTP 404 |
| src-ac-uk | rss | 0 | - | HTTP 404 |
| starknet-io | rss | 0 | - | parse error: no entries in feed |
| the-crypto-times | rss | 0 | - | fetch error: ConnectError: [Errno -3] Temporary failure in name resolution |
| theblock | rss | 0 | - | HTTP 404 |
| thedefiant-substack-com | rss | 0 | - | HTTP 403 |
| thetie-is | rss | 0 | - | fetch error: ConnectError: [Errno -2] Name or service not known |
| tokeninsight-substack-com | rss | 0 | - | HTTP 403 |
| tokenterminal-com | rss | 0 | - | HTTP 404 |
| tradingview-com | rss | 0 | - | HTTP 500 |
| twitter-bankless | nitter | 0 | - | HTTP 404 |
| twitter-duneanalytics | nitter | 0 | - | HTTP 404 |
| twitter-theblock | nitter | 0 | - | HTTP 404 |
| uniswap-org | rss | 0 | - | HTTP 404 |
| variant-fund | rss | 0 | - | parse error: no entries in feed |
| verge-crypto | rss | 0 | - | parse error: no entries in feed |
| weekinethereum-substack-com | rss | 0 | - | HTTP 403 |
| wired-crypto | rss | 0 | - | HTTP 400 |
| yahoo-crypto | rss | 0 | - | HTTP 404 |

## STALE Sources (17)

| id | type | item_count | last_good | error |
|----|------|-----------:|-----------|-------|
| aptoslabs-medium-com | rss | 10 | 2026-05-28T16:25:58+00:00 | newest item 76d old (window: 30d) |
| bitcoin-core | rss | 112 | 2026-07-13T04:00:00+00:00 | newest item 31d old (window: 30d) |
| blockworks | rss | 50 | 2026-01-07T14:00:00+00:00 | newest item 217d old (window: 30d) |
| blog-matter-labs-io | rss | 10 | 2023-07-08T00:26:34+00:00 | newest item 1132d old (window: 30d) |
| blog-synthetix-io | rss | 15 | 2026-06-17T20:22:39+00:00 | newest item 56d old (window: 30d) |
| blog-trezor-io | rss | 10 | 2026-02-05T14:12:07+00:00 | newest item 188d old (window: 30d) |
| blog-yearn-fi | rss | 11 | 2026-04-17T13:00:00+00:00 | newest item 117d old (window: 30d) |
| immunefi | rss | 15 | 2026-05-21T12:55:35+00:00 | newest item 83d old (window: 30d) |
| insidebitcoins-com | rss | 100 | 2026-02-02T15:05:59+00:00 | newest item 191d old (window: 30d) |
| mit-blockchain | rss | 10 | 2024-02-06T15:00:00+00:00 | newest item 918d old (window: 30d) |
| nftnow-com | rss | 10 | 2025-07-15T14:29:28+00:00 | newest item 393d old (window: 30d) |
| pancakeswap-medium-com | rss | 10 | 2022-01-07T12:06:53+00:00 | newest item 1678d old (window: 30d) |
| paragraph-xyz | rss | 1 | 2022-04-01T09:55:14+00:00 | newest item 1595d old (window: 30d) |
| peckshield | rss | 10 | 2021-08-15T07:58:11+00:00 | newest item 1824d old (window: 30d) |
| starkware-medium-com | rss | 10 | 2024-10-09T06:46:45+00:00 | newest item 673d old (window: 30d) |
| wsj-markets | rss | 20 | 2025-01-27T19:26:00+00:00 | newest item 562d old (window: 30d) |
| wsj-tech | rss | 20 | 2025-01-27T18:17:00+00:00 | newest item 562d old (window: 30d) |
