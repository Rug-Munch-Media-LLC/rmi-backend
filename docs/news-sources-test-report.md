# Crypto News Sources — Test Report

- **Generated:** 2026-10-15T04:06:38.583071+00:00
- **Source file:** `app/databus/unified_news_sources.json`
- **Total sources probed:** 397
- **Freshness window:** 30 days

## Summary

- **VERIFIED** (returns fresh items): **171**
- **DEAD** (connection/parse error): **177**
- **STALE** (returns items, but old): **49**

Status map: `app/databus/news_sources_status.json`

## VERIFIED Sources

171 sources returned fresh items. IDs: 99bitcoins-com-feed, aave-com, aljazeera-tech, ambcrypto, ar-ca-blog-rss-xml, ars-crypto, bankless, bankless-com, bbc-business, bbc-tech, beincrypto, bi-crypto, bing-blockchain, bing-crypto, bitcoin-com, bitcoin-ops, bitcoinbazis-hu-feed, bitcoinchaser-com-feed, bitcoinist, bitcoinmagazine, bitcointe-com-feed, bitdegree-org-crypto-news-rss, bitpinas-com-feed, bleepingcomputer-com, blockchain-news-rss, blockonomi-com, blog-arbitrum-io, blog-bitfinex-com, blog-bitquery-io, blog-celestia-org, blog-chainalysis-com, blog-chainstack-com, blog-coinjar-com-feed, blog-hexens-io, blog-intotheblock-com, blog-lido-fi, blog-lido-fi-rss, blog-mexc-com, blog-sui-io, blog-tenderly-co, bloomberg-crypto, bloomberg-tech, bravenewcoin-com-news-rss, buyucoin-com-crypto-labs-feed, castleisland-vc-feed, chainalysis, changelly-com-blog-feed, cnbc-crypto, coincentral-com-news-feed, coindesk, coindoo-com-feed, coingate-com-feed, coingecko-buzz, coinjournal, coinpedia, coinspeaker-com, coinstats-app-blog-feed, cointelegraph, cointelegraph-com-rss-tag-bitcoin, cointelegraph-com-rss-tag-defi, cointelegraph-com-rss-tag-ethereum, cointelegraph-com-rss-tag-regulation, coinwink-com-blog-feed, coinworldstory-com-feed, collective-flashbots-net, crypto-daily, crypto-news, crypto-news-flash, crypto-news-net-feed, crypto-reporter, cryptoadventure-com-feed, cryptobreaking-com-feed, cryptobriefing, cryptocurrencynews-com-feed, cryptohayes-substack-com, cryptoinsider-media-feed, cryptonews, cryptonews-com-au-feed, cryptonewsz-com-feed, cryptoninjas-net-feed, cryptopolitan, cryptopotato, cryptosecurity-substack-com, cryptoslate, cryptoticker-io-en-feed, dailycoin-com, dailyhodl, darkreading-com, decrypt, defieducation-substack-com, dune-analytics, economist-fintech, ethereum-blog, ethereum-magicians-org, ethresear-ch, etoro-com-news-and-analysis-feed, feeds-feedburner-com, fetchrss-com-rss-608cfdce491e3967e35a5c12608cfeacee1b217b11729ab2-xml, financemagnates-com-cryptocurrency-feed, finbold-com, fintech-ca-feed, fintechbusinessweekly-substack-com-feed, forbes-crypto, fortune-crypto, forum-makerdao-com, forum-openzeppelin-com, ft-markets, ft-tech, globalcryptopress-com-feeds-posts-default, google-crypto, google-defi, google-news-bitcoin, google-news-crypto-regulation, google-news-crypto-security, google-news-cryptocurrency, google-news-defi, google-news-ethereum, google-news-nft, google-news-solana, google-news-stablecoin, google-nft, google-regulation, gov-uniswap-org, guardian-crypto, halborn-com, herbertrsim-com-feed, insidebitcoins-com, investing-com-rss-news-301-rss, krebsonsecurity-com, live-bitcoin-news, mit-blockchain, multicoin-capital, near-org, news-curve-finance-rss, newsbtc, nftevening-com, nftplazas-com, nulltx-com, nyt-business, nyt-technology, openzeppelin, osec-io, platinumcryptoacademy-com-feed, protos, scmp-crypto, sec-crypto, sec-gov, slowmist, smartliquidity-info-feed, solana-blog, solana-news, techcrunch-com, techcrunch-crypto, the-defiant, thebitcoinnews-com-feed, theblock, thecryptobasic-com-feed, time-com-nextadvisor-feed, trailofbits, tronweekly-com-feed, trustnodes, u-today, ukbitcoinblog-com-feed, unchained, usethebitcoin-com-feed, venturebeat-com, verge-crypto, web3isgoinggreat-com, wired-crypto, zebpay-com-feed, zycrypto

## DEAD Sources (177)

| id | type | item_count | last_good | error |
|----|------|-----------:|-----------|-------|
| binance-com | rss | 0 | - | HTTP 202 |
| blockchain-berkeley-edu | rss | 0 | - | HTTP 404 |
| blog-bybit-com | rss | 0 | - | parse error: no entries in feed |
| blog-coinbase-com | rss | 0 | - | parse error: no entries in feed |
| blog-infura-io | rss | 0 | - | fetch error: ConnectError: [SSL: TLSV1_UNRECOGNIZED_NAME] tlsv1 unrecognized name (_ssl.c:1000) |
| blog-injective-com | rss | 0 | - | parse error: no entries in feed |
| blog-kraken-com | rss | 0 | - | HTTP 403 |
| blog-ledger-com | rss | 0 | - | HTTP 401 |
| blog-makerdao-com | rss | 0 | - | parse error: no entries in feed |
| cfpb | rss | 0 | - | parse error: no entries in feed |
| cftc-gov | rss | 0 | - | HTTP 403 |
| chainlink-blog | rss | 0 | - | parse error: no entries in feed |
| coinbase-com | rss | 0 | - | HTTP 403 |
| coinmetrics-io | rss | 0 | - | parse error: no entries in feed |
| consensys-io | rss | 0 | - | HTTP 404 |
| cryptoadventure | rss | 0 | - | parse error: no entries in feed |
| dappradar-com | rss | 0 | - | parse error: no entries in feed |
| defillama | rss | 0 | - | HTTP 404 |
| dragonfly-xyz | rss | 0 | - | parse error: no entries in feed |
| fatf-gafi-org | rss | 0 | - | HTTP 403 |
| gate-io | rss | 0 | - | HTTP 404 |
| gemini-com | rss | 0 | - | parse error: no entries in feed |
| glassnode | rss | 0 | - | HTTP 403 |
| initc3-org | rss | 0 | - | HTTP 403 |
| investing-com-substack-com-feed | rss | 0 | - | parse error: no entries in feed |
| marketwatch-crypto | rss | 0 | - | HTTP 403 |
| medium-com-feed-arbitrum | rss | 0 | - | HTTP 429 |
| medium-com-feed-chainlink | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-compound | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-cosmos | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-curve | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-dune | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-kraken | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-lido | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-maker | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-messari | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-near | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-optimism | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-paradigm | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-solana | rss | 0 | - | parse error: no entries in feed |
| medium-com-feed-starknet | rss | 0 | - | parse error: no entries in feed |
| messari | rss | 0 | - | parse error: no entries in feed |
| panteracapital-com | rss | 0 | - | parse error: no entries in feed |
| playtoearn-net | rss | 0 | - | HTTP 403 |
| polkadot-blog | rss | 0 | - | parse error: no entries in feed |
| reddit-altcoin | reddit | 0 | - | HTTP 429 |
| reddit-bitcoin | reddit | 0 | - | HTTP 429 |
| reddit-buttcoin | reddit | 0 | - | HTTP 429 |
| reddit-crypto_general | reddit | 0 | - | HTTP 429 |
| reddit-cryptocurrency | reddit | 0 | - | HTTP 429 |
| reddit-cryptocurrencytrading | reddit | 0 | - | HTTP 429 |
| reddit-cryptogaming | reddit | 0 | - | HTTP 429 |
| reddit-cryptomarkets | reddit | 0 | - | HTTP 403 |
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
| rekt-substack-com | rss | 0 | - | parse error: no entries in feed |
| starknet-io | rss | 0 | - | parse error: no entries in feed |
| twitter-0x_lens | nitter | 0 | - | HTTP 410 |
| twitter-0xcygaar | nitter | 0 | - | HTTP 410 |
| twitter-0xfoobar | nitter | 0 | - | HTTP 410 |
| twitter-0xmert_ | nitter | 0 | - | HTTP 410 |
| twitter-0xpolygon | nitter | 0 | - | HTTP 410 |
| twitter-100trillionusd | nitter | 0 | - | HTTP 410 |
| twitter-1confirmation | nitter | 0 | - | HTTP 410 |
| twitter-1inch | nitter | 0 | - | HTTP 410 |
| twitter-a16zcrypto | nitter | 0 | - | HTTP 410 |
| twitter-aantonop | nitter | 0 | - | HTTP 410 |
| twitter-aaveaave | nitter | 0 | - | HTTP 410 |
| twitter-aeyakovenko | nitter | 0 | - | HTTP 410 |
| twitter-apompliano | nitter | 0 | - | HTTP 410 |
| twitter-aptos | nitter | 0 | - | HTTP 410 |
| twitter-arbitrum | nitter | 0 | - | HTTP 410 |
| twitter-avax | nitter | 0 | - | HTTP 410 |
| twitter-balajis | nitter | 0 | - | HTTP 410 |
| twitter-banklesshq | nitter | 0 | - | HTTP 410 |
| twitter-base | nitter | 0 | - | HTTP 410 |
| twitter-biancoresearch | nitter | 0 | - | HTTP 410 |
| twitter-bitcoinmagazine | nitter | 0 | - | HTTP 410 |
| twitter-blknoiz06 | nitter | 0 | - | HTTP 410 |
| twitter-blockworks_ | nitter | 0 | - | HTTP 410 |
| twitter-bloombergcrypto | nitter | 0 | - | HTTP 410 |
| twitter-cathiedwood | nitter | 0 | - | HTTP 410 |
| twitter-cburniske | nitter | 0 | - | HTTP 410 |
| twitter-celestiaorg | nitter | 0 | - | HTTP 410 |
| twitter-chainlink | nitter | 0 | - | HTTP 410 |
| twitter-cobie | nitter | 0 | - | HTTP 410 |
| twitter-coindesk | nitter | 0 | - | HTTP 410 |
| twitter-cointelegraph | nitter | 0 | - | HTTP 410 |
| twitter-cosmos | nitter | 0 | - | HTTP 410 |
| twitter-cryptohayes | nitter | 0 | - | HTTP 410 |
| twitter-cryptokoryo | nitter | 0 | - | HTTP 410 |
| twitter-cryptorank | nitter | 0 | - | HTTP 410 |
| twitter-decryptmedia | nitter | 0 | - | HTTP 410 |
| twitter-defiantnews | nitter | 0 | - | HTTP 410 |
| twitter-defiignas | nitter | 0 | - | HTTP 410 |
| twitter-defillama | nitter | 0 | - | HTTP 410 |
| twitter-delphi_digital | nitter | 0 | - | HTTP 410 |
| twitter-dlnewsinfo | nitter | 0 | - | HTTP 410 |
| twitter-doveywan | nitter | 0 | - | HTTP 410 |
| twitter-dragonfly_xyz | nitter | 0 | - | HTTP 410 |
| twitter-drakefjustin | nitter | 0 | - | HTTP 410 |
| twitter-duneanalytics | nitter | 0 | - | HTTP 410 |
| twitter-eigenlayer | nitter | 0 | - | HTTP 410 |
| twitter-electriccapital | nitter | 0 | - | HTTP 410 |
| twitter-ercwl | nitter | 0 | - | HTTP 410 |
| twitter-ericbalchunas | nitter | 0 | - | HTTP 410 |
| twitter-ethereumjoseph | nitter | 0 | - | HTTP 410 |
| twitter-forbescrypto | nitter | 0 | - | HTTP 410 |
| twitter-ft | nitter | 0 | - | HTTP 410 |
| twitter-gavofyork | nitter | 0 | - | HTTP 410 |
| twitter-glassnode | nitter | 0 | - | HTTP 410 |
| twitter-hasufl | nitter | 0 | - | HTTP 410 |
| twitter-haydenzadams | nitter | 0 | - | HTTP 410 |
| twitter-intotheblock | nitter | 0 | - | HTTP 410 |
| twitter-jseyff | nitter | 0 | - | HTTP 410 |
| twitter-kylesamani | nitter | 0 | - | HTTP 410 |
| twitter-laurashin | nitter | 0 | - | HTTP 410 |
| twitter-lidofinance | nitter | 0 | - | HTTP 410 |
| twitter-lightcrypto | nitter | 0 | - | HTTP 410 |
| twitter-lookonchain | nitter | 0 | - | HTTP 410 |
| twitter-lopp | nitter | 0 | - | HTTP 410 |
| twitter-lucasnuzzi | nitter | 0 | - | HTTP 410 |
| twitter-lynaldencontact | nitter | 0 | - | HTTP 410 |
| twitter-makerdao | nitter | 0 | - | HTTP 410 |
| twitter-matt_hougan | nitter | 0 | - | HTTP 410 |
| twitter-melt_dem | nitter | 0 | - | HTTP 410 |
| twitter-messari | nitter | 0 | - | HTTP 410 |
| twitter-michael_saylor | nitter | 0 | - | HTTP 410 |
| twitter-monad_xyz | nitter | 0 | - | HTTP 410 |
| twitter-multicoincap | nitter | 0 | - | HTTP 410 |
| twitter-muststopmurad | nitter | 0 | - | HTTP 410 |
| twitter-nansen_ai | nitter | 0 | - | HTTP 410 |
| twitter-nearprotocol | nitter | 0 | - | HTTP 410 |
| twitter-nic__carter | nitter | 0 | - | HTTP 410 |
| twitter-optimismfnd | nitter | 0 | - | HTTP 410 |
| twitter-panteracapital | nitter | 0 | - | HTTP 410 |
| twitter-pendle_fi | nitter | 0 | - | HTTP 410 |
| twitter-peterktodd | nitter | 0 | - | HTTP 410 |
| twitter-polychaincap | nitter | 0 | - | HTTP 410 |
| twitter-raoulgmi | nitter | 0 | - | HTTP 410 |
| twitter-reuters | nitter | 0 | - | HTTP 410 |
| twitter-rewkang | nitter | 0 | - | HTTP 410 |
| twitter-runekek | nitter | 0 | - | HTTP 410 |
| twitter-sassal0x | nitter | 0 | - | HTTP 410 |
| twitter-solanafndn | nitter | 0 | - | HTTP 410 |
| twitter-solanastatus | nitter | 0 | - | HTTP 410 |
| twitter-stacks | nitter | 0 | - | HTTP 410 |
| twitter-stanikulechov | nitter | 0 | - | HTTP 410 |
| twitter-starknet | nitter | 0 | - | HTTP 410 |
| twitter-suinetwork | nitter | 0 | - | HTTP 410 |
| twitter-theblock__ | nitter | 0 | - | HTTP 410 |
| twitter-thinkingusd | nitter | 0 | - | HTTP 410 |
| twitter-timbeiko | nitter | 0 | - | HTTP 410 |
| twitter-udiwertheimer | nitter | 0 | - | HTTP 410 |
| twitter-unchained_pod | nitter | 0 | - | HTTP 410 |
| twitter-uniswap | nitter | 0 | - | HTTP 410 |
| twitter-vancespencer | nitter | 0 | - | HTTP 410 |
| twitter-variantfund | nitter | 0 | - | HTTP 410 |
| twitter-vitalikbuterin | nitter | 0 | - | HTTP 410 |
| twitter-watanglass | nitter | 0 | - | HTTP 410 |
| twitter-whale_alert | nitter | 0 | - | HTTP 410 |
| twitter-wsjmarkets | nitter | 0 | - | HTTP 410 |
| twitter-zachxbt | nitter | 0 | - | HTTP 410 |
| twitter-zksync | nitter | 0 | - | HTTP 410 |
| variant-fund | rss | 0 | - | parse error: no entries in feed |
| yahoo-crypto | rss | 0 | - | HTTP 404 |

## STALE Sources (49)

| id | type | item_count | last_good | error |
|----|------|-----------:|-----------|-------|
| 0xresearch-substack-com-feed | rss | 1 | 2022-08-15T22:10:11+00:00 | newest item 1521d old (window: 30d) |
| aleph-substack-com-feed | rss | 1 | 2020-08-02T09:19:08+00:00 | newest item 2264d old (window: 30d) |
| api-paragraph-com-blogs-rss-polynya | rss | 20 | 2024-03-24T04:05:14+00:00 | newest item 935d old (window: 30d) |
| api-paragraph-com-blogs-rss-the-optimism-collective | rss | 20 | 2025-06-12T19:48:04+00:00 | newest item 489d old (window: 30d) |
| api-paragraph-com-blogs-rss-zksync | rss | 20 | 2025-10-07T10:47:22+00:00 | newest item 372d old (window: 30d) |
| aptoslabs-medium-com | rss | 10 | 2026-05-28T16:25:58+00:00 | newest item 139d old (window: 30d) |
| banklessdao-substack-com | rss | 20 | 2025-01-27T00:53:44+00:00 | newest item 626d old (window: 30d) |
| bitcoin-core | rss | 112 | 2026-07-13T04:00:00+00:00 | newest item 94d old (window: 30d) |
| blockworks | rss | 50 | 2026-01-07T14:00:00+00:00 | newest item 280d old (window: 30d) |
| blockworks-substack-com-feed | rss | 1 | 2022-07-03T20:24:39+00:00 | newest item 1564d old (window: 30d) |
| blog-balancer-fi | rss | 10 | 2026-06-01T13:53:02+00:00 | newest item 135d old (window: 30d) |
| blog-base-org | rss | 20 | 2026-09-01T16:57:37+00:00 | newest item 43d old (window: 30d) |
| blog-compound-finance | rss | 10 | 2022-09-14T13:02:30+00:00 | newest item 1491d old (window: 30d) |
| blog-matter-labs-io | rss | 10 | 2023-07-08T00:26:34+00:00 | newest item 1195d old (window: 30d) |
| blog-morpho-org | rss | 10 | 2023-05-24T15:08:42+00:00 | newest item 1239d old (window: 30d) |
| blog-santiment-net | rss | 10 | 2024-06-06T13:44:44+00:00 | newest item 860d old (window: 30d) |
| blog-synthetix-io | rss | 15 | 2026-06-17T20:22:39+00:00 | newest item 119d old (window: 30d) |
| blog-trezor-io | rss | 10 | 2026-02-05T14:12:07+00:00 | newest item 251d old (window: 30d) |
| blog-venus-io | rss | 8 | 2021-05-12T11:46:18+00:00 | newest item 1981d old (window: 30d) |
| blog-yearn-fi | rss | 12 | 2026-08-18T13:48:20+00:00 | newest item 57d old (window: 30d) |
| certik-medium-com | rss | 10 | 2026-09-08T04:51:09+00:00 | newest item 36d old (window: 30d) |
| dedaub-com | rss | 10 | 2023-02-10T18:32:38+00:00 | newest item 1342d old (window: 30d) |
| dlnews-com | rss | 40 | 2026-05-07T17:04:07+00:00 | newest item 160d old (window: 30d) |
| doseofdefi-substack-com | rss | 20 | 2025-12-31T22:23:00+00:00 | newest item 287d old (window: 30d) |
| esma-europa-eu | rss | 10 | - | items present but no parseable pub dates |
| immunefi | rss | 15 | 2026-05-21T12:55:35+00:00 | newest item 146d old (window: 30d) |
| medium-com-feed-a16z | rss | 2 | 2015-07-23T20:50:02+00:00 | newest item 4101d old (window: 30d) |
| medium-com-feed-aave | rss | 1 | 2021-08-26T17:25:23+00:00 | newest item 1875d old (window: 30d) |
| medium-com-feed-aptoslabs | rss | 10 | 2026-05-28T16:25:58+00:00 | newest item 139d old (window: 30d) |
| medium-com-feed-avax | rss | 10 | 2022-01-20T17:43:57+00:00 | newest item 1728d old (window: 30d) |
| medium-com-feed-balancer | rss | 2 | 2018-07-12T09:07:33+00:00 | newest item 3016d old (window: 30d) |
| medium-com-feed-bankless | rss | 5 | 2021-01-18T14:43:04+00:00 | newest item 2095d old (window: 30d) |
| medium-com-feed-binance | rss | 10 | 2019-02-04T03:14:51+00:00 | newest item 2810d old (window: 30d) |
| medium-com-feed-coinbase | rss | 10 | 2016-11-18T21:37:16+00:00 | newest item 3617d old (window: 30d) |
| medium-com-feed-ensdomains | rss | 1 | 2023-11-20T07:09:39+00:00 | newest item 1059d old (window: 30d) |
| milkroad-substack-com-feed | rss | 20 | 2026-09-14T00:05:22+00:00 | newest item 31d old (window: 30d) |
| nftnow-com | rss | 10 | 2025-07-15T14:29:28+00:00 | newest item 456d old (window: 30d) |
| pancakeswap-medium-com | rss | 10 | 2022-01-07T12:06:53+00:00 | newest item 1741d old (window: 30d) |
| paragraph-xyz | rss | 1 | 2022-04-01T09:55:14+00:00 | newest item 1657d old (window: 30d) |
| peckshield | rss | 10 | 2021-08-15T07:58:11+00:00 | newest item 1886d old (window: 30d) |
| quantstamp-com | rss | 50 | 2026-09-11T13:52:00+00:00 | newest item 33d old (window: 30d) |
| starkware-medium-com | rss | 10 | 2024-10-09T06:46:45+00:00 | newest item 735d old (window: 30d) |
| starkware-substack-com-feed | rss | 4 | 2022-11-01T06:43:53+00:00 | newest item 1443d old (window: 30d) |
| thedailygwei-substack-com-feed | rss | 20 | 2022-10-12T12:56:15+00:00 | newest item 1463d old (window: 30d) |
| thedefiant-substack-com | rss | 20 | 2022-11-24T04:16:32+00:00 | newest item 1420d old (window: 30d) |
| tokeninsight-substack-com | rss | 1 | 2022-10-31T03:50:19+00:00 | newest item 1445d old (window: 30d) |
| weekinethereum-substack-com | rss | 20 | 2026-08-04T21:07:40+00:00 | newest item 71d old (window: 30d) |
| wsj-markets | rss | 20 | 2025-01-27T19:26:00+00:00 | newest item 625d old (window: 30d) |
| wsj-tech | rss | 20 | 2025-01-27T18:17:00+00:00 | newest item 625d old (window: 30d) |
