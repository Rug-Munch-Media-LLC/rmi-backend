# Rug Munch Intelligence — Complete Tool Reference

> **70+ security engines · 16 chains · $0.01–$0.40/call · Free trials on every tool**

---

## Security Tools (29 + 9 SENTINEL = 38)

### Core Security Tools (29)

### `honeypot_check` — Honeypot Detector
**Price: $0.05 | Free trials: 2**
Simulates buy and sell transactions to verify you can actually sell a token. Tests transfer taxes, sell locks, blacklists, and trap mechanisms. Essential before aping into any new token.
- **Detects:** Buy-only mechanics, 99% sell tax, transfer pausability, blacklist traps
- **Chains:** 16 indexed chains (14 EVM + Solana + Tron)

### `rug_pull_predictor` — Rug Pull Predictor
**Price: $0.10 | Free trials: 1**
AI-powered risk scoring using 12+ on-chain signals. Analyzes liquidity locks, ownership concentration, holder distribution, social signals, deployer history, and contract patterns.
- **Risk levels:** Low / Medium / High / Critical
- **Signals:** Liquidity lock duration, ownership renounce status, mint authority, proxy patterns

### `rugshield` — Rug Shield
**Price: $0.02 | Free trials: 3**
Fast multi-factor rug protection score. Quick scan ideal for rapid token evaluation.
- **Score:** 0-100 with breakdown by category
- **Best for:** Initial screening before deep audit

### `audit` — Smart Contract Audit
**Price: $0.05 | Free trials: 1**
Deep smart contract audit with Slither analysis. Vulnerability scanning, ownership structure mapping, proxy detection, and function-level risk assessment.
- **Covers:** Reentrancy, overflow, access control, timelock, upgrade patterns
- **Chains:** Ethereum, Base, BSC, Polygon, Arbitrum, Optimism

### `clone_detect` — Clone Detector
**Price: $0.02 | Free trials: 3**
Bytecode similarity analysis comparing contracts against known scam templates. Catches copycat scams before they rug.
- **Detection:** Exact clones, modified clones, template matches
- **Database:** 10,000+ known scam contracts

### `anomaly` — Market Anomaly Detector
**Price: $0.08 | Free trials: 0**
Volume spike detection, price manipulation alerts, liquidity anomalies, sentiment extremes. Market health assessment.
- **Alerts:** 5x volume spike, 30%+ price deviation, liquidity removal

### `risk_monitor` — Risk Monitor
**Price: $0.05 | Free trials: 1**
Real-time risk monitoring. Set alerts for wallets, tokens, or chains. Get notified of rug pulls, liquidity removals, whale dumps.
- **Alert types:** Smart contract changes, liquidity events, whale movements

### `mev_protection` — MEV Protection Checker
**Price: $0.08 | Free trials: 0**
Verify if your transaction is protected from MEV extraction. Sandwich attack risk assessment and protection recommendations.

### `mev_alert` — MEV Alert System
**Price: $0.08 | Free trials: 0**
Real-time sandwich attack, frontrun, and arbitrage detection with wallet protection recommendations.

### `bridge_security` — Bridge Security Monitor
**Price: $0.08 | Free trials: 0**
Cross-chain bridge TVL tracking, recent exploit history, audit status, withdrawal limits, and security scores.

### `bridge_health` — Bridge Health & Exploit Monitor
**Price: $0.10 | Free trials: 2**
Active exploit surveillance across 12 major bridges. Monitors TVL anomalies, detects suspicious withdrawal patterns, scores trust models (validator sets, optimistic, intent-based), and computes contagion risk when one bridge shows exploit signs. Generates human-readable security bulletins and JSON alerts.
- **Bridges:** LayerZero, Stargate, Across, Wormhole, Hop, Synapse, Axelar, Celer, DeBridge, Chainlink CCIP, Connext, Orbiter
- **Detects:** Critical TVL drops (-40%+ in 24h), anomalous outflow patterns, upgrade events, validator set changes
- **Scoring:** 5-factor security rating (TVL depth, decentralization, audit recency, exploit history, upgrade risk)
- **Cron mode:** `--alert` flag returns [SILENT] when all bridges healthy, full report on anomaly — ideal for automated monitoring

### `wash_trading` — Wash Trading Detector
**Price: $0.08 | Free trials: 0**
Identify fake volume patterns, self-trades, and artificial market activity across NFTs and tokens.

### `protocol_risk` — Protocol Risk Assessment
**Price: $0.08 | Free trials: 0**
TVL stability analysis, admin key review, upgrade patterns, oracle dependency, governance risk scoring.

### `scam_database` — Scam Database Lookup
**Price: $0.03 | Free trials: 0**
Check addresses against known scam, phishing, honeypot, and rug pull databases.

### `urlcheck` — URL Safety Check
**Price: $0.01 | Free trials: 3**
URL and domain safety check for crypto projects. Detects phishing sites, fake docs, and malicious redirects.

### `bundler_detect` — Bundler Detector
**Price: $0.05 | Free trials: 2**
MEV bundler activity detection. Sandwich attacks, frontrunning, backrunning patterns on Solana and EVM.

### `fresh_pair` — Fresh Pair Scanner
**Price: $0.03 | Free trials: 2**
Detect newly created trading pairs with liquidity depth, ownership concentration, and honeypot risk assessment.

### `profile_flip` — Profile Flip Detector
**Price: $0.03 | Free trials: 2**
Sudden profile changes before token launches. Twitter/X profile monitoring, domain swaps, branding pivots.

### `liquidity_migration` — Liquidity Migration Detector
**Price: $0.05 | Free trials: 2**
Detect tokens moving pools, chains, or protocols. Often a rug pull precursor signal.

### `deployer_history` — Deployer History
**Price: $0.05 | Free trials: 2**
Complete investigation of a contract deployer's history. Previous tokens, success rate, scam patterns across chains.

### `token_age` — Token Age Verifier
**Price: $0.01 | Free trials: 2**
Verify token creation date, contract age, migration history, proxy upgrades, and deployment patterns.

### `flash_loan_detect` — Flash Loan Attack Detector
**Price: $0.08 | Free trials: 2**
Detect flash loan attack patterns on any token or pool. Identifies price manipulation sequences, atomic arbitrage exploits, and governance vote flashes before damage occurs.

### `governance_attack` — Governance Attack Detector
**Price: $0.08 | Free trials: 2**
Detect governance manipulation and voting anomalies. Flags flash-loan voting, proposal hijacking, quorum exploitation, and centralized governance risk in DAO protocols.

### `contract_upgrade_monitor` — Contract Upgrade Monitor
**Price: $0.05 | Free trials: 2**
Monitor proxy contract upgrades in real-time. Detects malicious implementation swaps, hidden timelock changes, and privilege escalation through upgrade patterns.

### `reentrancy_scanner` — Reentrancy Scanner
**Price: $0.05 | Free trials: 2**
Scan smart contracts for reentrancy vulnerability patterns. Detects external calls before state updates, callback loops, and cross-function reentrancy attack surfaces.

### `dust_attack_detect` — Dust Attack Detector
**Price: $0.05 | Free trials: 2**
Identify dusting attacks and address poisoning. Traces micro-transactions from attacker wallets, detects lookalike address generation, and flags contaminated UTXO sets.

### `oracle_manipulation` — Oracle Manipulation Detector
**Price: $0.08 | Free trials: 2**
Detect oracle price manipulation attack vectors. Analyzes feed latency, stale price windows, single-source dependencies, and historical manipulation events for any DeFi protocol.

### `privilege_escalation` — Privilege Escalation Detector
**Price: $0.05 | Free trials: 2**
Detect excessive contract owner privileges. Flags unlimited mint functions, emergency_PAUSE backdoors, transfer blocklists, and hidden admin roles in token contracts.

### `phantom_mint_detect` — Phantom Mint Detector
**Price: $0.05 | Free trials: 2**
Detect phantom minting attacks where hidden mint functions create tokens from nowhere. Scans contract bytecode for unauthorized mint paths, inflation exploits, and supply manipulation vectors.

### `wallet_drain_scanner` — Wallet Drain Scanner
**Price: $0.05 | Free trials: 2**
Scan wallet for dangerous token approvals and signatures. Identifies unlimited spending approvals, phishing permit signatures, and drain contract vulnerabilities.

### SENTINEL Deep Scan Modules (9)

### `sentinel_scan` — SENTINEL Full Deep Scan
**Price: $0.15 | Free trials: 1**
All 9 SENTINEL modules in parallel with graceful degradation. The most comprehensive token security scan available. Graded risk score from 0-100.

### `holder_analysis` — Holder Analysis
**Price: $0.05 | Free trials: 2**
HHI concentration, fake diversification detection, whale ratio, holder health score.

### `bundle_detect` — Bundle Detection (SENTINEL)
**Price: $0.08 | Free trials: 2**
Enhanced bundle/sniper detection, same-block group analysis, MEV exposure.

### `exchange_fund_check` — CEX Fund Check
**Price: $0.05 | Free trials: 2**
CEX-funded wallet detection, withdrawal clustering, deposit-to-dump patterns.

### `liquidity_verify` — Liquidity Verification
**Price: $0.05 | Free trials: 2**
Lock verification, fake locker detection, timelock analysis, rug-proof LP assessment.

### `dev_reputation` — Developer Reputation
**Price: $0.05 | Free trials: 2**
Serial rugg detection, cross-chain deployer tracking, team history analysis.

### `wash_trading` — Wash Trading (SENTINEL)
**Price: $0.08 | Free trials: 2**
Circular transfer detection, cross-DEX loops, volume anomaly scoring.

### `metadata_fingerprint` — Metadata Fingerprint
**Price: $0.05 | Free trials: 2**
HTML structure hashing, description similarity, social fingerprinting, clone project detection.

### `pumpfun_analysis` — PumpFun Analysis
**Price: $0.08 | Free trials: 2**
Bonding curve progress, bot detection, dev wallet concentration, early buyer patterns. Solana only.

### `sentiment_check` — SENTINEL Sentiment Check
**Price: $0.05 | Free trials: 2**
Social sentiment scoring, bot campaign detection, artificial hype flagging, pump probability.

---

## Intelligence Tools (27)

### `whale` — Whale Wallet Decoder
**Price: $0.15 | Free trials: 1**
Advanced whale wallet decoder. Solana/EVM balance, TX pattern analysis, DexScreener pair association, Blockchair multi-chain stats. Identifies whale persona, activity level, and trust score.
- **Persona types:** Accumulator, Distributor, Market Maker, Degen, Institutional
- **Data:** Holdings, trade history, PnL, associated wallets, risk profile

### `whale_scan` — Whale Scanner
**Price: $0.03 | Free trials: 2**
Real-time whale activity across chains. Large transfers, exchange deposits, accumulation signals.

### `whale_accumulation` — Whale Accumulation Detector
**Price: $0.08 | Free trials: 0**
Track large wallet accumulation and distribution patterns. Know what whales are buying and dumping.

### `whale_profile` — Whale Profile
**Price: $0.05 | Free trials: 1**
Detailed analysis of a whale wallet: holdings, strategy classification, historical performance, influence score.

### `smartmoney` — Smart Money Tracker
**Price: $0.05 | Free trials: 1**
Track smart money wallets and whale movements across chains. Find what profitable traders are buying before the crowd.
- **Metrics:** Win rate, average ROI, trade frequency, token preferences

### `smart_money_alpha` — Smart Money Alpha
**Price: $0.01 | Free trials: 1**
Real-time alerts when top-performing wallets enter new positions. Copy the best traders.

### `insider` — Insider Trading Detection
**Price: $0.10 | Free trials: 0**
Track wallet funding patterns, pre-launch accumulation, and coordinated buying before major events.
- **Signals:** Pre-listing accumulation, team wallet activity, coordinated entry patterns

### `insider_network` — Insider Network Mapper
**Price: $0.10 | Free trials: 0**
Trace connected wallets, shared funding sources, coordinated trading patterns across addresses.

### `cluster` — Wallet Cluster Analysis
**Price: $0.05 | Free trials: 0**
Identify linked wallets, Sybil networks, and coordinated manipulation across addresses.
- **Detection:** Shared funding, transaction correlation, temporal patterns

### `syndicate_scan` — Syndicate Scanner
**Price: $0.08 | Free trials: 0**
Identify coordinated trading groups, wash trading rings, and pump-and-dump networks.

### `syndicate_track` — Syndicate Tracker
**Price: $0.10 | Free trials: 0**
Follow known syndicate wallets, monitor their current positions and exit patterns.

### `copy_trade_finder` — Copy Trade Finder
**Price: $0.10 | Free trials: 0**
Find profitable wallets, their win rates, best performing trades, and current positions. Auto-identifies smart money worth following.

### `sniper_detect` — Sniper Detector
**Price: $0.08 | Free trials: 1**
Detect sniper bots on token launches. Block-0 buys, MEV bundles, sandwich patterns.

### `sniper_alert` — Sniper Alert
**Price: $0.05 | Free trials: 2**
Real-time sniper bot detection on new token launches. Get in before or after the snipers.

### `wallet_graph` — Wallet Graph Analysis
**Price: $0.10 | Free trials: 0**
Visualize transaction flows between wallets. Identify money laundering patterns and entity relationships.

### `alpha_digest` — Alpha Digest
**Price: $0.10 | Free trials: 1**
Curated crypto alpha from top-performing wallets, on-chain signals, sentiment spikes, and accumulation patterns.

### `airdrop_finder` — Airdrop Finder
**Price: $0.05 | Free trials: 2**
Discover active and upcoming airdrops across all major chains. Eligibility checks, value estimation, claim deadlines, and Sybil detection.

### `airdrop_check` — Airdrop Check
**Price: $0.05 | Free trials: 2**
Verify airdrop legitimacy. Contract audit, distribution analysis, scam pattern detection. Know if an airdrop is real or a wallet drainer before connecting.

### `cross_chain_whale` — Cross-Chain Whale Tracker
**Price: $0.08 | Free trials: 2**
Track whale wallets across multiple blockchains simultaneously. Maps cross-chain capital flows, bridge migrations, and multi-network positioning.

### `degen_score` — Degen Score
**Price: $0.05 | Free trials: 2**
Calculate degen trading behavior score for any wallet. Evaluates leverage usage, meme token exposure, entry timing quality, and risk-on appetite with percentile ranking.

### `dormant_whale_alert` — Dormant Whale Alert
**Price: $0.05 | Free trials: 2**
Alert when dormant large wallets become active. Monitors long-inactive top holders for reactivation signals, first transfers, and exchange deposits that precede market moves.

### `smart_contract_interactions` — Smart Contract Interactions
**Price: $0.05 | Free trials: 2**
Map all contract interactions for a given address. Reconstructs call graphs, identifies frequently-used protocols, and surfaces unknown delegate calls or suspicious relationships.

### `token_distribution_health` — Token Distribution Health
**Price: $0.05 | Free trials: 2**
Assess the health of token holder distribution. Computes Gini coefficient, Herfindahl index, top-10 concentration risk, and compares distribution trajectory over time.

### `token_velocity` — Token Velocity
**Price: $0.05 | Free trials: 2**
Analyze token circulation speed and holding patterns. Computes turnover rate, velocity of money, dormant supply ratio, and compares against sector benchmarks.

### `wallet_label_registry` — Wallet Label Registry
**Price: $0.05 | Free trials: 2**
Enrich wallet addresses with known entity labels. Resolves exchange hot wallets, institutional addresses, MEV bot identities, known scammers, and fund manager tags.

### `wallet_cluster_score` — Wallet Cluster Score
**Price: $0.08 | Free trials: 2**
Score wallet clusters for manipulation risk probability. Combines funding-source analysis, behavioral correlation, and timing patterns to rate coordinated group threat level.

---

## Market Tools (15)

### `pulse` — Market Pulse
**Price: $0.01 | Free trials: 3**
Real-time market dashboard. Token momentum, volume spikes, whale alerts, trending tokens across all major chains.

### `market_overview` — Market Overview
**Price: $0.05 | Free trials: 0**
Comprehensive crypto market overview. BTC/ETH prices, chain TVL, trending coins, top markets, BTC fees, news sentiment. Aggregates 10+ public sources.

### `chain_health` — Chain Health
**Price: $0.05 | Free trials: 0**
Chain health metrics. TVL, active protocols, gas fees, block times, network stats for Ethereum, Solana, Base, BSC.

### `gas_forecast` — Gas Forecaster
**Price: $0.05 | Free trials: 0**
Predicts optimal transaction times across chains. Tracks gas trends, suggests cheapest windows for swaps, mints, and transfers.

### `defi_yield_scanner` — DeFi Yield Scanner
**Price: $0.08 | Free trials: 1**
Finds best APYs across chains. Detects unsustainable yields, checks protocol TVL trends, impermanent loss risk, smart contract age.

### `arbitrage_scan` — Arbitrage Scanner
**Price: $0.05 | Free trials: 2**
Cross-chain and cross-DEX arbitrage scanner. Find price discrepancies across exchanges for instant profit opportunities.

### `liquidity_depth` — Liquidity Depth Analyzer
**Price: $0.05 | Free trials: 0**
Order book depth, slippage estimation, market impact across DEXs and chains.

### `liquidity_flow` — Liquidity Flow Tracker
**Price: $0.08 | Free trials: 0**
Track where capital is moving across chains, pools, and protocols. Front-run liquidity migrations.

### `unlock_calendar` — Token Unlock Calendar
**Price: $0.03 | Free trials: 2**
Track vesting schedules, team token unlocks, upcoming dilution events that move prices.

### `funding_rate` — Funding Rate Monitor
**Price: $0.05 | Free trials: 2**
Monitor and analyze perpetual futures funding rates. Tracks real-time rates across exchanges, identifies extreme positioning, and correlates funding with spot price action.

### `options_flow` — Options Flow Tracker
**Price: $0.08 | Free trials: 2**
Track unusual options activity and large block trades. Detects smart money positioning through IV skew, put/call ratio anomalies, and outsized OI changes on crypto derivatives.

### `dex_volume_rank` — DEX Volume Rank
**Price: $0.05 | Free trials: 2**
Rank tokens by decentralized exchange trading volume. Computes volume-weighted momentum scores, compares DEX vs CEX volume distribution, and surfaces volume outliers trending up.

### `liquidation_heatmap` — Liquidation Heatmap
**Price: $0.05 | Free trials: 2**
Visualize liquidation clusters and cascade risk zones. Maps leveraged position concentrations by price level, estimates cascade threshold prices, and highlights DeFi protocol vulnerability.

### `orderbook_imbalance` — Orderbook Imbalance
**Price: $0.05 | Free trials: 2**
Detect order book asymmetry signaling directional pressure. Computes bid-ask depth ratio, spoofing probability, and hidden wall detection across major trading venues.

### `volatility_surface` — Volatility Surface
**Price: $0.05 | Free trials: 2**
Analyze implied and realized volatility across timeframes. Constructs term structure, identifies volatility skew opportunities, and compares current levels to historical percentile ranges.

---

## Analysis Tools (14)

### `wallet` — Wallet Analysis
**Price: $0.05 | Free trials: 1**
Comprehensive wallet analysis. Balance, token holdings, transaction history, risk score, behavioral patterns across chains.

### `wallet_pnl` — Wallet PnL Calculator
**Price: $0.10 | Free trials: 0**
Realized/unrealized gains, win rate, ROI, Sharpe ratio, and complete trade history analysis.

### `forensics` — Token Forensics
**Price: $0.10 | Free trials: 1**
Deep token forensics report combining DexScreener, GeckoTerminal, CoinGecko, DefiLlama, CryptoPanic. Risk score, liquidity analysis, pair age, verification status, buy/sell recommendation.

### `token_deep_dive` — Token Deep Dive
**Price: $0.10 | Free trials: 0**
Deep token analysis across chains. CoinGecko data, DexScreener pairs, trending status, market cap rank, exchange listings, TVL in DeFi protocols.

### `token_comparison` — Token Comparison
**Price: $0.08 | Free trials: 0**
Side-by-side token comparison. Metrics, risk scores, holder distribution, liquidity depth, social sentiment. Compare 2-5 tokens instantly.

### `portfolio_tracker` — Portfolio Tracker
**Price: $0.10 | Free trials: 0**
Multi-wallet portfolio tracker. Aggregate PnL, asset allocation, top holdings, unrealized gains/losses across chains.

### `portfolio_aggregate` — Portfolio Aggregator
**Price: $0.10 | Free trials: 0**
Combine multiple wallets into a single dashboard with consolidated PnL and asset allocation.

### `nft_wash_detector` — NFT Wash Trading Detector
**Price: $0.10 | Free trials: 1**
Identifies fake volume in NFT collections. Tracks floor price manipulation, finds collections with real organic demand vs artificial hype.

### `correlation_matrix` — Correlation Matrix
**Price: $0.05 | Free trials: 2**
Compute cross-token correlation heatmap for portfolio risk. Generates rolling correlation matrices, identifies regime shifts, and flags pairs with diverging correlation signals.

### `drawdown_analyzer` — Drawdown Analyzer
**Price: $0.05 | Free trials: 2**
Calculate maximum drawdown and recovery patterns. Computes peak-to-trough metrics, underwater equity curves, recovery time estimates, and stress tests against historical crash scenarios.

### `sharpe_ratio_calc` — Sharpe Ratio Calculator
**Price: $0.05 | Free trials: 2**
Compute risk-adjusted return metrics for any wallet or token. Calculates Sharpe, Sortino, and Calmar ratios with configurable benchmark and rolling window parameters.

### `volume_profile` — Volume Profile
**Price: $0.05 | Free trials: 2**
Analyze volume distribution across price levels. Identifies volume nodes, point-of-control zones, high-value nodes, and low-volume gaps that act as price magnets or barriers.

### `tax_lot_optimizer` — Tax Lot Optimizer
**Price: $0.05 | Free trials: 2**
Optimize tax lot identification for crypto portfolios. Identifies tax-loss harvesting opportunities, computes FIFO/LIFO/Specific ID outcomes, and generates compliant lot assignment recommendations.

### `sector_rotation` — Sector Rotation Tracker
**Price: $0.05 | Free trials: 2**
Track capital rotation between crypto sectors and narratives. Monitors DeFi, L1/L2, gaming, AI, and meme sector flows, identifying leading and lagging rotation patterns.

---

## Social Tools (11)

### `sentiment` — Sentiment Analysis
**Price: $0.03 | Free trials: 0**
Crypto sentiment analysis across Twitter, Telegram, and Discord. Detects bot activity, organic hype, and coordinated shilling.

### `sentiment_spike` — Sentiment Spike Detector
**Price: $0.05 | Free trials: 0**
Real-time social media volume anomalies and sentiment shifts for any token.

### `social_signal` — Social Signal Analyzer
**Price: $0.10 | Free trials: 0**
Twitter profile analysis, timeline engagement, CryptoPanic news sentiment, on-chain token correlation. Sentiment score and recommendation.

### `tw_profile` — Twitter Profile
**Price: $0.01 | Free trials: 0**
Get Twitter/X user profile data. Followers, bio, tweet count, verification status.

### `tw_timeline` — Twitter Timeline
**Price: $0.01 | Free trials: 0**
Get a user's recent tweets. Text, engagement, timestamps, media flags. Up to 100 tweets.

### `tw_search` — Twitter Search
**Price: $0.01 | Free trials: 0**
Search Twitter/X for tweets matching a query. Returns text, engagement, timestamps.

### `kol_performance` — KOL Performance Tracker
**Price: $0.10 | Free trials: 0**
Measure influencer call accuracy, average ROI after calls, follower quality score.

### `meme_vibe_score` — Meme Vibe Score
**Price: $0.01 | Free trials: 1**
Meme token vibe scoring. Sentiment, community strength, and virality analysis.

### `reddit_sentiment` — Reddit Sentiment
**Price: $0.05 | Free trials: 2**
Aggregate and score Reddit crypto community sentiment. Analyzes post frequency, comment polarity, subreddit engagement velocity, and identifies narrative shifts across crypto subreddits.

### `discord_alpha` — Discord Alpha Monitor
**Price: $0.05 | Free trials: 2**
Monitor Discord servers for early alpha signals. Detects project announcements, team member activity spikes, and insider conversation patterns before information reaches public channels.

### `influencer_impact_score` — Influencer Impact Score
**Price: $0.08 | Free trials: 2**
Quantify specific influencer impact on token prices. Measures post-to-pump latency, 24h post-impact ROI, audience authenticity, and distinguishes organic vs paid shill influence.

### `github_developer_activity` — GitHub Developer Activity
**Price: $0.05 | Free trials: 2**
Track blockchain project developer commit activity. Monitors code velocity, contributor count trends, issue resolution speed, and flags abandoned or suddenly-resumed repositories.

### `telegram_pump_detect` — Telegram Pump Detector
**Price: $0.05 | Free trials: 2**
Detect coordinated pump groups on Telegram channels. Identifies pre-pump coordination messages, group call timing patterns, and cross-references with on-chain volume anomalies.

---

## Launchpad Tools (7)

### `launch` — Token Launch Analysis
**Price: $0.03 | Free trials: 2**
Bonding curve status, liquidity locks, initial holder distribution, and fair launch verification.

### `launch_intel` — Launch Intelligence
**Price: $0.05 | Free trials: 2**
PumpFun new tokens, DexScreener trending pairs, bonding curve tracking, market summary, risk alerts for coordinated launches.

### `airdrop_finder` — Airdrop Finder
**Price: $0.05 | Free trials: 2**
Discover active and upcoming airdrops across all major chains. Eligibility checks, value estimation, claim deadlines, and Sybil detection.

### `presale_scanner` — Presale Scanner
**Price: $0.05 | Free trials: 2**
Scan and risk-score upcoming token presales. Evaluates team verification, hard cap vs soft cap ratio, vesting terms, contract audit status, and community authenticity signals.

### `ido_tracker` — IDO Tracker
**Price: $0.05 | Free trials: 2**
Track initial DEX offering launches and participation metrics. Monitors IDO schedules, raise progress, oversubscription rates, and historical post-IDO performance patterns.

### `fair_launch_detect` — Fair Launch Detector
**Price: $0.05 | Free trials: 2**
Detect and verify fair launch token distribution parameters. Checks for no team allocation, renounced ownership, locked liquidity, and equal-opportunity buy conditions.

### `vesting_schedule_analyzer` — Vesting Schedule Analyzer
**Price: $0.05 | Free trials: 2**
Analyze token vesting schedules and cliff impacts. Maps unlock timelines, calculates dilution pressure at each vesting event, and correlates scheduled unlocks with historical price impact.

---

## Premium Tools (7)

### `forensic_valuation` — Forensic Valuation
**Price: $0.25 | Free trials: 1**
Institutional-grade token valuation. DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring.

### `osint_identity_hunt` — OSINT Identity Hunt
**Price: $0.15 | Free trials: 2**
Cross-platform OSINT investigation. Hunt usernames across 400+ networks, domain intelligence, stealth page capture.

### `investigation_report` — Investigation Report
**Price: $0.20 | Free trials: 1**
Full investigation report. On-chain forensics, financial valuation, OSINT findings, scam scoring in one deliverable.

### `deep_forensics` — Deep Forensics
**Price: $0.25 | Free trials: 1**
Institutional deep-dive forensic analysis. Complete contract bytecode decompilation, variable state reconstruction, hidden function discovery, and multi-vector exploit scenario modeling.

### `whale_network_map` — Whale Network Map
**Price: $0.20 | Free trials: 1**
Map complete whale wallet interconnection networks. Reveals shared funding sources, coordinated allocation patterns, and influence cascades between top-100 holders across all chains.

### `cross_chain_trace` — Cross-Chain Trace
**Price: $0.15 | Free trials: 1**
Trace funds across blockchain bridges and mixers. Follows tainted money through bridge hops, mixer obfuscation, DEX swaps, and cross-chain routing with confidence scoring.

### `full_wallet_dossier` — Full Wallet Dossier
**Price: $0.30 | Free trials: 1**
Complete dossier on any wallet combining all intelligence. Behavioral profiling, P&L history, counterparty network, risk scoring, cross-chain footprint, and OSINT identity correlation.

---

## DeFi Tools (4)

### `defi_yield_scanner` — DeFi Yield Scanner
**Price: $0.08 | Free trials: 1**
Finds best APYs across chains. Detects unsustainable yields, checks protocol TVL trends, impermanent loss risk, smart contract age.

### `yield_aggregator` — Yield Aggregator
**Price: $0.05 | Free trials: 2**
Aggregate and compare yields across DeFi protocols. Ranks staking, lending, and LP opportunities by risk-adjusted APY, accounting for impermanent loss, smart contract risk, and fee structure.

### `impermanent_loss` — Impermanent Loss Calculator
**Price: $0.05 | Free trials: 2**
Calculate impermanent loss for liquidity provision positions. Computes current IL, projects worst-case scenarios, compares IL versus hold returns, and recommends optimal rebalancing intervals.

### `protocol_risk` — Protocol Risk Assessment
**Price: $0.08 | Free trials: 0**
TVL stability analysis, admin key review, upgrade patterns, oracle dependency, governance risk scoring.

### `liquidation_cascade` — Liquidation Cascade Risk Analyzer
**Price: $0.15 | Free trials: 1**
Cross-chain DeFi position monitoring across Aave, Compound, and Radiant. Computes health factors, identifies liquidation clusters, simulates cascade scenarios under 10%/25%/worst-case market drops. Reveals hidden systemic risk before liquidations trigger.
- **Risk tiers:** SAFE / WATCH / DANGER / CRITICAL with health factor thresholds
- **Scenarios:** 10% collateral drop, 25% flash crash, worst-case cascade
- **Chains:** Ethereum, Base, Arbitrum, Optimism, Polygon, BSC

---

## NFT Tools (2)

### `nft_wash_detector` — NFT Wash Trading Detector
**Price: $0.10 | Free trials: 1**
Identifies fake volume in NFT collections. Tracks floor price manipulation, finds collections with real organic demand vs artificial hype.

### `nft_floor_analytics` — NFT Floor Analytics
**Price: $0.05 | Free trials: 2**
Analyze NFT collection floor price trends and health. Tracks floor price support levels, wash trading indicators, unique holder growth, and listing-to-sale ratio dynamics.

---

## Bundles (4)

### `security_pack` — Security Pack
**Price: $0.10 (23% savings) | Free trials: 1**
honeypot_check + rug_pull_predictor + audit + clone_detect

### `intelligence_pack` — Intelligence Pack
**Price: $0.25 (29% savings) | Free trials: 1**
whale + smartmoney + cluster

### `all_in_one` — All-in-One Audit
**Price: $0.35 (30% savings) | Free trials: 1**
Complete security scan combining all security tools in a single comprehensive report.

### `forensic_pack` — Forensic Pack
**Price: $0.40 (33% savings) | Free trials: 1**
forensic_valuation + osint_identity_hunt + investigation_report

---

## API / Meta Tools (3)

### `catalog` — Tool Catalog
**Price: $0.00 | Free trials: 999**
Browse available tools, pricing, and chain support.

### `mcp-proxy` — MCP Protocol Proxy
**Price: $0.01 | Free trials: 5**
Route MCP tool calls through the x402 payment layer.

### `human-execute` — Human-in-the-Loop Execution
**Price: $0.02 | Free trials: 2**
Wallet-based payment for manual crypto investigation tasks.

---

## Per-Chain Variants (80)

Each of the 20 most popular tools is available in per-chain variants for Solana, Base, Ethereum, and BSC. These are accessed by appending the chain name (e.g., `honeypot_check_solana`, `wallet_base`, `whale_scan_ethereum`).

| Base Tool | Solana | Base | Ethereum | BSC |
|:---|:---:|:---:|:---:|:---:|
| `honeypot_check` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `rug_pull_predictor` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `rugshield` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `scam_database` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `wallet` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `wallet_pnl` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `whale_scan` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `smart_money_alpha` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `forensics` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `audit` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `fresh_pair` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `sniper_alert` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `liquidity_depth` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `arbitrage_scan` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `sentiment_spike` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `cluster` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `insider` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `deployer_history` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `alpha_digest` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |
| `urlcheck` | ✅ $0.03 | ✅ $0.03 | ✅ $0.04 | ✅ $0.04 |

---

## Discovery Endpoints

Six discovery and tool-format endpoints are available:

| Endpoint | Format | URL |
|:---|:---|:---|
| Discovery | x402 v2 | `GET /api/v1/x402-tools/discovery` |
| Catalog | JSON | `GET /api/v1/x402-tools/catalog` |
| OpenAI | Function calling | `GET /api/v1/x402-tools/openai-tools` |
| Anthropic | Tool use | `GET /api/v1/x402-tools/anthropic-tools` |
| Gemini | Function declarations | `GET /api/v1/x402-tools/gemini-tools` |
| LangChain | Tool schema | `GET /api/v1/x402-tools/langchain-tools` |

All endpoints return the complete list of **70+ security engines** in their respective formats.

---

## Payment

All tools support free trials (1-5 calls) then x402 micropayments.
Pay with USDC on 13 payment rails, USDT on TRON/BSC, BTC, or EUR via SEPA.
Full refund if tool returns no data within 48 hours.
