#!/usr/bin/env python3
"""Populate empty RAG collections with scam patterns, contract audits, and transaction patterns."""

import json
import urllib.request

API = "http://localhost:8000/api/v1/rag/ingest"


def ingest(collection, documents):
    payload = json.dumps({"collection": collection, "documents": documents}).encode()
    req = urllib.request.Request(API, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        print(f"  {collection}: ingested {len(documents)} docs → {result}")
        return True
    except Exception as e:
        print(f"  {collection}: FAILED - {e}")
        return False


# ── SCAM PATTERNS (50 patterns) ──────────────────────────────────────
scam_patterns = [
    {
        "id": "sp-001",
        "title": "Liquidity Removal Rug Pull",
        "content": "Liquidity Removal Rug Pull. Severity: CRITICAL. Category: rug_pull. Developer removes all liquidity from DEX pool after attracting buyers, making tokens untradeable. Indicator: LP tokens held in deployer wallet, no timelock on liquidity. Code patterns: removeLiquidity(), remove_liquidity(), withdrawLiquidity(). Detection: Monitor LP token balance of deployer, check if liquidity is locked. Real examples: Meerkat Finance AVAX $31M, Merlin DEX BSC $4.8M.",
        "category": "rug_pull",
    },
    {
        "id": "sp-002",
        "title": "Mint-and-Dump",
        "content": "Mint-and-Dump. Severity: CRITICAL. Category: rug_pull. Contract has hidden or unlimited mint function that allows owner to mint new tokens and sell them, diluting holder value. Indicator: Unchecked mint function, _mint() with no cap, owner-only mint. Code: mint(), _mint(to, amount), increaseSupply(). Detection: Check contract for mint functions without supply cap, verify if mint is timelocked. Function selectors: 0xa0712d68 (mint), 0x40c10f19 (mint address amount).",
        "category": "rug_pull",
    },
    {
        "id": "sp-003",
        "title": "Honeypot Token",
        "content": "Honeypot Token. Severity: CRITICAL. Category: rug_pull. Token can be purchased but cannot be sold due to transfer restrictions, trading taxes, or blacklisting. Buyers lose entire investment. Indicator: 100% sell tax, transfer blocklist, isExcluded check on transfer. Code: _beforeTokenTransfer override, transferFrom restrictions, _taxRate > 90%. Detection: Simulate buy+sell transaction, check for transfer restrictions. Real examples: Squid Game Token BSC.",
        "category": "rug_pull",
    },
    {
        "id": "sp-004",
        "title": "Hidden Self-Destruct",
        "content": "Hidden Self-Destruct. Severity: CRITICAL. Category: rug_pull. Contract contains selfdestruct or suicide function that allows owner to kill contract and steal remaining ETH. Indicator: selfdestruct, suicide, destroy calls. Code: selfdestruct(owner), destroy(owner). Detection: Scan bytecode for SELFDESTRUCT opcode 0xff, check for owner-only destroy functions. Real examples: Parity wallet hack $150M.",
        "category": "rug_pull",
    },
    {
        "id": "sp-005",
        "title": "Backdoor Transfer Pause",
        "content": "Backdoor Transfer Pause. Severity: HIGH. Category: rug_pull. Contract owner can pause all transfers preventing holders from selling. Indicator: Pausable with owner-controlled pause, onlyOwner on pause(). Code: pause(), _pause(), setTradingEnabled(false). Detection: Check for Pausable inheritance, onlyOwner on pause functions. Function selectors: 0x8456cb59 (pause), 0x3f4ba83a (unpause).",
        "category": "rug_pull",
    },
    {
        "id": "sp-006",
        "title": "Tax Modification Rug Pull",
        "content": "Rug Pull via Tax Modification. Severity: CRITICAL. Category: rug_pull. Contract owner can change buy/sell tax rates, increasing sell tax to 100% making token untradeable. Indicator: setTaxRate(), setSellFee(), updateFee() functions with no cap on fee values. Detection: Scan for fee-setting functions, check if fees are capped. Function selectors: 0x4b2e2f5b (setTaxRate), 0xdb006a75 (setFee).",
        "category": "rug_pull",
    },
    {
        "id": "sp-007",
        "title": "Blacklist Rug Pull",
        "content": "Blacklist Rug Pull. Severity: CRITICAL. Category: rug_pull. Contract has blacklist function that owner uses to prevent specific addresses from selling. After accumulating buyers, owner blacklists major holders. Indicator: blacklist(), setBlacklist(), isBlacklisted mapping, _beforeTokenTransfer check. Detection: Scan for blacklist functions. Function selector: 0xe9a4db3e (setBlacklistAddress).",
        "category": "rug_pull",
    },
    {
        "id": "sp-008",
        "title": "Hidden Owner Privilege Escalation",
        "content": "Hidden Owner Privilege Escalation. Severity: HIGH. Category: rug_pull. Contract appears to have renounced ownership but hidden mechanism allows original owner to regain control. Indicator: pendingOwner variable, ROLE_ADMIN pattern, two-step ownership transfer. Detection: Check for hidden ownership patterns, verify renounce transaction removed all privileges.",
        "category": "rug_pull",
    },
    {
        "id": "sp-009",
        "title": "Fake Liquidity Lock",
        "content": "DEX Listing Lock Manipulation. Severity: HIGH. Category: rug_pull. Token claims liquidity is locked but lock is fake, expired, or for wrong token pair. Indicator: Lock contract locks wrong LP token, unlock date already passed, lock contract is owner-controlled. Detection: Verify lock contract address, check locked token address matches actual LP pair, check unlock date.",
        "category": "rug_pull",
    },
    {
        "id": "sp-010",
        "title": "Infinite Approval Phishing",
        "content": "Infinite Approval Phishing. Severity: CRITICAL. Category: phishing. Attacker tricks user into calling approve() with unlimited spending allowance on malicious contract. Once approved, attacker drains wallet. Code: approve(spender, MAX_UINT256), setApprovalForAll(). Detection: Monitor for approve calls with uint256 max value, warn on unfamiliar token approvals.",
        "category": "phishing",
    },
    {
        "id": "sp-011",
        "title": "Fake Airdrop Site",
        "content": "Fake Airdrop Site. Severity: HIGH. Category: phishing. Attacker creates website impersonating legitimate project offering free tokens. Site prompts wallet connection and signs malicious transaction draining funds. Indicator: Domain similar to real project, requests wallet connection, asks for token approval. Detection: Check URL vs official project URL.",
        "category": "phishing",
    },
    {
        "id": "sp-012",
        "title": "Permit Signature Phishing",
        "content": "Permit Signature Phishing EIP-2612. Severity: CRITICAL. Category: phishing. Attacker tricks user into signing EIP-2612 permit message off-chain, allowing token spend without gas. User sees harmless-looking signature that authorizes transfer. Code: permit(owner, spender, value, deadline, v, r, s). Detection: Decode typed data before signing, check spender address. Real examples: $14M theft via blank check permits.",
        "category": "phishing",
    },
    {
        "id": "sp-013",
        "title": "Address Poisoning",
        "content": "Address Poisoning. Severity: MEDIUM. Category: phishing. Attacker sends dust from wallets with addresses matching first/last chars of victim frequent contacts. Victim copies wrong address from transaction history. Indicator: Small dust transfers from lookalike addresses. Detection: Compare sending address against known contacts, warn on similar prefix/suffix addresses.",
        "category": "phishing",
    },
    {
        "id": "sp-014",
        "title": "Discord Telegram Impersonation",
        "content": "Discord/Telegram Impersonation. Severity: HIGH. Category: phishing. Attacker impersonates project admin in Discord/Telegram, sends fake support link or asks for seed phrase. Indicator: DM from unknown claiming to be admin, request for seed phrase or private key. Detection: Verify admin roles, warn on DM requests for sensitive info.",
        "category": "phishing",
    },
    {
        "id": "sp-015",
        "title": "Token Clone Scam",
        "content": "Token Emulation Clone Scam. Severity: HIGH. Category: phishing. Attacker deploys token with identical name/symbol to legitimate project on same or different chain. Users buy fake version. Indicator: Same name/symbol but different contract address, deployed after original. Detection: Compare contract addresses against verified project addresses, check deployment order.",
        "category": "phishing",
    },
    {
        "id": "sp-016",
        "title": "Drain Approval Attack",
        "content": "Drain Approval Attack. Severity: CRITICAL. Category: phishing. Malicious contract slowly drains approved tokens over time making detection harder. Uses transferFrom in small amounts. Indicator: Small periodic transferFrom calls from wallets that approved the contract. Detection: Monitor for recurring transferFrom patterns.",
        "category": "phishing",
    },
    {
        "id": "sp-017",
        "title": "Seaport Signature Scam",
        "content": "Seaport/LooksRare Signature Scam. Severity: CRITICAL. Category: phishing. Attacker tricks user into signing Seaport order that transfers NFTs at zero cost. User sees approve-like prompt but actually signing sale order. Detection: Decode signature typed data, warn on zero-value NFT transfers.",
        "category": "phishing",
    },
    {
        "id": "sp-018",
        "title": "Token Migration Scam",
        "content": "Token Migration Scam. Severity: HIGH. Category: phishing. Attacker announces fake V1 to V2 token migration, directs users to fraudulent migration contract that steals tokens. Indicator: Migration announcement requiring token approval, no governance vote. Detection: Verify migration through official channels.",
        "category": "phishing",
    },
    {
        "id": "sp-019",
        "title": "Sandwich Attack Front-Run",
        "content": "Sandwich Attack Front-Run. Severity: HIGH. Category: mev. MEV bot detects large pending swap, buys before victim (frontrun), sells after (backrun), profiting from price impact. Indicator: Consecutive transactions in same block from known MEV bots. Detection: Analyze mempool for front-running, check for Jito bundles. Research: Flash Boys 2.0 (Daian et al. 2019).",
        "category": "mev",
    },
    {
        "id": "sp-020",
        "title": "Jito Bundle MEV",
        "content": "Jito Bundle MEV. Severity: HIGH. Category: mev. Solana-specific sandwich attacks using Jito tip-based bundle system. MEV searcher bundles tip transaction with victim swap in same slot. Indicator: Transactions in same Jito bundle, tip payment to Jito validator. Detection: Check for Jito tip instructions.",
        "category": "mev",
    },
    {
        "id": "sp-021",
        "title": "Just-In-Time Liquidity",
        "content": "Just-In-Time Liquidity. Severity: MEDIUM. Category: mev. LP provider adds concentrated liquidity right before large swap and removes immediately after, capturing fees without price risk. Indicator: Add and remove liquidity in same block around large swap. Detection: Monitor liquidity events around large swaps.",
        "category": "mev",
    },
    {
        "id": "sp-022",
        "title": "Bot Sniping at Launch",
        "content": "Bot Sniping at Launch. Severity: MEDIUM. Category: mev. MEV bots detect token launch in mempool and front-run it, buying at initial price before human traders. Indicator: First trades after liquidity add are from known bot addresses. Detection: Analyze first block trades, check for Jito bundles. Real examples: Common on Pump.fun Solana and BSC.",
        "category": "mev",
    },
    {
        "id": "sp-023",
        "title": "Flash Loan Price Manipulation",
        "content": "Flash Loan Price Manipulation. Severity: CRITICAL. Category: flash_loan. Attacker borrows massive amount via flash loan, manipulates price on one DEX, profits on another protocol using manipulated price as oracle. Repays flash loan in same transaction. Indicator: Single tx with borrow+manipulate+exploit+repay pattern. Real examples: bZk $8M, Harvest Finance $24M, Cream Finance $130M. Research: Flashot (Zhang et al. 2020).",
        "category": "flash_loan",
    },
    {
        "id": "sp-024",
        "title": "Flash Loan Governance Attack",
        "content": "Flash Loan Governance Attack. Severity: CRITICAL. Category: flash_loan. Attacker uses flash loan to acquire governance tokens, passes malicious proposal, executes, then repays flash loan. Exploits protocols where governance snapshot is at block timestamp. Indicator: Massive token borrow before vote. Real examples: Build Finance $400K.",
        "category": "flash_loan",
    },
    {
        "id": "sp-025",
        "title": "Flash Loan Sandwich",
        "content": "Flash Loan Sandwich. Severity: HIGH. Category: flash_loan. Combines flash loan borrowing power with sandwich attack. Attacker borrows large amount for front-run. Much larger capital than traditional sandwich. Indicator: Flash loan borrow in same tx as swap operations. Detection: Identify flash loan sources Aave dYdX, analyze borrow-swap-repay patterns.",
        "category": "flash_loan",
    },
    {
        "id": "sp-026",
        "title": "Flash Loan Atomic Arbitrage Exploit",
        "content": "Flash Loan Atomic Arbitrage Exploit. Severity: CRITICAL. Category: flash_loan. Attacker uses flash loan to exploit price differences between protocols in single atomic transaction draining protocol reserves. Indicator: Complex tx trace with borrows from multiple sources, swaps across multiple DEXs. Real examples: PancakeBunny $200M, Fei Rari $80M.",
        "category": "flash_loan",
    },
    {
        "id": "sp-027",
        "title": "Circular Transfer Wash Trading",
        "content": "Circular Transfer Wash Trading. Severity: HIGH. Category: wash_trading. Multiple wallets controlled by same entity repeatedly trade same token between themselves creating fake volume. Indicator: Token transfers in cycle A->B->C->A, same amount round-tripping. Detection: Build transfer graph, detect cycles, check for wallets funded from same source.",
        "category": "wash_trading",
    },
    {
        "id": "sp-028",
        "title": "Cross-DEX Wash Trading",
        "content": "Cross-DEX Wash Trading. Severity: HIGH. Category: wash_trading. Buy on one DEX sell on another at same time creating volume on both without price exposure. Indicator: Simultaneous buy/sell on different DEXs by same wallet. Detection: Cross-reference trades across DEXs.",
        "category": "wash_trading",
    },
    {
        "id": "sp-029",
        "title": "Self-Trade Volume Inflation",
        "content": "Self-Trade Volume Inflation. Severity: MEDIUM. Category: wash_trading. Contract owner trades against themselves using bots to inflate reported volume attracting genuine buyers. Indicator: Fresh wallets with no other trading history, concentrated trading in short windows. Detection: Analyze wallet age, source of funds.",
        "category": "wash_trading",
    },
    {
        "id": "sp-030",
        "title": "NFT Wash Trading",
        "content": "NFT Wash Trading. Severity: MEDIUM. Category: wash_trading. Same NFT bought and sold between related wallets to inflate collection floor price and volume. Indicator: NFT transfers cycle between few wallets, price escalation with no organic demand. Detection: Build NFT transaction graph. Real examples: Blur farming wash trades.",
        "category": "wash_trading",
    },
    {
        "id": "sp-031",
        "title": "Coordinated Pump Scheme",
        "content": "Coordinated Pump Scheme. Severity: CRITICAL. Category: pump_dump. Organized group buys token simultaneously, promotes heavily on social media, then dumps on new buyers. Lifecycle: deploy->accumulation->pump promotion->distribution->dump. Indicator: Volume spike >5x average, coordinated buy clusters from fresh wallets. Real examples: BitConnect, Telegram pump groups.",
        "category": "pump_dump",
    },
    {
        "id": "sp-032",
        "title": "Celebrity Endorsement Dump",
        "content": "Celebrity Influencer Endorsement Dump. Severity: HIGH. Category: pump_dump. Token pays celebrity to promote, team dumps during promotion period. Indicator: Large transfers from team wallet during promotion. Detection: Monitor team wallet during promotion windows.",
        "category": "pump_dump",
    },
    {
        "id": "sp-033",
        "title": "Volume Spike Artificial Inflation",
        "content": "Volume Spike Artificial Inflation. Severity: HIGH. Category: pump_dump. Trading bots create 10-100x normal volume to get listed on aggregators and attract buyers. Volume collapses after listing. Indicator: Volume spike with no price movement proportionate to volume. Detection: Compute volume/price change ratio.",
        "category": "pump_dump",
    },
    {
        "id": "sp-034",
        "title": "Whale Dominance Governance",
        "content": "Whale Dominance Governance. Severity: HIGH. Category: governance_attack. Single whale holds majority of governance tokens, can pass any proposal unilaterally. Indicator: Top holder >50% of supply, quorum easily met by single voter. Detection: Compute HHI of governance token distribution.",
        "category": "governance_attack",
    },
    {
        "id": "sp-035",
        "title": "Missing Timelock Exploit",
        "content": "Missing Timelock Exploit. Severity: HIGH. Category: governance_attack. Protocol has no timelock on governance execution allowing instant execution of malicious proposals. Indicator: No Timelock contract, execute() callable immediately after proposal pass. Detection: Check for Timelock in protocol architecture.",
        "category": "governance_attack",
    },
    {
        "id": "sp-036",
        "title": "Low Quorum Flash Vote",
        "content": "Low Quorum Flash Vote. Severity: HIGH. Category: governance_attack. Governance quorum threshold very low, allowing small holders to pass proposals without broad consent. Combined with flash loan, attacker can borrow tokens, vote, repay. Indicator: Quorum <5% of supply. Detection: Check governance parameters.",
        "category": "governance_attack",
    },
    {
        "id": "sp-037",
        "title": "Compromised Multi-sig",
        "content": "Compromised Multi-sig. Severity: CRITICAL. Category: governance_attack. Attacker gains control of enough multi-sig keys to execute transactions unilaterally via social engineering or exploit. Indicator: Multi-sig threshold lowered, unusual transactions from multi-sig. Detection: Monitor multi-sig composition changes.",
        "category": "governance_attack",
    },
    {
        "id": "sp-038",
        "title": "Single Oracle Dependency",
        "content": "Single Oracle Dependency. Severity: HIGH. Category: oracle_manipulation. Protocol relies on single price oracle. If that pool is manipulated all protocol functions are compromised. Code: latestRoundData() from single Chainlink feed. Detection: Count oracle sources, flag if only one.",
        "category": "oracle_manipulation",
    },
    {
        "id": "sp-039",
        "title": "Low-Liquidity Oracle Pool",
        "content": "Low-Liquidity Oracle Pool. Severity: CRITICAL. Category: oracle_manipulation. Oracle reads price from DEX pool with low liquidity. Attacker easily manipulates price with small capital. Indicator: Oracle pool has < $1M liquidity. Real examples: Mango Markets $114M, Bonq DAO $120M.",
        "category": "oracle_manipulation",
    },
    {
        "id": "sp-040",
        "title": "Stale Oracle Data",
        "content": "Stale Oracle Data. Severity: MEDIUM. Category: oracle_manipulation. Oracle price feed not updated recently showing stale prices. Exploitable if actual price diverges significantly. Indicator: latestRoundData() returns old timestamp. Detection: Check oracle updatedAt timestamp.",
        "category": "oracle_manipulation",
    },
    {
        "id": "sp-041",
        "title": "TWAP Manipulation",
        "content": "Price Feed Manipulation via TWAP. Severity: MEDIUM. Category: oracle_manipulation. Protocol uses Uniswap TWAP with too short observation period. Attacker manipulates pool price, waits for TWAP to reflect it. Indicator: TWAP period < 30 minutes. Detection: Check TWAP parameters.",
        "category": "oracle_manipulation",
    },
    {
        "id": "sp-042",
        "title": "UUPS Upgrade Rug Pull",
        "content": "UUPS Upgrade Rug Pull. Severity: CRITICAL. Category: proxy_attack. UUPS proxy where upgrade function is in implementation. Owner upgrades to malicious implementation that drains funds. Indicator: upgradeTo() without timelock. Detection: Check proxy type, verify upgrade authorization. Function selectors: 0x4f1ef286 (upgradeToAndCall).",
        "category": "proxy_attack",
    },
    {
        "id": "sp-043",
        "title": "Transparent Proxy Admin Control",
        "content": "Transparent Proxy Admin Control. Severity: HIGH. Category: proxy_attack. Proxy admin can change implementation instantly. If admin is EOA single key can rug by deploying malicious impl. Indicator: Proxy admin is EOA, no timelock. Detection: Check proxy admin address type, verify if admin is multisig or timelock.",
        "category": "proxy_attack",
    },
    {
        "id": "sp-044",
        "title": "Implementation Swap Attack",
        "content": "Implementation Swap Attack. Severity: CRITICAL. Category: proxy_attack. Attacker gains control of proxy admin and swaps implementation to contract that drains all user funds. Indicator: Implementation address changed with no governance vote. Detection: Monitor implementation address changes.",
        "category": "proxy_attack",
    },
    {
        "id": "sp-045",
        "title": "Telegram Shill Campaign",
        "content": "Telegram Shill Campaign. Severity: HIGH. Category: social_engineering. Paid shillers promote token in multiple Telegram groups simultaneously creating FOMO. Indicator: Multiple groups posting about same token within minutes, similar messaging. Detection: Monitor cross-group message timing.",
        "category": "social_engineering",
    },
    {
        "id": "sp-046",
        "title": "Fake Project Website",
        "content": "Fake Project Website. Severity: HIGH. Category: social_engineering. Clone website of legitimate DeFi protocol with modified contract addresses. Indicator: HTML structure similar to real site but different contract addresses. Detection: Compare HTML structure hash, verify contract addresses.",
        "category": "social_engineering",
    },
    {
        "id": "sp-047",
        "title": "Reentrancy Vault Drain",
        "content": "Reentrancy Vault Drain. Severity: CRITICAL. Category: exploit. Contract makes external call before updating state, allowing recursive withdraw calls draining entire balance. Code: withdraw() uses msg.sender.call before balance deduction. Detection: Check for external calls before state changes, use Slither reentrancy detector. Real examples: The DAO $60M 2016.",
        "category": "exploit",
    },
    {
        "id": "sp-048",
        "title": "Access Control Bypass",
        "content": "Access Control Bypass. Severity: HIGH. Category: exploit. Critical functions lack proper access control allowing any caller to execute owner-only operations like minting or withdrawing. Indicator: No onlyOwner modifier on mint/withdraw/pause. Detection: Check function visibility and modifiers. Function selectors: 0xa0712d68 (mint without access control).",
        "category": "exploit",
    },
    {
        "id": "sp-049",
        "title": "ERC4626 Vault Inflation",
        "content": "ERC4626 Vault Inflation Attack. Severity: HIGH. Category: exploit. Attacker deposits 1 wei, donates tokens to inflate share price, then redeems for more than deposited. New depositors lose funds. Indicator: Vault with no initial deposit. Detection: Check for initial deposit, verify vault has minimum deposit threshold.",
        "category": "exploit",
    },
    {
        "id": "sp-050",
        "title": "ERC777 Callback Reentrancy",
        "content": "Callback Reentrancy in ERC777/ERC1155. Severity: HIGH. Category: exploit. ERC777 tokensToSend and ERC1155 onERC1155Received callbacks allow reentrancy during token transfers. Indicator: Contract accepts ERC777 tokens and makes state changes after transfer. Detection: Check for ERC777 compatibility. Real examples: imBTC Uniswap $340K, Lendf.me $25M.",
        "category": "exploit",
    },
]

# ── CONTRACT AUDITS (25 known audit patterns) ────────────────────────
contract_audits = [
    {
        "id": "ca-001",
        "title": "OpenZeppelin ERC20 Standard Audit",
        "content": "OpenZeppelin ERC20 Standard Implementation Audit. Category: safe_implementation. The standard OpenZeppelin ERC20 implementation is widely used and audited. Known safe selectors: totalSupply(), balanceOf(), transfer(), allowance(), approve(), transferFrom(). No mint function exposed, standard access control. Audit status: Multiple audits by OpenZeppelin, Consensys Diligence. Risk: LOW. This is a known safe implementation baseline.",
        "category": "audit_report",
    },
    {
        "id": "ca-002",
        "title": "Unchecked External Call Pattern",
        "content": "Unchecked External Call Vulnerability. Category: code_vulnerability. Contract makes external call but does not check return value. If call silently fails, state may be inconsistent. Code: token.transfer(to, amount) without checking return value. Detection: Slither check-unchecked-returns detector. Severity: MEDIUM. Fix: Use SafeERC20 library or check return value.",
        "category": "audit_report",
    },
    {
        "id": "ca-003",
        "title": "Integer Overflow/Underflow",
        "content": "Integer Overflow/Underflow Vulnerability. Category: code_vulnerability. Arithmetic operations can overflow/underflow in Solidity <0.8.0 without SafeMath. Code: balance += amount without overflow check. Detection: Check Solidity version, use Slither arithmetic detector. Severity: HIGH for pre-0.8.0. Fix: Use Solidity 0.8.0+ or SafeMath. Real examples: BEC token $9M overflow.",
        "category": "audit_report",
    },
    {
        "id": "ca-004",
        "title": "Front-Running Vulnerability",
        "content": "Front-Running Vulnerability in DEX. Category: code_vulnerability. Transaction ordering is visible in mempool, allowing MEV extraction. Code: Uniswap swapExactTokensForTokens without slippage protection. Detection: Check for deadline parameter, minimum amount parameters. Severity: MEDIUM. Fix: Set appropriate slippage tolerance and deadline.",
        "category": "audit_report",
    },
    {
        "id": "ca-005",
        "title": "Unsafe Delegate Call",
        "content": "Unsafe Delegate Call. Category: code_vulnerability. Contract uses delegatecall to untrusted contract, potentially allowing storage corruption or arbitrary code execution. Code: delegatecall to user-controlled address. Detection: Slither controlled-delegatecall detector. Severity: CRITICAL. Fix: Only delegatecall to trusted/immutable addresses.",
        "category": "audit_report",
    },
    {
        "id": "ca-006",
        "title": "Timelock Audit Pattern",
        "content": "Timelock Contract Audit. Category: security_pattern. Timelock contracts delay execution of governance decisions, giving community time to react. Safe pattern: 48h minimum delay, queued execution, grace period for cancellation. Known safe implementations: OpenZeppelin TimelockController. Detection: Verify delay >= 48h, check executor roles, verify cancellation mechanism.",
        "category": "audit_report",
    },
    {
        "id": "ca-007",
        "title": "Multisig Wallet Audit",
        "content": "Multisig Wallet Security Audit. Category: security_pattern. Gnosis Safe multisig is standard for project treasuries. Safe patterns: M-of-N with N>=3, timelock on high-value operations, regular signer rotation. Detection: Check signer count, threshold ratio, verify no single signer can execute alone.",
        "category": "audit_report",
    },
    {
        "id": "ca-008",
        "title": "Flash Loan Guard Pattern",
        "content": "Flash Loan Guard Security Pattern. Category: security_pattern. Protocols can block flash loan attacks by checking if block.number changed between deposit and action. Code: require(block.number > lastDepositBlock[msg.sender]). Detection: Check for block.number based flash loan guards. Also: EIP-4626 flash loan resistant vaults.",
        "category": "audit_report",
    },
    {
        "id": "ca-009",
        "title": "Reentrancy Guard Implementation",
        "content": "Reentrancy Guard Implementation Audit. Category: security_pattern. OpenZeppelin ReentrancyGuard uses status variable to prevent recursive calls. Code: modifier nonReentrant { require(_status != _ENTERED); _status = _ENTERED; _; _status = _NOT_ENTERED; }. Detection: Verify nonReentrant on all state-changing external functions.",
        "category": "audit_report",
    },
    {
        "id": "ca-010",
        "title": "Proxy Pattern Security Audit",
        "content": "Proxy Pattern Security Audit. Category: security_pattern. Common proxy patterns: Transparent Proxy (admin vs user functions), UUPS (upgradeable implementation), Beacon Proxy (shared implementation). Safe patterns: Proxy admin is timelocked multisig, upgrade requires governance vote. Detection: Check proxy admin type, verify timelock on upgrades.",
        "category": "audit_report",
    },
    {
        "id": "ca-011",
        "title": "Oracle Security Assessment",
        "content": "Oracle Security Assessment. Category: security_pattern. Safe oracle patterns: Chainlink with fallback, TWAP with minimum period 30min, multiple oracle sources with median. Unsafe: Single Uniswap pool TWAP < 10min, no stale data check. Code: requireupdatedAt >= block.timestamp - HEARTBEAT). Detection: Count oracle sources, verify fallback, check heartbeat intervals.",
        "category": "audit_report",
    },
    {
        "id": "ca-012",
        "title": "ERC20 Token Security Checklist",
        "content": "ERC20 Token Security Checklist. Category: audit_checklist. 1) Owner can mint? If yes, is supply capped? 2) Owner can pause transfers? 3) Sell tax modifiable? Capped? 4) Blacklist functions? 5) Self-destruct present? 6) Proxy contract? If yes, can upgrade be blocked? 7) Ownership renounced or timelocked? 8) LP tokens locked? 9) Trading enabled toggle? 10) Hidden functions in bytecode?",
        "category": "audit_report",
    },
    {
        "id": "ca-013",
        "title": "Slither Static Analysis Report",
        "content": "Slither Static Analysis Capabilities. Category: tool_reference. Slither detects: reentrancy, uninitialized variables, unchecked external calls, shadowed variables, dangerous state variable names, delegatecall to untrusted, unchecked return values, ERC20 interface violations, access control issues. Command: slither contract.sol --check-all. Detectors: 80+ built-in detectors. Can be integrated in CI/CD.",
        "category": "audit_report",
    },
    {
        "id": "ca-014",
        "title": "Forta Bot Detection",
        "content": "Forta Bot Detection Network. Category: tool_reference. Forta bots monitor blockchain for: exploit detection (flash loan attacks, reentrancy), anomaly detection (unusual governance actions, large withdrawals), compliance monitoring (sanctioned addresses). Public API: https://api.forta.network/alerts?ids=BOT-ID. Detection: Query Forta alerts for token address.",
        "category": "audit_report",
    },
    {
        "id": "ca-015",
        "title": "Heimdall Decompiler Capabilities",
        "content": "Heimdall-rs Decompiler. Category: tool_reference. Heimdall decompiles EVM bytecode to readable Solidity-like code. Features: ABI recovery, function signature matching, control flow graph. Command: heimdall decompile --target 0xADDRESS --rpc-url URL. Useful for: unverified contracts, analyzing proxy implementations, detecting hidden functions.",
        "category": "audit_report",
    },
    {
        "id": "ca-016",
        "title": "4-byte Function Selector Database",
        "content": "4-byte Function Selector Database. Category: tool_reference. Ethereum function selectors (first 4 bytes of keccak256 hash) identify contract functions. Database: https://www.4byte.directory. Known rug selectors: 0xa0712d68 (mint), 0x3ccfd60b (withdraw), 0x4b2e2f5b (setTaxRate), 0x8456cb59 (pause), 0xe9a4db3e (setBlacklistAddress), 0x4f1ef286 (upgradeToAndCall), 0xf3fef3a3 (drain), 0x5a3f0b2c (rescueTokens).",
        "category": "audit_report",
    },
    {
        "id": "ca-017",
        "title": "EIP-1967 Proxy Storage Slots",
        "content": "EIP-1967 Proxy Standard Storage Slots. Category: technical_reference. Standard slots prevent storage collisions: Implementation: 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc. Admin: 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103. Beacon: 0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50. Detection: Read storage slots via eth_getStorageAt to resolve proxy.",
        "category": "audit_report",
    },
    {
        "id": "ca-018",
        "title": "Uniswap V2 Pair Flash Loan Vulnerability",
        "content": "Uniswap V2 Pair Flash Loan Vulnerability. Category: code_vulnerability. Uniswap V2 allows flash swaps (borrow then repay in same tx). Can be exploited for price manipulation if protocol uses spot price as oracle. Code: swap(0, amount, to, data) triggers uniswapV2Call callback. Detection: Check if protocol uses Uniswap V2 pair as price oracle without TWAP. Fix: Use TWAP with sufficient period.",
        "category": "audit_report",
    },
    {
        "id": "ca-019",
        "title": "Compound Governor Alpha Vulnerabilities",
        "content": "Compound Governor Alpha Known Vulnerabilities. Category: audit_report. Governor Alpha issues: 1) No timelock on proposal execution, 2) Quorum is total supply not circulating, 3) No vote delegation revocation. These enabled flash loan governance attacks. Detection: Check if using Governor Alpha vs Governor Bravo which has execution delay.",
        "category": "audit_report",
    },
    {
        "id": "ca-020",
        "title": "SafeMoon Clone Vulnerabilities",
        "content": "SafeMoon Clone Vulnerabilities. Category: audit_report. Common SafeMoon clone issues: 1) Owner can modify tax rates arbitrarily, 2) Unchecked LP lock claims, 3) SwapAndLiquify can be front-run by owner, 4) Blacklist function often present, 5) Trading toggle allows owner to freeze all trades. Detection: Compare bytecode against SafeMoon variants, check for modifiable fee structure.",
        "category": "audit_report",
    },
    {
        "id": "ca-021",
        "title": "ERC721 Reentrancy via onERC721Received",
        "content": "ERC721 Reentrancy via onERC721Received. Category: code_vulnerability. Contracts that accept ERC721 tokens via safeTransferFrom receive onERC721Received callback. If contract modifies state after this callback, reentrancy possible. Detection: Check state changes after onERC721Received. Fix: Apply reentrancy guard.",
        "category": "audit_report",
    },
    {
        "id": "ca-022",
        "title": "Uniswap V3 Concentrated Liquidity Risk",
        "content": "Uniswap V3 Concentrated Liquidity Risk Assessment. Category: audit_report. V3 concentrated positions can be made nearly zero width, reducing effective liquidity. Attack: LP provides narrow range, price moves out of range, position becomes inactive. Protocol using V3 TWAP may get stale price. Detection: Check for active liquidity in range, verify TWAP uses adequate observation period.",
        "category": "audit_report",
    },
    {
        "id": "ca-023",
        "title": "Aave Flash Loan Security",
        "content": "Aave Flash Loan Security Assessment. Category: audit_report. Aave V2/V3 flash loans: borrower must repay principal + fee in same transaction. Fee: 0.05% V2, 0.05-0.09% V3. Attack vector: borrow -> manipulate price on DEX -> exploit protocol using DEX as oracle -> repay. Detection: Monitor flash loan borrows > $1M, trace subsequent operations.",
        "category": "audit_report",
    },
    {
        "id": "ca-024",
        "title": "Chainlink Oracle Best Practices",
        "content": "Chainlink Oracle Best Practices. Category: audit_report. Safe Chainlink usage: 1) Check latestRoundData return values including updatedAt, 2) Verify price > 0 (stale/invalid), 3) Set heartbeat timeout (e.g. 3600s for 1h feeds), 4) Use fallback oracle, 5) Use multiple feeds for cross-validation. Code: require(answer > 0 && updatedAt > block.timestamp - 3600).",
        "category": "audit_report",
    },
    {
        "id": "ca-025",
        "title": "Solidity Compiler Vulnerabilities History",
        "content": "Solidity Compiler Known Vulnerabilities. Category: audit_report. Historical bugs: 0.4.22 storage layout bug, 0.5.x ABIEncoderV2 issues, 0.6.x immutable variable issues. Current safe versions: 0.8.20+. Detection: Check pragma solidity version, apply known patches. Always use latest stable compiler.",
        "category": "audit_report",
    },
]

# ── TRANSACTION PATTERNS (25 patterns) ────────────────────────────────
tx_patterns = [
    {
        "id": "tp-001",
        "title": "Flash Loan Borrow-Exploit-Repay",
        "content": "Flash Loan Transaction Pattern: borrow-exploit-repay. Description: Single atomic transaction containing Aave/dYdX flash loan borrow, followed by swap operations that manipulate price, followed by protocol exploit, ending with flash loan repayment. Trace depth typically >10 internal calls. Known indicators: Transaction originates from contract not EOA, contains call to Aave LendingPool or dYdX SoloMargin, concludes with repay of exact borrow amount plus fee. Detection: Analyze trace for borrow-repay bookends.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-002",
        "title": "Sandwich Attack Transaction",
        "content": "Sandwich Attack Transaction Pattern. Description: Two or three transactions in same block: frontrun buy, victim swap, backrun sell. Indicators: Same searcher bot address does buy then sell around victim transaction, buy and sell in same block, victim pays higher slippage than expected. Gas prices: frontrun uses high gas to get ahead. Detection: Compare transaction gas prices within same block, identify searcher addresses.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-003",
        "title": "Rug Pull Liquidity Removal",
        "content": "Rug Pull Liquidity Removal Transaction Pattern. Description: Deployer wallet removes large portion or all LP tokens from DEX liquidity pool. Typically within hours or days of project launch. Indicators: Large removeLiquidity call from deployer address, entire LP balance removed, no remaining liquidity. Detection: Monitor deployer wallet LP balance, alert on large withdrawal > 50% of pool.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-004",
        "title": "Mint and Dump Transaction",
        "content": "Mint and Dump Transaction Pattern. Description: Contract owner mints large amount of tokens then immediately sells on DEX. Indicators: Mint transaction followed by swap to ETH/stable within same or next block, minted amount significantly larger than normal transfers. Detection: Monitor owner mint calls, track subsequent sell transactions.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-005",
        "title": "Wash Trading Circular Transfer",
        "content": "Wash Trading Circular Transfer Pattern. Description: Tokens flow in cycle: Wallet A sends to B, B sends to C, C sends back to A. No net change in holdings, but generates false volume. Indicators: Transfer amounts similar, cycle completes within few blocks, wallets have no other significant activity. Detection: Build transfer graph, detect cycles, compute cycle flow metrics.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-006",
        "title": "CEX Funded Sniper Bot",
        "content": "CEX Funded Sniper Bot Transaction Pattern. Description: Wallet receives ETH from Binance/OKX hot wallet, immediately buys token at launch, then sells within minutes. Indicators: First incoming tx is from known CEX hot wallet, first outgoing tx is swap on new token, sell within 1-5 blocks of buy. Detection: Check wallet funding source against CEX addresses database.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-007",
        "title": "Governance Proposal Execution",
        "content": "Governance Proposal Execution Pattern. Description: Malicious governance proposal queued and executed. Indicators: Proposal contains call to transfer funds, change implementation, or modify critical parameters. Execution happens after voting period with minimal opposition. Detection: Decode proposal calldata, check target contracts, verify execution impact.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-008",
        "title": "Proxy Implementation Upgrade",
        "content": "Proxy Implementation Upgrade Transaction Pattern. Description: Transaction changes proxy implementation address. Can be legitimate upgrade or rug pull depending on new implementation. Indicators: Storage slot 0x360894a... changes value, upgradeTo() or changeImplementation() called. Detection: Monitor implementation storage slot changes, verify new implementation bytecode.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-009",
        "title": "Phishing Approval Transaction",
        "content": "Phishing Approval Transaction Pattern. Description: Victim calls approve() or permit() giving malicious contract spending allowance. Followed by transferFrom() draining tokens. Indicators: approve(spender, MAX_UINT256) or permit signature, spender is unknown contract. Detection: Monitor for max-value approvals to unknown contracts, warn users.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-010",
        "title": "Pump and Dump Volume Pattern",
        "content": "Pump and Dump Volume Transaction Pattern. Description: Volume spikes 5-100x average, followed by even larger sell volume. Buy volume concentrated in fresh wallets, sell volume from team wallets. Indicators: Buy volume from wallets < 24h old, sell volume from wallets > 7d old, volume/price divergence. Detection: Compute rolling volume baseline, flag >5x spikes, analyze wallet age distribution.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-011",
        "title": "Drain Transaction After Approval",
        "content": "Post-Approval Drain Transaction Pattern. Description: After user approves contract, attacker calls transferFrom in one or multiple transactions to drain tokens. Indicators: transferFrom called by non-owner, source is victim wallet, destination is attacker. Often happens hours or days after approval. Detection: Monitor for transferFrom calls where spender was recently approved.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-012",
        "title": "Dusting Attack Transaction",
        "content": "Dusting Attack Transaction Pattern. Description: Attacker sends tiny amounts of tokens to many addresses (dusting). Purpose: track wallet activity, prep for address poisoning. Indicators: Hundreds of tiny transfers from single source to random addresses, amounts < $1. Detection: Monitor for mass small-value transfers, flag dusting patterns.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-013",
        "title": "Cross-Chain Bridge Exploit",
        "content": "Cross-Chain Bridge Exploit Transaction Pattern. Description: Attacker exploits bridge contract vulnerability to mint or withdraw tokens on destination chain without proper deposit on source chain. Indicators: Large withdrawal without matching deposit, or minted tokens on destination chain. Real examples: Ronin Bridge $624M, Wormhole $326M, Nomad $190M. Detection: Verify deposit-lock-mint/redeem-burn-unlock cycle integrity.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-014",
        "title": "Uniswap V2 Add Liquidity + Remove Same Block",
        "content": "Uniswap V2 Add-Remove Same Block Pattern. Description: Entity adds liquidity and removes in same or consecutive block. Legitimate during pool migration, suspicious if repeated (fake volume). Indicators: mint() and burn() in same block from same address. Detection: Monitor for consecutive add/remove operations.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-015",
        "title": "High-Frequency Token Rotation",
        "content": "High-Frequency Token Rotation Pattern. Description: Wallet rapidly buys and sells multiple tokens within minutes, characteristic of MEV bot or sniper. Not necessarily malicious but indicates automated trading. Indicators: >10 swaps per hour, multiple different tokens, short holding periods. Detection: Compute trade frequency, identify automated behavior.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-016",
        "title": "Team Wallet Gradual Dump",
        "content": "Team Wallet Gradual Dump Pattern. Description: Team wallet sells tokens in small amounts over days/weeks to avoid detection. Total sells add up to significant % of supply. Indicators: Daily sells from team wallets, sells correlated with price increases (selling into pumps). Detection: Track team wallet activity, compute cumulative sell volume.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-017",
        "title": "Flash Loan Arbitrage Cascade",
        "content": "Flash Loan Arbitrage Cascade Pattern. Description: Single transaction exploits multiple protocols in sequence using flash loan capital. Borrow from Aave -> manipulate Protocol A -> use A output to attack Protocol B -> repay. Complex trace with depth >15. Detection: Analyze transaction trace depth, identify protocol interaction sequence.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-018",
        "title": "Token Launch Sniper Pattern",
        "content": "Token Launch Sniper Pattern. Description: Multiple bots buy token in same block as liquidity addition. First buyers get best price. Indicators: >5 buys in first block, most buyers are fresh wallets or known bot addresses, first block gas prices significantly higher than average. Detection: Analyze first block composition, identify bot addresses.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-019",
        "title": "Liquidity Migration Rug Pattern",
        "content": "Liquidity Migration Rug Pattern. Description: Project announces migration from DEX A to DEX B. Removes liquidity from A but never adds to B, taking funds. Indicators: removeLiquidity call followed by no corresponding addLiquidity, funds sent to team wallet instead of new pool. Detection: Monitor liquidity events, verify migrated LP appears on new DEX.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-020",
        "title": "Staking Reward Inflation",
        "content": "Staking Reward Inflation Pattern. Description: Protocol offers unrealistic staking APY (>1000%) funded by minting new tokens. Token price drops as supply increases, creating death spiral. Indicators: APY > 500%, token supply increasing rapidly, price declining trend. Detection: Check APY vs token inflation rate, compute real yield.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-021",
        "title": "Private Key Compromise Pattern",
        "content": "Private Key Compromise Transaction Pattern. Description: Large unexpected transfers from wallet that has been inactive, draining all assets rapidly. Indicators: Wallet inactive for weeks, then sudden large transfers to multiple unknown addresses, all assets liquidated, no interaction with usual DeFi protocols. Detection: Compare against known wallet behavior patterns.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-022",
        "title": "Manipulated Oracle Price Feed",
        "content": "Manipulated Oracle Price Feed Pattern. Description: Price on oracle feed diverges significantly from market-wide price. Often preceded by large swap that displaces pool price. Indicators: Oracle price > 5% deviation from CoinGecko/other sources, single large swap moved pool price. Detection: Compare oracle price against multiple market sources, flag >2% deviations.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-023",
        "title": "Batch Transfer Phish",
        "content": "Batch Transfer Phishing Pattern. Description: Attacker contract uses batchTransfer or multiSend to send tokens from multiple approved victims in single transaction. Efficient drain operation. Indicators: Single tx with multiple transferFrom calls to different source addresses. Detection: Analyze internal transactions for batch drain patterns.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-024",
        "title": "NFT Floor Price Manipulation",
        "content": "NFT Floor Price Manipulation Pattern. Description: Collection floor price pushed up via wash trades between related wallets. Borrowers use inflated floor as collateral for loans. Indicators: Related wallets trading same NFT at escalating prices, high frequency bids. Detection: Cross-reference wallet funding sources, compute actual demand metrics.",
        "category": "transaction_pattern",
    },
    {
        "id": "tp-025",
        "title": "Emergency Withdrawal Exploit",
        "content": "Emergency Withdrawal Exploit Pattern. Description: Protocol has emergencyWithdraw function meant for crisis, but exploited when access control missing or compromised. Often drains entire contract balance. Indicators: Large withdrawal via emergency function, called by non-timelock address. Detection: Monitor for emergency withdrawal calls, verify caller authorization.",
        "category": "transaction_pattern",
    },
]

print("Ingesting scam_patterns...")
ingest("scam_patterns", scam_patterns)

print("Ingesting contract_audits...")
ingest("contract_audits", contract_audits)

print("Ingesting transaction_patterns...")
ingest("transaction_patterns", tx_patterns)

print(f"\nTotal: {len(scam_patterns) + len(contract_audits) + len(tx_patterns)} documents ingested")
