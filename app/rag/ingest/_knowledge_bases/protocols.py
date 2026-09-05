"""Protocol / DeFi knowledge base entries (phishing DB + wallet labels)."""

from __future__ import annotations

from typing import Any

PHISHING_DB: list[dict[str, Any]] = [
    {
        "doc_id": "defi:amm_basics",
        "content": (
            "AUTOMATED MARKET MAKER (AMM): A decentralized exchange mechanism that uses "
            "liquidity pools instead of order books. Key concepts: (1) Constant Product "
            "Formula (x * y = k): Used by Uniswap V2, where x and y are token reserves "
            "and k is constant. Price is determined by the ratio of reserves. (2) "
            "Concentrated Liquidity (Uniswap V3): LPs provide liquidity within specific "
            "price ranges for better capital efficiency. (3) Impermanent Loss: The "
            "temporary loss LPs experience when token prices diverge from the deposit "
            "ratio. The greater the divergence, the larger the loss. (4) Slippage: The "
            "difference between expected and executed price due to trade size relative "
            "to pool liquidity. (5) Price Impact: How much a trade moves the pool price. "
            "Large trades in low-liquidity pools have high price impact. Common AMMs: "
            "Uniswap, SushiSwap, PancakeSwap, Curve (stablecoin-focused), Balancer."
        ),
        "metadata": {"category": "defi", "topic": "amm", "severity": "informational"},
    },
    {
        "doc_id": "defi:lending_protocols",
        "content": (
            "LENDING PROTOCOLS (DeFi): Platforms that allow users to lend and borrow crypto "
            "assets without intermediaries. Key concepts: (1) Collateralization Ratio: "
            "The ratio of collateral value to borrowed amount. Over-collateralized loans "
            "require >100% collateral (typically 150-200%). (2) Liquidation: When collateral "
            "value drops below the required ratio, the position is liquidated — collateral "
            "is sold to repay the loan, often with a penalty. (3) Health Factor: A measure "
            "of position safety. Health factor < 1 means liquidation is possible. (4) APY "
            "vs APR: APY includes compound interest. Supply APY is what lenders earn; "
            "borrow APY is what borrowers pay. (5) Flash Loans: Uncollateralized loans "
            "that must be borrowed and repaid in the same transaction. Used for arbitrage, "
            "liquidation, and (maliciously) for exploits. Major protocols: Aave, Compound, "
            "MakerDAO (now Sky), Venus, Morpho."
        ),
        "metadata": {"category": "defi", "topic": "lending", "severity": "informational"},
    },
    {
        "doc_id": "defi:yield_farming",
        "content": (
            "YIELD FARMING / LIQUIDITY MINING: Providing liquidity to DeFi protocols to "
            "earn rewards, typically in the protocol's native token. Key concepts: "
            "(1) APR/APY: Annual Percentage Rate/Yield — often extremely high initially "
            "to attract liquidity, then declines. (2) Reward tokens: The native token "
            "earned as rewards. If reward token price drops, real yield drops. (3) "
            "Impermanent Loss risk: LP positions can lose value compared to just holding "
            "the tokens. (4) Smart contract risk: Funds are locked in smart contracts "
            "that may have bugs or be exploited. (5) Rug pull risk: Protocol developers "
            "can drain funds if contracts are upgradeable or contain backdoors. (6) "
            "Compounding: Reinvesting rewards to earn yield on yield. Red flags: "
            "Unsustainably high APY (>1000%), anonymous team, no audit, no timelock "
            "on contract changes, reward token has no utility beyond farming."
        ),
        "metadata": {"category": "defi", "topic": "yield", "severity": "informational"},
    },
    {
        "doc_id": "defi:bridge_security",
        "content": (
            "CROSS-CHAIN BRIDGE SECURITY: Bridges enable token transfers between blockchains "
            "but are frequent exploit targets. Security models: (1) Lock-and-Mint: Original "
            "tokens are locked on source chain, wrapped tokens minted on destination. "
            "(2) Burn-and-Mint: Tokens are burned on source and minted on destination. "
            "(3) Liquidity Pool: Pre-funded pools of tokens on each chain. Key risks: "
            "(1) Validator compromise: If bridge validators are compromised, attacker can "
            "mint unbacked tokens. (2) Smart contract bugs: Verification/validation logic "
            "errors. (3) Centralization: Many bridges have centralized validator sets "
            "(e.g., 5-15 validators). (4) Upgradeability: Bridge contracts that can be "
            "upgraded without timelock. Red flags when evaluating: Bridge has been "
            "exploited before, small validator set, anonymous validators, unaudited code. "
            "Historical losses: Ronin $620M, Wormhole $326M, Multichain $126M, Nomad $190M."
        ),
        "metadata": {"category": "defi", "topic": "bridge", "severity": "high"},
    },
    {
        "doc_id": "defi:stablecoins",
        "content": (
            "STABLECOINS: Tokens designed to maintain a stable value, typically pegged to $1. "
            "Types: (1) Fiat-collateralized (USDC, USDT): Backed 1:1 by fiat reserves. "
            "Safest but centralized. (2) Crypto-collateralized (DAI, crvUSD): Backed by "
            "over-collateralized crypto positions. Decentralized but capital-inefficient. "
            "(3) Algorithmic (FRAX, formerly UST): Uses algorithms and incentives to "
            "maintain the peg without full collateral backing. Highest risk — UST death "
            "spiral showed fatal flaws. Key metrics: (1) Collateralization ratio: >100% "
            "means fully backed. (2) Peg stability: How often and how far from $1 does "
            "the price deviate? (3) Reserve composition: What backs the stablecoin? "
            "(4) Redemption mechanism: Can users redeem 1:1 for the underlying? "
            "(5) Depeg history: Has it ever lost significant value before? Red flags: "
            "Algorithmic with low collateral, opaque reserves, no redemption guarantee."
        ),
        "metadata": {"category": "defi", "topic": "stablecoin", "severity": "medium"},
    },
    {
        "doc_id": "defi:uniswap_v3_mechanics",
        "content": (
            "UNISWAP V3 / CONCENTRATED LIQUIDITY MECHANICS: LPs provide liquidity inside "
            "custom price ranges (ticks) instead of 0-to-infinity, earning higher fees per "
            "dollar when in range. Key mechanics: (1) Ticks: discrete price boundaries; "
            "positions are NFTs with custom ranges. (2) Tick ranges: liquidity is only "
            "active inside the range; when price exits, the position becomes single-sided. "
            "(3) TWAP oracles: v3 observes accumulated prices for TWAP, but short windows "
            "over thin ranges are manipulable. (4) JIT liquidity: bots add and remove "
            "concentrated liquidity in the same block as a large swap to capture fees. "
            "(5) Fee tiers: 0.05%, 0.3%, 1% routes change swap economics. Gotchas for "
            "token analysis: check liquidity distribution across ticks, not just TVL; "
            "check who holds active-range positions. Severity: informational."
        ),
        "metadata": {"category": "defi", "topic": "uniswap", "severity": "high"},
    },
    {
        "doc_id": "defi:curve_stableswap",
        "content": (
            "CURVE STABLE-SWAP POOL MECHANICS: Curve-style pools use a hybrid "
            "constant-sum/constant-product curve optimized for assets with similar value "
            "(stablecoins, liquid staking derivatives). Key mechanics: (1) Amplification "
            "coefficient (A) controls how flat the curve is near the peg; higher A = "
            "more efficient near peg, more vulnerable to imbalance. (2) Price stays near "
            "1:1 while pools are balanced; large imbalance moves price sharply. (3) "
            "Lending integrations (crvUSD, LLAMMA) price from Curve pools, so pool "
            "manipulation can cascade into liquidations. Gotchas: spot price from a thin "
            "Curve pool is flash-loan manipulable (Harvest Finance 2020); the 2023 "
            "Vyper bug hit Curve pools specifically. Check pool depth on both sides and "
            "whether TWAP or deviation guards protect any integration. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "curve", "severity": "high"},
    },
    {
        "doc_id": "defi:liquidation_auctions",
        "content": (
            "LIQUIDATION AUCTIONS AND CASCADES: Lending protocols close underwater "
            "positions by selling collateral, often at a discount via auction or a "
            "liquidation bonus. Mechanics: (1) Health factor < 1 triggers liquidation. "
            "(2) Liquidators race to close first and earn the bonus — MEV bots dominate. "
            "(3) Cascades: one liquidation drives price down, triggering more "
            "liquidations sharing the same price feed. (4) Bad debt: when collateral "
            "cannot cover the loan, the protocol eats the loss, hitting lenders. "
            "Gotchas: partial liquidation (Aave v3) dampens cascades; eMode "
            "correlated-asset baskets liquidate together; auction-style (Maker) has "
            "different dynamics than instant bonus liquidations. For token analysis, "
            "large borrowed positions against a volatile token are cascade fuel. "
            "Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "liquidation", "severity": "high"},
    },
    {
        "doc_id": "defi:erc4626_vaults",
        "content": (
            "ERC-4626 VAULT MECHANICS: The tokenized-vault standard (yield vaults, "
            "staking wrappers) prices shares as assets-per-share: shares = deposit / "
            "pricePerShare. Key mechanics: (1) Donation-inflatable balance: a large "
            "donation before/after deposits inflates pricePerShare, hurting late or "
            "early depositors (first-depositor / share-inflation attack). (2) "
            "Rounding: deposit rounds shares down and withdrawal rounds assets down, "
            "which over many ops can drain a vault (extreme case: the 2021 Yearn "
            "v1 weirding / 'morphing' incidents). (3) fee accounting: fees accrue "
            "inside pricePerShare, so share price is not flat. Gotchas: verify "
            "minimum first deposit, virtual shares/offset (OpenZeppelin v5), and "
            "donation resistance before trusting a vault. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "vault", "severity": "high"},
    },
    {
        "doc_id": "defi:solana_program_mechanics",
        "content": (
            "SOLANA PROGRAM / ACCOUNT MODEL MECHANICS: Solana programs (smart contracts) "
            "receive all state as explicit accounts, so account validation IS the "
            "security model. Key mechanics: (1) Every account must be checked for "
            "program ownership, key match, and signer status — missing checks let "
            "attackers pass fake mints or token accounts. (2) CPIs call other programs; "
            "reentrancy-style issues come from state changed between CPI and later "
            "checks. (3) Token-2022 extensions (transfer fees, transfer hooks, "
            "confidential transfers) add programmable behavior on every transfer. "
            "(4) Priority fees + Jito bundles determine MEV ordering. (5) Rent and "
            "close-account mechanics differ from EVM storage. Gotchas: verify mint "
            "authority, freeze authority, and extension list before trusting a Solana "
            "token; simulate the full instruction set. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "solana", "severity": "high"},
    },
    {
        "doc_id": "defi:governance_mechanics",
        "content": (
            "ON-CHAIN GOVERNANCE MECHANICS: DAO governance moves protocol parameters and "
            "treasury funds through proposals. Key mechanics: (1) Quorum: minimum votes "
            "for a proposal to pass; quorum that a flash loan can buy is attackable. "
            "(2) Timelock: delay between proposal passing and execution — the community "
            "window to respond; no timelock means instant execution. (3) Vote weight: "
            "token holdings, veTokens, or delegated votes; instant-borrowable weight is "
            "manipulable. (4) Gauge/bribe mechanics in veTokenomics direct emissions. "
            "(5) Emergency pause/guardian roles can halt protocol activity. Gotchas: "
            "Beanstalk 2022 showed flash-loan governance; low-turnout proposals get "
            "buried; proposals bundling innocuous changes with a transfer get rubber-"
            "stamped. Severity: high."
        ),
        "metadata": {"category": "defi", "topic": "governance", "severity": "high"},
    },
]

WALLET_LABELS: list[dict[str, Any]] = [
    {
        "doc_id": "ta:support_resistance",
        "content": (
            "SUPPORT AND RESISTANCE LEVELS: Technical analysis concepts for identifying "
            "price levels where buying or selling pressure is expected. Support: A price "
            "level where buying interest is strong enough to prevent further decline. "
            "Resistance: A price level where selling pressure prevents further increases. "
            "Identification methods: (1) Historical price reversals: Levels where price "
            "reversed multiple times. (2) Round numbers: Psychological levels (e.g., $1, "
            "$10, $100 for tokens; $50K, $100K for BTC). (3) Moving averages: Commonly "
            "watched MAs act as dynamic support/resistance (20, 50, 200 period). "
            "(4) Volume profile: Price levels with highest trading volume (Volume "
            "Weighted Average Price - VWAP). (5) Fibonacci retracement levels: 38.2%, "
            "50%, 61.8% of prior trend. Patterns: Support becomes resistance when broken "
            "(and vice versa). Multiple touches of a level strengthen its significance."
        ),
        "metadata": {"category": "technical_analysis", "topic": "support_resistance"},
    },
    {
        "doc_id": "ta:indicators",
        "content": (
            "TECHNICAL INDICATORS for crypto trading. Trend indicators: (1) Moving Averages "
            "(SMA/EMA): Simple and exponential moving averages. Golden cross (50 crosses "
            "above 200) = bullish. Death cross (50 below 200) = bearish. (2) MACD: Moving "
            "Average Convergence Divergence — shows trend direction and momentum. Momentum "
            "indicators: (3) RSI (Relative Strength Index): 0-100 scale. >70 = overbought, "
            "<30 = oversold. Divergence between price and RSI signals reversal. (4) "
            "Stochastic Oscillator: Compares closing price to price range. Volume indicators: "
            "(5) OBV (On-Balance Volume): Cumulative volume that adds on up days, subtracts "
            "on down days. (6) Volume Profile: Shows traded volume at each price level. "
            "Volatility: (7) Bollinger Bands: Price envelope at 2 standard deviations from "
            "SMA. Squeeze (bands tighten) precedes breakout. (8) ATR (Average True Range): "
            "Measures volatility. Higher ATR = more volatile. On-chain specific: (9) NVT "
            "Ratio: Network Value to Transactions ratio — high NVT = overvalued. (10) MVRV: "
            "Market Value to Realized Value — >3.7 historically indicates cycle top."
        ),
        "metadata": {"category": "technical_analysis", "topic": "indicators"},
    },
    {
        "doc_id": "ta:chart_patterns",
        "content": (
            "CHART PATTERNS in crypto trading. Reversal patterns: (1) Head and Shoulders: "
            "Three peaks, middle highest. Break below neckline = bearish reversal. Inverse "
            "head and shoulders = bullish reversal. (2) Double Top/Bottom: Two peaks/troughs "
            "at similar levels. Break of the middle level confirms reversal. (3) Cup and "
            "Handle: U-shaped recovery followed by small consolidation — bullish continuation. "
            "Continuation patterns: (4) Bull/Bear Flags: Sharp move (pole) followed by "
            "consolidation in a channel (flag). Breakout in original direction. (5) Pennants: "
            "Similar to flags but with converging trendlines. (6) Ascending/Descending "
            "Triangles: Flat top/rising bottom or flat bottom/falling top — breakout in "
            "direction of the flat side. (7) Symmetrical Triangle: Converging trendlines — "
            "breakout can go either way. Candlestick patterns: (8) Doji: Open and close "
            "are nearly equal — indecision. (9) Hammer/Hanging Man: Small body with long "
            "lower wick — reversal signal. (10) Engulfing pattern: Second candle completely "
            "engulfs first — strong reversal signal."
        ),
        "metadata": {"category": "technical_analysis", "topic": "patterns"},
    },
    {
        "doc_id": "ta:market_structure",
        "content": (
            "MARKET STRUCTURE ANALYSIS for crypto. Trend identification: (1) Higher Highs + "
            "Higher Lows = Uptrend. Lower Highs + Lower Lows = Downtrend. Equal Highs + "
            "Equal Lows = Range/sideways. (2) Market phases: Accumulation (range after "
            "downtrend, smart money buying), Markup (uptrend, retail enters), Distribution "
            "(range after uptrend, smart money selling), Markdown (downtrend, retail panic). "
            "(3) Wyckoff Method: Schematic for identifying accumulation/distribution phases "
            "through price and volume analysis. Liquidity concepts: (4) Order blocks: Zones "
            "where large orders were previously executed — likely areas of support/resistance. "
            "(5) Fair Value Gaps (FVG): Price inefficiencies that tend to get filled. (6) "
            "Liquidity grabs: Fake breakouts that trap traders before reversing. Multi-timeframe "
            "analysis: (7) Higher timeframe = trend direction. (8) Intermediate timeframe = "
            "trade setup. (9) Lower timeframe = entry. Crypto-specific: (10) Exchange orderbook "
            "depth — large bid/ask walls indicate support/resistance. (11) Funding rates: "
            "Positive = longs pay shorts (bullish sentiment), negative = shorts pay longs "
            "(bearish sentiment). Extreme funding rates signal potential reversals."
        ),
        "metadata": {"category": "technical_analysis", "topic": "market_structure"},
    },
    {
        "doc_id": "ta:onchain_metrics",
        "content": (
            "ON-CHAIN METRICS for crypto analysis. Network health: (1) Active addresses: "
            "Daily/weekly/monthly unique addresses interacting with the chain. (2) Transaction "
            "count: Total and average transactions per day. (3) Hash rate: For PoW chains — "
            "higher = more secure. (4) Staking ratio: For PoS chains — percentage of supply "
            "staking. Valuation metrics: (5) NVT Ratio = Market Cap / Daily Transaction "
            "Volume. High NVT = network valued higher than its transaction utility suggests. "
            "(6) MVRV = Market Cap / Realized Cap. Realized cap values each coin at its "
            "last movement price. MVRV > 3.7 historically signals market top. (7) Puell "
            "Multiple = Daily miner revenue (USD) / 365-day MA of miner revenue. High value "
            "suggests miners may sell. (8) SOPR (Spent Output Profit Ratio) > 1 means coins "
            "are being sold at profit. Holder behavior: (9) HODL Waves: Distribution of "
            "coins by age since last moved. Older coins moving = potential sell pressure. "
            "(10) Exchange inflows/outflows: Large inflows to exchanges = potential selling. "
            "Large outflows = accumulation. (11) Whale activity: Transactions >$1M — "
            "tracking whale accumulation/distribution. (12) Supply in profit/loss: "
            "Extreme values signal market tops/bottoms."
        ),
        "metadata": {"category": "onchain", "topic": "metrics"},
    },
]

# Map collection names to knowledge bases
