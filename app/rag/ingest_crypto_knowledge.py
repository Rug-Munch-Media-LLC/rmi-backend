#!/usr/bin/env python3
"""
Crypto Knowledge Ingestion Script
=================================
Populates RAG vector collections with curated crypto security knowledge.

Usage:
    python app/rag/ingest_crypto_knowledge.py [--base-url http://localhost:8000]
                                              [--collection scam_intel]
                                              [--dry-run]
                                              [--incidents]

Collections populated:
    scam_intel       — Common scam patterns, honeypots, rug pulls, flash loans
    deployer_history — Historical incidents (LUNA, FTX, Squid Game, etc.)
    contract_audit   — Token analysis methodology, audit patterns
    phishing_db      — DeFi concepts, terminology, security concepts
    wallet_labels    — Technical analysis patterns (support/resistance, indicators)

``--incidents`` additionally fetches public scam/rug/exploit incident feeds
(crawl_sources.json → incident_sources: DefiLlama hacks, REKT leaderboard,
SlowMist hacked, CertiK alerts), normalizes each record into a consistent
shape and ingests it into the matching RAG collection(s) (known_scams,
scam_patterns, defi_exploits, threat_intel). Best-effort: network failures
log and return empty — never raise.

``--docs`` fetches the raw markdown / reST / AsciiDoc knowledge sources in the
``education`` category (crawl_sources.json → incident_sources: ConsenSys
smart-contract-best-practices, crytic/not-so-smart-contracts, kadenzipfel/
smart-contract-vulnerabilities, Solidity security-considerations, OpenZeppelin
upgradeable docs), splits each doc into heading-aware chunks, dedupes by URL +
content hash and ingests into the target collection(s) (coding_knowledge,
threat_intel). Fail-open: a source that 404s / times out / yields no usable
chunks is skipped and recorded — never raise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger
from app.rag._crawler_constants import (
    INCIDENT_CATEGORIES,
    INCIDENT_CATEGORY_BY_SOURCE,
    INCIDENT_COLLECTION_BY_CATEGORY,
    INCIDENT_SOURCES_FILE,
)
from app.rag.config import RAG_CHUNK_OVERLAP, RAG_CHUNK_SIZE, RAG_MIN_CHUNK_CHARS
from app.rag.dedup import (
    content_hash,
    is_duplicate_content,
    is_duplicate_url,
    mark_content_ingested,
    mark_url_ingested,
)

log = get_logger(__name__)

# Bearer token kept for CSRF bypass; X-RMI-Key satisfies the auth middleware
# (Tier 1 shared secret) on the now-protected /api/v1/rag/v2/* write endpoints.
RAG_WRITE_HEADERS = {
    "Authorization": "Bearer sk-local",  # bypass CSRF middleware
    "X-RMI-Key": INTERNAL_API_KEY,  # auth middleware shared secret
    "Content-Type": "application/json",
}

# ── Knowledge bases ────────────────────────────────────────────────────

SCAM_INTEL: list[dict[str, Any]] = [
    {
        "doc_id": "scam:honeypot",
        "content": (
            "HONEYPOT SCAM: A token that allows buys but blocks sells. The contract contains a "
            "blacklist/whitelist mechanism where only specific addresses can sell. Key indicators: "
            "(1) Trading is enabled but transfers revert for non-whitelisted addresses when selling. "
            "(2) The contract uses OpenZeppelin's Ownable pattern with custom transfer restrictions. "
            "(3) Check if _beforeTokenTransfer or _transfer functions contain address checks that "
            "prevent certain addresses from transferring. (4) Use simulated sells to detect if the "
            "transaction would revert. (5) High buy pressure with zero sell volume is a red flag. "
            "Detection method: Deploy a simulation contract that attempts to buy then immediately "
            "sell. If the sell reverts with a custom error, it is a honeypot. Severity: CRITICAL."
        ),
        "metadata": {"severity": "critical", "category": "honeypot", "chain": "all"},
    },
    {
        "doc_id": "scam:rugpull",
        "content": (
            "RUG PULL DETECTION: A rug pull occurs when developers drain liquidity or mint/sell their "
            "tokens, crashing the price. Key patterns: (1) Liquidity pool has no lock or very short "
            "lock duration (<24h). (2) Large percentage of tokens held in a single wallet (deployer). "
            "(3) Mint function is not revoked — anyone can create unlimited tokens. (4) Ownership is "
            "not renounced and the owner can call functions that drain the pool. (5) High tax on "
            "transfers (buy/sell tax >10%) that funnels to deployer wallet. (6) Suspicious token "
            "distribution: large allocation to team/insider wallets without vesting. (7) Check for "
            "hidden mint functions or proxy patterns that allow contract upgrades. (8) Recent deployer "
            "activity: wallet has launched multiple tokens that all rugged. Severity: CRITICAL."
        ),
        "metadata": {"severity": "critical", "category": "rugpull", "chain": "all"},
    },
    {
        "doc_id": "scam:flash_loan",
        "content": (
            "FLASH LOAN ATTACK: Exploits that use uncollateralized flash loans to manipulate markets. "
            "Attack pattern: (1) Attacker takes a flash loan of a large amount of tokens. (2) Uses "
            "borrowed tokens to manipulate price oracle or DEX pool. (3) Exploits the manipulated "
            "price to drain another protocol. (4) Repays the flash loan within the same transaction. "
            "Key vulnerabilities: (1) Protocols using spot price from a single DEX as oracle. (2) "
            "Lack of slippage protection on swaps. (3) Token pairs with low liquidity that can be "
            "easily manipulated. (4) Protocols that don't validate oracle price against multiple "
            "sources. Mitigation: Use TWAP (Time-Weighted Average Price) oracles, Chainlink price "
            "feeds, or multi-source price validation. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "flash_loan", "chain": "all"},
    },
    {
        "doc_id": "scam:oracle_manipulation",
        "content": (
            "ORACLE MANIPULATION: Attackers manipulate the price feed that a protocol relies on. "
            "Types: (1) Spot price manipulation — attacker swaps tokens to move the DEX spot price, "
            "then exploits protocols using that price. (2) Oracle frontrunning — attacker sees a "
            "pending oracle update and frontruns it. (3) Stale oracle — protocol uses outdated "
            "price data. (4) Single-source oracle — relying on one DEX pair for price. Detection: "
            "Check if protocol uses TWAP or multiple oracle sources. Verify oracle update frequency. "
            "Test if a large swap significantly moves the reported price. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "oracle", "chain": "all"},
    },
    {
        "doc_id": "scam:sandwich_attack",
        "content": (
            "SANDWICH ATTACK: MEV attack where attacker places orders before and after a victim's "
            "transaction. Attack flow: (1) Attacker sees pending swap transaction in mempool. "
            "(2) Attacker submits buy order with higher gas before victim (frontrun). (3) Victim's "
            "swap executes at worse price due to slippage. (4) Attacker sells immediately after "
            "(backrun) for profit. Detection: Check for transactions in the same block where tokens "
            "are bought and sold around a target swap. Look for addresses consistently performing "
            "this pattern. Impact: Variable, typically 0.5-3% of trade value per attack. Mitigation: "
            "Use slippage protection, private mempools, or MEV-resistant DEX designs."
        ),
        "metadata": {"severity": "medium", "category": "mev", "chain": "all"},
    },
    {
        "doc_id": "scam:proxy_risk",
        "content": (
            "PROXY CONTRACT RISK: Upgradeable contracts that can be modified by the owner at any "
            "time. Red flags: (1) The implementation contract can be upgraded without timelock. "
            "(2) Owner is an EOA (Externally Owned Account), not a multisig. (3) No transparent "
            "proxy pattern used (UUPS proxies hide the implementation address). (4) The proxy "
            "admin can drain funds or rug through an upgrade. (5) Implementation is not verified "
            "on block explorer. Detection: Check for ERC-1967 proxy storage slots. Verify "
            "implementation contract source code. Check if there is a timelock on upgrades. "
            "Look for TransparentUpgradeableProxy or UUPS patterns. Severity: HIGH."
        ),
        "metadata": {"severity": "high", "category": "proxy", "chain": "evm"},
    },
    {
        "doc_id": "scam:fake_token",
        "content": (
            "FAKE TOKEN: A token impersonating a legitimate project to trick buyers. Indicators: "
            "(1) Token name/symbol matches a known project but contract address is different. "
            "(2) No liquidity or extremely low liquidity. (3) Contract is a direct copy of a "
            "legitimate token with no changes except deployer parameters. (4) Metadata (name, "
            "symbol, decimals) can be changed by owner. (5) Social media/website is fake or "
            "recently created. Detection: Verify contract address against official sources "
            "(CoinGecko, CoinMarketCap, project website). Check deployer history for similar "
            "tokens. Compare bytecode with known projects. Severity: MEDIUM."
        ),
        "metadata": {"severity": "medium", "category": "impersonation", "chain": "all"},
    },
    {
        "doc_id": "scam:dusting_attack",
        "content": (
            "DUSTING ATTACK: Attackers send tiny amounts of tokens (dust) to many wallets to "
            "track and de-anonymize them. Purpose: (1) Link wallet addresses to real identities "
            "by tracking when dust is moved or consolidated. (2) Phishing attacks — the dust "
            "transaction contains a memo with a malicious link. Prevention: (1) Don't interact "
            "with unknown tokens received. (2) Use privacy tools to consolidate funds. (3) "
            "Ignore dust amounts in wallet UI. Detection: Monitor for small unsolicited token "
            "transfers (<$0.01 value) from unknown addresses. Severity: LOW."
        ),
        "metadata": {"severity": "low", "category": "privacy", "chain": "all"},
    },
]

DEPLOYER_HISTORY: list[dict[str, Any]] = [
    {
        "doc_id": "history:luna_collapse",
        "content": (
            "LUNA/UST COLLAPSE (May 2022): Terra's algorithmic stablecoin UST lost its $1 peg, "
            "triggering a death spiral that wiped out ~$60B in value. Mechanism: (1) UST was "
            "backed by LUNA through an arbitrage mechanism — 1 UST could always be swapped for "
            "$1 worth of LUNA. (2) Large UST sell pressure caused the peg to break. (3) The "
            "arbitrage mechanism minted more LUNA to absorb UST sells, hyperinflating LUNA supply "
            "from ~350M to ~6.5T tokens. (4) Both LUNA and UST became essentially worthless. "
            "Key lessons: Algorithmic stablecoins without external collateral are vulnerable to "
            "death spirals. Always check the collateralization ratio and redemption mechanisms. "
            "Do Kwon was later arrested for fraud. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "luna", "date": "2022-05"},
    },
    {
        "doc_id": "history:ftx_collapse",
        "content": (
            "FTX COLLAPSE (November 2022): Centralized exchange FTX filed for bankruptcy after "
            "a bank run revealed an $8B hole in customer funds. Mechanism: (1) FTX had been "
            "loaning customer deposits to its sister trading firm Alameda Research. (2) Alameda "
            "used FTX's native FTT token as collateral. (3) When FTT price dropped after a "
            "CoinDesk report revealed Alameda's balance sheet was mostly FTT, the collateral "
            "became worthless. (4) Customers rushed to withdraw, revealing FTX was insolvent. "
            "Key lessons: (1) Never trust centralized exchanges with all your funds — use "
            "self-custody. (2) Exchange tokens used as collateral create circular risk. "
            "(3) Proof-of-reserves without proof-of-liabilities is meaningless. (4) Sam "
            "Bankman-Fried was convicted of fraud. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "ftx", "date": "2022-11"},
    },
    {
        "doc_id": "history:squid_game_token",
        "content": (
            "SQUID GAME TOKEN RUG PULL (November 2021): A token named after the Netflix show "
            "Squid Game rose from $0.01 to $2,861 before developers drained ~$3.3M. Mechanism: "
            "(1) Token had an anti-dumping mechanism that prevented selling. (2) Only the "
            "developers could sell. (3) After the price peak, developers withdrew all liquidity "
            "and disappeared. (4) The project's website and social media were deleted. Key "
            "indicators: (1) No sell functionality except for whitelisted addresses. (2) "
            "Anonymous team. (3) Unrealistic marketing promises. (4) Website registered "
            "anonymously. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "squid_game", "date": "2021-11"},
    },
    {
        "doc_id": "history:wormhole_hack",
        "content": (
            "WORMHOLE BRIDGE HACK (February 2022): The Wormhole cross-chain bridge was exploited "
            "for ~$326M (120K wETH). Mechanism: (1) Attacker exploited a signature verification "
            "vulnerability in the bridge's Solana program. (2) They minted 120K wETH on Solana "
            "without depositing equivalent ETH on Ethereum. (3) The attacker converted 93,750 "
            "wETH to ETH and bridged the rest. (4) Jump Crypto (parent company) replenished the "
            "funds to prevent ecosystem collapse. Key lessons: (1) Bridge contracts are high-value "
            "targets. (2) Cross-chain message verification must be foolproof. (3) Always check "
            "bridge audit reports and exploit history. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "wormhole", "date": "2022-02"},
    },
    {
        "doc_id": "history:ronin_bridge_hack",
        "content": (
            "RONIN BRIDGE HACK (March 2022): Axie Infinity's Ronin bridge was exploited for "
            "~$620M (173,600 ETH + 25.5M USDC). Mechanism: (1) Attacker gained control of 5 "
            "of 9 validator nodes (4 Sky Mavis validators + 1 Axie DAO validator). (2) Used "
            "compromised keys to approve two withdrawal transactions. (3) Funds were laundered "
            "through Tornado Cash and CEXs. (4) Lazarus Group (North Korea) was identified as "
            "the attacker. Key lessons: (1) Validator decentralization is critical — 5/9 "
            "multisig is centralized. (2) Always verify validator set distribution. (3) Bridge "
            "security requires defense-in-depth. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "ronin", "date": "2022-03"},
    },
    {
        "doc_id": "history:three_arrows_capital",
        "content": (
            "3AC (THREE ARROWS CAPITAL) COLLAPSE (June-July 2022): Major crypto hedge fund "
            "defaulted on $3.5B+ in loans after excessive leverage on LUNA/UST and GBTC trades. "
            "Mechanism: (1) 3AC was heavily leveraged on the LUNA ecosystem (lost ~$560M when "
            "LUNA collapsed). (2) Also had large GBTC (Grayscale Bitcoin Trust) position betting "
            "on NAV premium convergence that never happened. (3) When prices dropped, lenders "
            "issued margin calls that 3AC couldn't meet. (4) 3AC filed for bankruptcy, triggering "
            "contagion across crypto lending (Voyager, BlockFi, Genesis). Key lessons: (1) "
            "Counterparty risk in centralized lending. (2) Excessive leverage magnifies losses. "
            "(3) Concentration risk in correlated assets. Severity: HISTORICAL-HIGH."
        ),
        "metadata": {"severity": "high", "incident": "3ac", "date": "2022-06"},
    },
    {
        "doc_id": "history:multichain_exploit",
        "content": (
            "MULTICHAIN BRIDGE EXPLOIT (July 2023): ~$126M was drained from Multichain's "
            "Fantom and Moonriver bridges. Mechanism: (1) The CEO was arrested in China and the "
            "MPC keys were seized by authorities. (2) Funds were moved to unknown addresses. "
            "Key lessons: (1) Centralized key management is a single point of failure. (2) "
            "Founder/keyholder risk is real for bridges. (3) MPC is not a substitute for "
            "decentralization. Severity: HISTORICAL-CRITICAL."
        ),
        "metadata": {"severity": "critical", "incident": "multichain", "date": "2023-07"},
    },
]

CONTRACT_AUDIT: list[dict[str, Any]] = [
    {
        "doc_id": "audit:token_analysis_methodology",
        "content": (
            "TOKEN ANALYSIS METHODOLOGY: A systematic approach to evaluating token safety. "
            "Phase 1 - Basic Checks: (1) Verify contract is verified on block explorer. "
            "(2) Check token name, symbol, decimals are standard. (3) Identify if token "
            "is a proxy and check upgradeability. (4) Verify mint function is revoked or "
            "properly restricted. (5) Check if ownership is renounced. Phase 2 - Liquidity "
            "Analysis: (1) Check DEX liquidity amount and distribution. (2) Verify LP tokens "
            "are burned or locked. (3) Check lock duration on platforms like Unicrypt, "
            "Team Finance. (4) Analyze buy/sell tax rates. Phase 3 - Holder Analysis: "
            "(1) Check top 10 holder concentration. (2) Identify if deployer still holds "
            "large amounts. (3) Look for bundler/sniper bot wallets in early buys. "
            "(4) Check for wash trading patterns. Phase 4 - Social/Community: (1) Verify "
            "social media presence and engagement. (2) Check for paid engagement/bots. "
            "(3) Verify team is doxxed or anonymous. (4) Review audit reports if available."
        ),
        "metadata": {"category": "methodology", "severity": "informational"},
    },
    {
        "doc_id": "audit:liquidity_pool_safety",
        "content": (
            "LIQUIDITY POOL SAFETY ANALYSIS: Key checks for DEX liquidity pools. (1) LP "
            "token status: Are LP tokens burned (sent to dead address) or locked in a "
            "time-lock contract? Burned LP tokens are ideal — they cannot be removed. "
            "Locked LP tokens expire after the lock period — check remaining duration. "
            "(2) Pool concentration: How many LP providers? Single provider = high risk. "
            "(3) Pool size relative to market cap: Low liquidity relative to FDV means "
            "high price impact on trades. (4) LP withdrawal history: Has the pool been "
            "recently drained or refilled? (5) Fee structure: Unusual fee tiers may "
            "indicate manipulation. (6) For Uniswap V3: Check concentrated liquidity "
            "ranges — large positions at current price can exit quickly."
        ),
        "metadata": {"category": "liquidity", "severity": "high"},
    },
    {
        "doc_id": "audit:tokenomics_check",
        "content": (
            "TOKENOMICS ANALYSIS: Evaluate token economic design for red flags. (1) Supply: "
            "Fixed or inflationary? Check if total supply can be increased. (2) Distribution: "
            "How are tokens allocated? Typical allocation: Team 10-20%, Investors 10-20%, "
            "Community/Ecosystem 30-50%, Liquidity 10-20%. Red flag: Team + insiders >50%. "
            "(3) Vesting: Are team/investor tokens vested? Check for linear vesting over "
            "1-4 years with cliff period. Red flag: No vesting or tokens unlock in <30 days. "
            "(4) Emission rate: For inflationary tokens, what is the annual inflation rate? "
            ">20% annual inflation is a red flag. (5) Burn mechanism: Is there a deflationary "
            "mechanism (buyback and burn, fee burn)? (6) Utility: What is the token actually "
            "used for? Governance tokens with no revenue share have limited value capture."
        ),
        "metadata": {"category": "tokenomics", "severity": "high"},
    },
    {
        "doc_id": "audit:holder_analysis",
        "content": (
            "HOLDER ANALYSIS TECHNIQUE: Analyzing wallet distributions to detect manipulation. "
            "(1) Top 10 concentration: If top 10 holders own >80% of supply, high centralization "
            "risk. (2) Deployer holdings: Check if the deployer wallet retains tokens. Large "
            "deployer holdings without timelock = rug risk. (3) Bundler detection: Look for "
            "wallets that bought in the same block as launch with identical amounts — these "
            "are likely bundled/sniper buys. Bundler presence suggests organized launch. "
            "(4) Wash trading: Look for wallets that repeatedly buy and sell the same amounts, "
            "creating fake volume. (5) Connected wallets: Use on-chain clustering to identify "
            "wallets funded from the same source (same funder = same entity). (6) Smart money: "
            "Are known successful traders/investors holding or avoiding the token?"
        ),
        "metadata": {"category": "holders", "severity": "medium"},
    },
    {
        "doc_id": "audit:tax_analysis",
        "content": (
            "TRANSFER TAX ANALYSIS: Understanding buy/sell taxes and their implications. "
            "(1) Tax structure: What are the buy and sell taxes? Typical: 0-5%. Red flag: "
            "Sell tax >10% or buy tax >5%. (2) Tax destination: Where do collected taxes go? "
            "Marketing wallet? LP? Burn address? Red flag: Taxes go to deployer wallet. "
            "(3) Tax mutability: Can tax rates be changed? Check for setTax or similar "
            "functions. (4) Tax exemption: Are there tax-exempt addresses? The deployer "
            "being tax-exempt is a red flag. (5) Max transaction amount: Is there a max "
            "tx limit that prevents whales from selling? This protects against dumps but "
            "also prevents legitimate exits."
        ),
        "metadata": {"category": "tax", "severity": "medium"},
    },
]

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
KNOWLEDGE_BASES: dict[str, list[dict[str, Any]]] = {
    "scam_intel": SCAM_INTEL,
    "deployer_history": DEPLOYER_HISTORY,
    "contract_audit": CONTRACT_AUDIT,
    "phishing_db": PHISHING_DB,
    "wallet_labels": WALLET_LABELS,
}


# ── Public scam/rug/exploit incident sources (RAG enrichment) ────────────────
# Sources are declared in crawl_sources.json under ``incident_sources`` and
# consumed by ``ingest_incidents_from_sources``. Every network touch is
# best-effort: failures log and return empty — never raise.

_REQUIRED_SOURCE_FIELDS = ("name", "type", "url", "category", "collections", "enabled")
_VALID_SEVERITIES = ("critical", "high", "medium", "low", "informational")
_BULK_CHUNK_SIZE = 500  # matches the /api/v1/rag/v2/bulk-ingest per-call limit


def load_incident_sources() -> list[dict[str, Any]]:
    """Load the incident source registry from ``crawl_sources.json``.

    Returns only entries that carry the required fields (name, url, category,
    collections, enabled) with a valid category. Malformed entries are logged
    and skipped. Never raises.
    """
    try:
        data = json.loads(INCIDENT_SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("incident_sources_load_failed", path=str(INCIDENT_SOURCES_FILE), error=str(e))
        return []
    raw_sources = []
    if isinstance(data, dict):
        block = data.get("incident_sources")
        if isinstance(block, dict):
            raw_sources = block.get("sources", [])
    if not isinstance(raw_sources, list):
        return []

    sources: list[dict[str, Any]] = []
    for entry in raw_sources:
        if not isinstance(entry, dict):
            continue
        if any(f not in entry for f in _REQUIRED_SOURCE_FIELDS):
            log.warning("incident_source_missing_fields", source=entry.get("name"))
            continue
        if entry.get("category") not in INCIDENT_CATEGORIES:
            log.warning("incident_source_bad_category", source=entry.get("name"), category=entry.get("category"))
            continue
        if entry.get("type") not in ("json_api", "html_page"):
            log.warning("incident_source_bad_type", source=entry.get("name"), type=entry.get("type"))
            continue
        if not isinstance(entry.get("collections"), list) or not entry["collections"]:
            log.warning("incident_source_bad_collections", source=entry.get("name"))
            continue
        sources.append({k: entry[k] for k in _REQUIRED_SOURCE_FIELDS if k in entry})
    return sources


def normalize_incident(raw: dict[str, Any], source: str) -> dict[str, Any] | None:
    """Normalize a raw incident record into the canonical shape.

    Returns ``{title, description, chain, severity, category, amount_lost,
    date, source, url}`` or None when the record is malformed (not a dict or
    missing a usable title). Records without an explicit URL get a stable
    synthetic URL derived from (source, title) so URL-based dedup still works.
    Pure — no network, no I/O.
    """
    if not isinstance(raw, dict):
        return None
    raw_title = raw.get("title") or raw.get("name") or raw.get("project")
    if not isinstance(raw_title, str):
        return None
    title = raw_title.strip()
    if not title:
        return None

    description = str(
        raw.get("description")
        or raw.get("summary")
        or raw.get("details")
        or raw.get("technique")
        or raw.get("classification")
        or ""
    ).strip()
    category = raw.get("category")
    if category not in INCIDENT_CATEGORIES:
        category = INCIDENT_CATEGORY_BY_SOURCE.get(source, "scam")

    return {
        "title": title[:500],
        "description": description[:2000],
        "chain": _normalize_chain(raw.get("chain")),
        "severity": _normalize_severity(raw.get("severity")),
        "category": category,
        "amount_lost": _normalize_amount(
            raw.get("amount") or raw.get("amount_lost") or raw.get("losses") or raw.get("loss")
        ),
        "date": _normalize_date(raw.get("date")),
        "source": source,
        "url": _incident_url(raw, source, title),
    }


def dedupe_incidents(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop incidents whose URL was already seen in the batch (first wins).

    Pure URL-dedup helper — the runtime ingestion path additionally consults
    the shared Redis-backed ``app.rag.dedup`` helpers (fail-open) so URLs
    ingested on previous runs are also skipped.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for incident in incidents:
        url = str(incident.get("url") or "").strip().rstrip("/")
        key = url.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(incident)
    return out


def collection_names_for_category(category: str) -> tuple[str, ...]:
    """Resolve the RAG collections for an incident category (rug/exploit/scam)."""
    return INCIDENT_COLLECTION_BY_CATEGORY.get(category, INCIDENT_COLLECTION_BY_CATEGORY["scam"])


def _incident_doc_id(url: str) -> str:
    """Stable document id for an incident URL."""
    return f"incident:{hashlib.sha256(url.encode()).hexdigest()[:12]}"


def _synthetic_incident_url(source: str, title: str) -> str:
    """Stable synthetic URL for records without one (dedup key only)."""
    digest = hashlib.sha256(f"{source}:{title}".encode()).hexdigest()[:12]
    return f"https://{source}/incident/{digest}"


def _incident_url(raw: dict[str, Any], source: str, title: str) -> str:
    for key in ("url", "link", "source_url"):
        value = raw.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return _synthetic_incident_url(source, title)


def _normalize_chain(raw: Any) -> str:
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        return ", ".join(dict.fromkeys(parts)) or "unknown"
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "unknown"


def _normalize_severity(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _VALID_SEVERITIES else "medium"


def _normalize_amount(raw: Any) -> int:
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    cleaned = str(raw or "").strip().replace(",", "").replace("$", "")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _normalize_date(raw: Any) -> str:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC).isoformat()
        except (OSError, ValueError, OverflowError):
            return ""
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return value[:64]
    return ""


def _incident_payload(incident: dict[str, Any]) -> dict[str, Any]:
    """Build the bulk-ingest item (doc_id/content/metadata) for a record."""
    url = str(incident["url"])
    content = (
        f"{incident['title']}. Chain: {incident['chain']}. "
        f"Amount lost: ${int(incident['amount_lost']):,}. Date: {incident['date']}. "
        f"{incident['description']}"
    ).strip()
    metadata = {
        "source": incident["source"],
        "source_url": url,
        "incident_url": url,
        "category": incident["category"],
        "severity": incident["severity"],
        "chain": incident["chain"],
        "amount_lost": incident["amount_lost"],
        "date": incident["date"],
        "published_at": incident["date"],
        "content_type": "incident",
        "ingested_via": "incident_loader",
    }
    return {"doc_id": _incident_doc_id(url), "content": content, "metadata": metadata}


async def fetch_incident_source(source_cfg: dict[str, Any], max_records: int | None = None) -> list[dict[str, Any]]:
    """Fetch a JSON incident feed. Best-effort: any failure returns [] (logged)."""
    url = str(source_cfg.get("url") or "")
    if not url:
        return []
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "RMI-RAG-Enrichment/1.0"})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("incident_fetch_failed", source=source_cfg.get("name"), url=url, error=str(e))
        return []
    records = _extract_records(payload)
    if not isinstance(records, list):
        return []
    return records if max_records is None else records[:max_records]


def _extract_records(payload: Any) -> list[Any]:
    """Unwrap common API envelope shapes into a record list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "hacks", "incidents", "results", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


async def ingest_incidents_from_sources(
    base_url: str = "http://localhost:8000",
    sources: list[str] | None = None,
    dry_run: bool = False,
    max_per_source: int | None = 1000,
) -> dict[str, Any]:
    """Fetch → normalize → dedup → ingest public scam/exploit incident feeds.

    Pipeline per enabled ``json_api`` source in crawl_sources.json:
      1. ``fetch_incident_source``          — httpx GET (best-effort, [] on failure)
      2. ``normalize_incident``             — canonical record shape, drop invalid
      3. ``dedupe_incidents`` + dedup.is_duplicate_url — unique by URL (in-batch
         plus the shared Redis URL dedup, fail-open)
      4. map category → collection(s), bulk-ingest via the existing
         ``ingest_collection`` path (chunked at 500 items).

    Never raises: a failing source contributes an error entry to the summary.
    """
    configs = load_incident_sources()
    if sources is not None:
        wanted = set(sources)
        configs = [c for c in configs if c["name"] in wanted]
    enabled = [c for c in configs if c.get("enabled") is True and c.get("type") == "json_api"]
    if not enabled:
        log.info("incident_no_enabled_sources")
        return {"enabled": 0, "reason": "no enabled json_api sources", "sources": {}}

    summary: dict[str, Any] = {"enabled": len(enabled), "sources": {}, "total_normalized": 0}
    for source_cfg in enabled:
        name = str(source_cfg["name"])
        try:
            raw_records = await fetch_incident_source(source_cfg, max_records=max_per_source)
            normalized = [n for r in raw_records if (n := normalize_incident(r, name)) is not None]
            batch = dedupe_incidents(normalized)

            # Cross-time URL dedup via the shared dedup module (fail-open).
            fresh: list[dict[str, Any]] = []
            for incident in batch:
                if is_duplicate_url(str(incident["url"])):
                    continue
                fresh.append(incident)

            collections = tuple(
                source_cfg.get("collections") or collection_names_for_category(str(source_cfg["category"]))
            )
            per_collection: dict[str, list[dict[str, Any]]] = {col: [] for col in collections}
            for incident in fresh:
                payload = _incident_payload(incident)
                for col in collections:
                    per_collection[col].append(payload)

            ok_total = 0
            for col, items in per_collection.items():
                for start in range(0, len(items), _BULK_CHUNK_SIZE):
                    chunk = items[start : start + _BULK_CHUNK_SIZE]
                    result = await ingest_collection(base_url, col, chunk, dry_run=dry_run)
                    if not dry_run and "error" not in result:
                        ok_total += int(result.get("ok", 0) or 0)
            if not dry_run:
                for incident in fresh:
                    mark_url_ingested(str(incident["url"]))

            summary["sources"][name] = {
                "fetched": len(raw_records),
                "normalized": len(normalized),
                "deduped_in_batch": len(normalized) - len(batch),
                "skipped_already_ingested": len(batch) - len(fresh),
                "ingested": len(fresh) if dry_run else ok_total,
                "collections": list(collections),
            }
            summary["total_normalized"] += len(fresh)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("incident_source_failed", source=name, error=str(e))
            summary["sources"][name] = {"error": str(e)}
    return summary


async def ingest_collection(
    base_url: str,
    collection: str,
    items: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest all items into a collection via the bulk-ingest API."""
    if dry_run:
        return {"collection": collection, "items": len(items), "status": "dry_run"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(
                f"{base_url}/api/v1/rag/v2/bulk-ingest",
                json={"collection": collection, "items": items},
                headers=RAG_WRITE_HEADERS,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            return {"collection": collection, "error": str(e)}


async def ingest_doc_sources(
    base_url: str = "http://localhost:8000",
    sources: list[str] | None = None,
    dry_run: bool = False,
    chunk_size: int = RAG_CHUNK_SIZE,
    chunk_overlap: int = RAG_CHUNK_OVERLAP,
    min_chunk_chars: int = RAG_MIN_CHUNK_CHARS,
) -> dict[str, Any]:
    """Fetch → chunk → dedup → ingest raw markdown/reST/AsciiDoc knowledge.

    Pipeline per enabled ``education`` source in crawl_sources.json:
      1. ``fetch_doc_source``       — httpx GET the raw doc (best-effort)
      2. URL dedup                  — dedup.is_duplicate_url (fail-open)
      3. ``split_doc_into_chunks``  — heading-aware chunking
      4. content dedup              — per-chunk dedup.content_hash + an
         in-batch hash set, plus dedup.is_duplicate_content per collection
         (both fail-open)
      5. bulk-ingest each configured collection via the existing
         ``ingest_collection`` path (embedding happens server-side, same as
         the incident loader)
      6. mark the URL + chunk content hashes ingested (fail-open)

    Fail-open contract: a source that 404s, times out, returns empty/parses to
    no usable chunks, or is already known is skipped and recorded in the
    summary — never raises.
    """
    configs = load_doc_sources()
    if sources is not None:
        wanted = set(sources)
        configs = [c for c in configs if c["name"] in wanted]
    enabled = [c for c in configs if c.get("enabled") is True]
    if not enabled:
        log.info("doc_no_enabled_sources")
        return {"enabled": 0, "reason": "no enabled education sources", "sources": {}}

    summary: dict[str, Any] = {"enabled": len(enabled), "sources": {}, "total_chunks": 0}
    for source_cfg in enabled:
        name = str(source_cfg["name"])
        url = str(source_cfg["url"])
        try:
            # Cheapest gate first: cross-time URL dedup (fail-open).
            if is_duplicate_url(url):
                summary["sources"][name] = {"url": url, "skipped": "url_already_ingested"}
                continue

            text = await fetch_doc_source(source_cfg)
            if text is None:
                summary["sources"][name] = {"url": url, "error": "fetch_failed"}
                continue

            chunks = split_doc_into_chunks(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                min_chunk_chars=min_chunk_chars,
            )
            if not chunks:
                summary["sources"][name] = {"url": url, "skipped": "no_usable_chunks"}
                continue

            collections = tuple(source_cfg.get("collections") or ())
            category = str(source_cfg.get("category") or "education")
            fresh: list[dict[str, Any]] = []
            seen_hashes: set[str] = set()
            for idx, chunk in enumerate(chunks):
                fp = content_hash(chunk)
                if fp in seen_hashes:
                    continue
                # Cross-time content dedup per target collection (fail-open).
                if any(is_duplicate_content(fp, col) for col in collections):
                    continue
                seen_hashes.add(fp)
                fresh.append(_doc_payload(source=name, url=url, chunk=chunk, index=idx, category=category))

            ok_total = 0
            for col in collections:
                for start in range(0, len(fresh), _BULK_CHUNK_SIZE):
                    batch = fresh[start : start + _BULK_CHUNK_SIZE]
                    result = await ingest_collection(base_url, col, batch, dry_run=dry_run)
                    if not dry_run and "error" not in result:
                        ok_total += int(result.get("ok", 0) or 0)

            if not dry_run and fresh:
                mark_url_ingested(url)
                for item in fresh:
                    fp = content_hash(item["content"])
                    for col in collections:
                        mark_content_ingested(fp, col)

            summary["sources"][name] = {
                "url": url,
                "chars": len(text),
                "chunks": len(chunks),
                "deduped": len(chunks) - len(fresh),
                "ingested": len(fresh) if dry_run else ok_total,
                "collections": list(collections),
            }
            summary["total_chunks"] += len(fresh)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("doc_source_failed", source=name, error=str(e))
            summary["sources"][name] = {"url": url, "error": str(e)}
    return summary


def load_doc_sources() -> list[dict[str, Any]]:
    """Load the ``education`` raw-doc source registry from crawl_sources.json.

    Reuses the validated incident-source loader (required fields + valid
    category/type) and keeps only the ``education`` category — the sources the
    raw markdown/reST/AsciiDoc ingest path is responsible for. Never raises.
    """
    return [s for s in load_incident_sources() if s.get("category") == "education"]


async def fetch_doc_source(source_cfg: dict[str, Any]) -> str | None:
    """Fetch a raw markdown/reST/AsciiDoc file. Best-effort: None on failure."""
    url = str(source_cfg.get("url") or "")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "RMI-RAG-Docs/1.0"})
            resp.raise_for_status()
            text = resp.text
    except httpx.HTTPError as e:
        log.warning("doc_fetch_failed", source=source_cfg.get("name"), url=url, error=str(e))
        return None
    if not text or not text.strip():
        log.warning("doc_fetch_empty", source=source_cfg.get("name"), url=url)
        return None
    return text


# ── Heading-aware doc chunking (markdown / reST / AsciiDoc) ────────────────
# Converts reST underline + AsciiDoc ``=`` headings into markdown ``#`` form so
# a single splitter handles all three formats. Section boundaries preserve the
# heading line as context; over-long sections are recursively split at
# paragraph/sentence/word boundaries (langchain-style greedy packing).

_DOC_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+([^\n]*?)[ \t]*$", re.MULTILINE)
_RST_UNDERLINE_RE = re.compile(r"^([^\n].*?)\n([=\-~^\"'`:.+*#<>]{3,})[ \t]*$", re.MULTILINE)
_ADOC_HEADING_RE = re.compile(r"^(=+)[ \t]+([^\n]*?)[ \t]*$", re.MULTILINE)


def _normalize_doc_headings(text: str) -> str:
    """Convert reST underline + AsciiDoc ``=`` headings to markdown ``#`` form.

    Only used to make the heading-aware split format-agnostic; the body text is
    otherwise preserved verbatim. Markdown docs pass through unchanged.
    """
    text = _ADOC_HEADING_RE.sub(lambda m: f"{'#' * min(len(m.group(1)), 6)} {m.group(2)}", text)
    text = _RST_UNDERLINE_RE.sub(lambda m: f"# {m.group(1)}", text)
    return text


def _split_at_headings(text: str) -> list[tuple[str, str]]:
    """Split normalized markdown into ``(heading, body)`` sections."""
    matches = list(_DOC_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = m.group(0).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((heading, text[body_start:body_end].strip()))
    return sections


def _recursive_char_split(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Greedy character split preferring paragraph → sentence → word breaks."""
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        split_at = -1
        for sep in ("\n\n", "\n", ". ", " "):
            pos = text.rfind(sep, start + chunk_size // 2, end)
            if pos != -1:
                split_at = pos + len(sep)
                break
        if split_at == -1 or split_at <= start:
            split_at = end
        piece = text[start:split_at].strip()
        if piece:
            chunks.append(piece)
        if split_at >= n:
            break
        next_start = split_at - chunk_overlap if chunk_overlap > 0 else split_at
        if next_start <= start:
            next_start = split_at
        start = next_start
    return chunks


def split_doc_into_chunks(
    text: str,
    chunk_size: int = RAG_CHUNK_SIZE,
    chunk_overlap: int = RAG_CHUNK_OVERLAP,
    min_chunk_chars: int = RAG_MIN_CHUNK_CHARS,
) -> list[str]:
    """Heading-aware split of raw markdown/reST/AsciiDoc into chunk strings.

    Sections are cut at heading boundaries; each chunk keeps its heading line
    as retrieval context. Over-long sections are recursively split at
    paragraph/sentence/word boundaries. Noise fragments below
    ``min_chunk_chars`` and exact duplicate chunks are dropped. Pure — no
    network, no I/O.
    """
    if not text or not text.strip():
        return []
    normalized = _normalize_doc_headings(text)
    out: list[str] = []
    for heading, body in _split_at_headings(normalized):
        section = f"{heading}\n{body}" if heading else body
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            out.append(section)
            continue
        for piece in _recursive_char_split(section, chunk_size, chunk_overlap):
            if heading and heading not in piece:
                out.append(f"{heading}\n{piece}")
            else:
                out.append(piece)
    seen: set[str] = set()
    final: list[str] = []
    for c in out:
        stripped = c.strip()
        if not stripped or len(stripped) < min_chunk_chars:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        final.append(stripped)
    return final


def _doc_payload(source: str, url: str, chunk: str, index: int, category: str = "education") -> dict[str, Any]:
    """Build the bulk-ingest item for one doc chunk."""
    digest = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:12]
    metadata = {
        "source": source,
        "source_url": url,
        "doc_url": url,
        "category": category,
        "content_type": "doc_chunk",
        "chunk_index": index,
        "ingested_via": "doc_loader",
    }
    return {"doc_id": f"doc:{digest}:{index}", "content": chunk, "metadata": metadata}


async def ingest_all(
    base_url: str = "http://localhost:8000",
    collections: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest all knowledge bases into their respective collections."""
    cols = collections or list(KNOWLEDGE_BASES.keys())
    results: dict[str, Any] = {}

    for collection in cols:
        items = KNOWLEDGE_BASES.get(collection)
        if not items:
            results[collection] = {"status": "skipped", "reason": "no knowledge base defined"}
            continue

        print(f"\n{'[DRY RUN] ' if dry_run else ''}Ingesting {len(items)} documents into '{collection}'...")
        result = await ingest_collection(base_url, collection, items, dry_run=dry_run)
        results[collection] = result
        status = result.get("status", result.get("error", "unknown"))
        ok_count = result.get("ok", result.get("accepted", 0))
        print(f"  {collection}: {status} (ok={ok_count}, total={len(items)})")
        # Small delay between collections to avoid rate limiting
        if not dry_run:
            time.sleep(1)

    return results


async def verify_ingestion(base_url: str = "http://localhost:8000") -> dict[str, Any]:
    """Check RAG stats after ingestion."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{base_url}/api/v1/rag/v2/stats",
            headers={"Authorization": "Bearer sk-local"},
        )
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest crypto knowledge into RAG")
    parser.add_argument(
        "--base-url",
        default=os.getenv("RMI_API_URL", "http://localhost:8000"),
        help="RMI backend base URL",
    )
    parser.add_argument(
        "--collection",
        nargs="*",
        choices=[*list(KNOWLEDGE_BASES.keys()), "all"],
        default=["all"],
        help="Collections to populate (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without actually doing it",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only verify current ingestion status",
    )
    parser.add_argument(
        "--incidents",
        action="store_true",
        help="Fetch + ingest public scam/rug/exploit incident sources (DefiLlama hacks, REKT, ...)",
    )
    parser.add_argument(
        "--docs",
        action="store_true",
        help="Fetch + ingest raw markdown/reST/AsciiDoc education sources (ConsenSys best practices, NotSoSmartContracts, ...)",
    )
    args = parser.parse_args()

    import asyncio

    if args.verify:
        stats = asyncio.run(verify_ingestion(args.base_url))
        print("\n=== RAG Stats ===")
        print(json.dumps(stats, indent=2))
        return

    if args.incidents:
        results = asyncio.run(ingest_incidents_from_sources(args.base_url, dry_run=args.dry_run))
        print("\n=== Incident Ingestion Results ===")
        print(json.dumps(results, indent=2))
        return

    if args.docs:
        results = asyncio.run(ingest_doc_sources(args.base_url, dry_run=args.dry_run))
        print("\n=== Doc Source Ingestion Results ===")
        print(json.dumps(results, indent=2))
        return

    collections = None if "all" in args.collection else args.collection

    if args.dry_run:
        total = sum(len(KNOWLEDGE_BASES.get(c, [])) for c in (collections or KNOWLEDGE_BASES))
        print(f"[DRY RUN] Would ingest {total} documents into {len(collections or KNOWLEDGE_BASES)} collections")
        # Show what would be sent
        for col_name, items in (collections or KNOWLEDGE_BASES).items():
            if isinstance(col_name, str) and col_name in KNOWLEDGE_BASES:
                print(f"\n  {col_name} ({len(items)} docs):")
                for item in items[:2]:
                    print(f"    - {item['doc_id']}")
                if len(items) > 2:
                    print(f"    ... and {len(items) - 2} more")
    else:
        results = asyncio.run(ingest_all(args.base_url, collections, dry_run=False))
        print("\n=== Ingestion Results ===")
        print(json.dumps(results, indent=2))

        # Verify
        print("\n=== Post-Ingestion Stats ===")
        stats = asyncio.run(verify_ingestion(args.base_url))
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
