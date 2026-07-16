#!/usr/bin/env python3
"""Populate empty RAG collections - single doc at a time."""

import json
import urllib.request

API = "http://localhost:8000/api/v1/rag/ingest"


def ingest(collection, content, metadata=None):
    payload = json.dumps(
        {"collection": collection, "content": content, "metadata": metadata or {}}
    ).encode()
    req = urllib.request.Request(API, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception:
        return None


# Batch of scam patterns
sp = [
    (
        "Liquidity Removal Rug Pull. CRITICAL rug_pull. Developer removes all liquidity from DEX pool after attracting buyers. Indicator: LP tokens held in deployer wallet no timelock. Code: removeLiquidity() withdrawLiquidity(). Detection: Monitor LP balance of deployer. Real: Meerkat Finance $31M Merlin DEX $4.8M.",
        "rug_pull",
    ),
    (
        "Mint-and-Dump. CRITICAL rug_pull. Hidden unlimited mint function allows owner to mint new tokens and sell diluting value. Code: mint() _mint(to amount). Function selectors: 0xa0712d68 0x40c10f19. Detection: Check for mint without supply cap.",
        "rug_pull",
    ),
    (
        "Honeypot Token. CRITICAL rug_pull. Token can be bought but not sold due to transfer restrictions or 100% sell tax. Code: _beforeTokenTransfer override _taxRate > 90%. Detection: Simulate sell. Real: Squid Game Token.",
        "rug_pull",
    ),
    (
        "Hidden Self-Destruct. CRITICAL rug_pull. selfdestruct() function allows owner to kill contract and steal ETH. Detection: Scan bytecode for opcode 0xff. Real: Parity $150M.",
        "rug_pull",
    ),
    (
        "Backdoor Transfer Pause. HIGH rug_pull. Owner can pause all transfers preventing sells. Code: pause() setTradingEnabled(false). Selectors: 0x8456cb59 0x3f4ba83a.",
        "rug_pull",
    ),
    (
        "Tax Modification Rug. CRITICAL rug_pull. Owner can change sell tax to 100%. Code: setTaxRate() setSellFee(). Selectors: 0x4b2e2f5b 0xdb006a75.",
        "rug_pull",
    ),
    (
        "Blacklist Rug Pull. CRITICAL rug_pull. Owner blacklists holders from selling. Code: blacklist() setBlacklist() isBlacklisted. Selector: 0xe9a4db3e.",
        "rug_pull",
    ),
    (
        "Hidden Owner Privilege. HIGH rug_pull. Appears renounced but hidden mechanism allows regaining control. Indicator: pendingOwner ROLE_ADMIN two-step transfer.",
        "rug_pull",
    ),
    (
        "Fake Liquidity Lock. HIGH rug_pull. Claims locked but lock is fake expired or wrong token. Detection: Verify lock contract address and unlock date.",
        "rug_pull",
    ),
    (
        "Infinite Approval Phishing. CRITICAL phishing. Tricks user into approve(spender MAX_UINT256). Detection: Monitor for max-value approvals to unknown contracts.",
        "phishing",
    ),
    (
        "Fake Airdrop Site. HIGH phishing. Website impersonating project offering free tokens. Prompts wallet connection with malicious transaction. Detection: Check URL vs official.",
        "phishing",
    ),
    (
        "EIP-2612 Permit Phishing. CRITICAL phishing. User signs off-chain permit allowing token spend without gas. Code: permit(owner spender value deadline v r s). Real: $14M via blank check permits.",
        "phishing",
    ),
    (
        "Address Poisoning. MEDIUM phishing. Dust from lookalike addresses makes victim copy wrong address. Detection: Warn on similar prefix/suffix addresses.",
        "phishing",
    ),
    (
        "Token Clone Scam. HIGH phishing. Same name/symbol different contract address. Detection: Compare against verified addresses check deployment order.",
        "phishing",
    ),
    (
        "Seaport NFT Signature Scam. CRITICAL phishing. User signs Seaport order transferring NFTs at zero cost. Detection: Decode typed data warn on zero-value transfers.",
        "phishing",
    ),
    (
        "Sandwich Attack. HIGH mev. Bot frontruns victim swap then backruns. Research: Flash Boys 2.0 Daian 2019. Detection: Analyze mempool check same-block buy-sell patterns.",
        "mev",
    ),
    (
        "Jito Bundle MEV. HIGH mev. Solana sandwich via Jito tip bundles. Detection: Check for Jito tip instructions.",
        "mev",
    ),
    (
        "Bot Sniping Launch. MEDIUM mev. Bots buy at launch before humans. Detection: First-block trades from known bots. Common on Pump.fun.",
        "mev",
    ),
    (
        "Just-In-Time Liquidity. MEDIUM mev. LP adds concentrated liquidity before large swap removes after. Detection: Same-block add+remove around swaps.",
        "mev",
    ),
    (
        "Flash Loan Price Manipulation. CRITICAL flash_loan. Borrow manipulate DEX price exploit protocol repay in same tx. Real: bZk $8M Harvest $24M Cream $130M. Research: Flashot Zhang 2020.",
        "flash_loan",
    ),
    (
        "Flash Loan Governance Attack. CRITICAL flash_loan. Borrow governance tokens pass proposal execute repay. Real: Build Finance $400K.",
        "flash_loan",
    ),
    (
        "Flash Loan Sandwich. HIGH flash_loan. Flash loan capital for sandwich attack. Detection: Aave/dYdX borrow-swap-repay in same tx.",
        "flash_loan",
    ),
    (
        "Flash Loan Atomic Arbitrage. CRITICAL flash_loan. Exploit price differences between protocols atomically. Real: PancakeBunny $200M Fei Rari $80M.",
        "flash_loan",
    ),
    (
        "Circular Wash Trading. HIGH wash_trading. Same entity trades between own wallets for fake volume. Detection: Transfer graph cycle detection.",
        "wash_trading",
    ),
    (
        "Cross-DEX Wash Trading. HIGH wash_trading. Buy DEX A sell DEX B same time. Detection: Cross-reference trades across DEXs.",
        "wash_trading",
    ),
    (
        "NFT Wash Trading. MEDIUM wash_trading. Same NFT round-trip to inflate floor. Detection: Transaction graph analysis. Real: Blur farming.",
        "wash_trading",
    ),
    (
        "Coordinated Pump Scheme. CRITICAL pump_dump. Organized buy promote dump. Volume >5x average fresh wallet clusters. Real: BitConnect.",
        "pump_dump",
    ),
    (
        "Celebrity Endorsement Dump. HIGH pump_dump. Paid celebrity promotes while team dumps. Detection: Monitor team wallet during promotion.",
        "pump_dump",
    ),
    (
        "Whale Governance Dominance. HIGH governance_attack. Single holder >50% supply controls all proposals. Detection: HHI analysis.",
        "governance_attack",
    ),
    (
        "Missing Timelock. HIGH governance_attack. No delay on governance execution. Detection: Check for Timelock contract.",
        "governance_attack",
    ),
    (
        "Low Quorum Vulnerability. HIGH governance_attack. Quorum <5% supply enables flash loan voting. Detection: Check governance params.",
        "governance_attack",
    ),
    (
        "Compromised Multi-sig. CRITICAL governance_attack. Attacker gains multi-sig keys. Detection: Monitor signer composition threshold changes.",
        "governance_attack",
    ),
    (
        "Single Oracle Dependency. HIGH oracle_manipulation. Protocol uses one price feed. Detection: Count oracle sources flag single source.",
        "oracle_manipulation",
    ),
    (
        "Low-Liquidity Oracle Pool. CRITICAL oracle_manipulation. Oracle pool < $1M easily manipulated. Real: Mango Markets $114M Bonq DAO $120M.",
        "oracle_manipulation",
    ),
    (
        "Stale Oracle Data. MEDIUM oracle_manipulation. Price feed not updated recently. Detection: Check updatedAt timestamp vs heartbeat.",
        "oracle_manipulation",
    ),
    (
        "Short TWAP Period. MEDIUM oracle_manipulation. TWAP < 30min easily manipulated. Detection: Check observation period.",
        "oracle_manipulation",
    ),
    (
        "UUPS Upgrade Rug. CRITICAL proxy_attack. Owner upgrades to malicious implementation. Selector: 0x4f1ef286. Detection: Check proxy type upgrade auth.",
        "proxy_attack",
    ),
    (
        "Proxy Admin Control. HIGH proxy_attack. EOA admin can change implementation instantly. Detection: Check admin address type.",
        "proxy_attack",
    ),
    (
        "Implementation Swap. CRITICAL proxy_attack. Swap implementation to drain contract. Detection: Monitor implementation address changes.",
        "proxy_attack",
    ),
    (
        "Reentrancy Drain. CRITICAL exploit. External call before state update allows recursive withdraw. Real: The DAO $60M 2016. Detection: Slither reentrancy-detector.",
        "exploit",
    ),
    (
        "Access Control Bypass. HIGH exploit. Missing onlyOwner on mint/withdraw. Detection: Check function modifiers. Selector: 0xa0712d68.",
        "exploit",
    ),
    (
        "ERC4626 Vault Inflation. HIGH exploit. Deposit 1 wei donate tokens inflate share price. Detection: Check for initial deposit minimum threshold.",
        "exploit",
    ),
    (
        "ERC777 Callback Reentrancy. HIGH exploit. tokensReceived callback exploited. Real: imBTC $340K Lendf.me $25M.",
        "exploit",
    ),
    (
        "Telegram Shill Campaign. HIGH social_engineering. Paid shillers promote token in multiple TG groups simultaneously. Detection: Cross-group message timing.",
        "social_engineering",
    ),
    (
        "Fake Project Website. HIGH social_engineering. Clone website with modified contract addresses. Detection: HTML hash comparison address verification.",
        "social_engineering",
    ),
    (
        "Drain Approval Slow. CRITICAL phishing. Malicious contract slowly drains approved tokens via small transferFrom. Detection: Monitor recurring transferFrom.",
        "phishing",
    ),
    (
        "Token Migration Scam. HIGH phishing. Fake V1 to V2 migration steals tokens via approval. Detection: Verify migration through official channels.",
        "phishing",
    ),
    (
        "SafeMoon Clone Vulnerabilities. HIGH rug_pull. Owner can modify tax rates unchecked LP lock claims blacklist function trading toggle. Detection: Bytecode comparison against SafeMoon variants.",
        "rug_pull",
    ),
]

# Contract audits
ca = [
    (
        "OpenZeppelin ERC20 Standard. SAFE. Widely used audited implementation. Known selectors: totalSupply balanceOf transfer allowance approve transferFrom. No exposed mint function. Multiple audits by OpenZeppelin Consensys.",
        "audit",
    ),
    (
        "Unchecked External Call. MEDIUM vulnerability. External call return value not checked. Fix: Use SafeERC20 or check return value. Slither: check-unchecked-returns.",
        "audit",
    ),
    (
        "Integer Overflow. HIGH vulnerability pre-0.8.0. Arithmetic overflow without SafeMath. Real: BEC token $9M. Fix: Use Solidity 0.8.0+ or SafeMath.",
        "audit",
    ),
    (
        "Timelock Security Pattern. SAFE. 48h+ delay on governance execution. OpenZeppelin TimelockController. Verify delay >= 48h check executor roles.",
        "audit",
    ),
    (
        "Multisig Wallet Security. SAFE. M-of-N with N>=3. Gnosis Safe is standard. Check signer count threshold ratio.",
        "audit",
    ),
    (
        "Flash Loan Guard Pattern. SAFE. Block flash loan attacks by checking block.number between deposit and action. require(block.number > lastDepositBlock).",
        "audit",
    ),
    (
        "Reentrancy Guard. SAFE. OpenZeppelin ReentrancyGuard nonReentrant modifier. Verify on all state-changing external functions.",
        "audit",
    ),
    (
        "Proxy Security Assessment. SAFE when: admin is timelocked multisig upgrade requires governance. Check admin type verify timelock on upgrades.",
        "audit",
    ),
    (
        "Oracle Security Assessment. SAFE when: Chainlink with fallback TWAP >= 30min multiple sources with median. Unsafe: single Uniswap pool TWAP < 10min.",
        "audit",
    ),
    (
        "ERC20 Security Checklist. 1) Owner can mint? Capped? 2) Pause transfers? 3) Sell tax modifiable? Capped? 4) Blacklist? 5) Self-destruct? 6) Proxy+upgrade? 7) Ownership renounced? 8) LP locked? 9) Trading toggle? 10) Hidden bytecode functions?",
        "audit",
    ),
    (
        "EIP-1967 Proxy Storage Slots. Implementation: 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc. Admin: 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103. Read via eth_getStorageAt.",
        "audit",
    ),
    (
        "4-byte Function Selector Database. Known rug selectors: 0xa0712d68 mint 0x3ccfd60b withdraw 0x4b2e2f5b setTaxRate 0x8456cb59 pause 0xe9a4db3e setBlacklistAddress 0x4f1ef286 upgradeToAndCall 0xf3fef3a3 drain 0x5a3f0b2c rescueTokens.",
        "audit",
    ),
    (
        "Heimdall Decompiler. Tool for unverified contracts. Command: heimdall decompile --target 0xADDRESS. ABI recovery function signature matching control flow graph.",
        "audit",
    ),
    (
        "Slither Static Analysis. 80+ detectors. reentrancy uninitialized-variables unchecked-calls shadowed-variables delegatecall-to-untrusted unchecked-returns ERC20 violations access-control. Command: slither contract.sol --check-all.",
        "audit",
    ),
    (
        "Uniswap V2 Flash Swap Vulnerability. swap(0 amount to data) triggers uniswapV2Call callback. Can manipulate spot price. Fix: Use TWAP not spot price.",
        "audit",
    ),
    (
        "Compound Governor Alpha Issues. No timelock on execution quorum is total supply not circulating no vote delegation revocation. Enabled flash loan governance attacks. Use Governor Bravo instead.",
        "audit",
    ),
    (
        "Chainlink Oracle Best Practices. 1) Check latestRoundData return values including updatedAt 2) Verify price > 0 3) Set heartbeat timeout 3600s 4) Use fallback oracle 5) Multiple feeds cross-validation.",
        "audit",
    ),
    (
        "Aave Flash Loan Security. Fee: 0.05% V2 0.05-0.09% V3. Attack: borrow manipulate DEX price exploit protocol repay. Monitor flash loans > $1M.",
        "audit",
    ),
]

# Transaction patterns
tp = [
    (
        "Flash Loan Borrow-Exploit-Repay Transaction. Single atomic tx: Aave borrow price manipulation protocol exploit repay. Trace depth >10 internal calls. Detection: Analyze trace for borrow-repay bookends.",
        "tx_pattern",
    ),
    (
        "Sandwich Attack Transaction. Frontrun buy victim swap backrun sell in same block. High gas on frontrun. Detection: Compare gas prices identify searcher addresses.",
        "tx_pattern",
    ),
    (
        "Rug Pull Liquidity Removal Tx. Deployer removes all LP tokens. Large removeLiquidity from deployer address. Detection: Monitor deployer LP balance alert on >50% withdrawal.",
        "tx_pattern",
    ),
    (
        "Mint and Dump Transaction. Owner mints large amount then immediately sells. Mint tx followed by swap in same/next block. Detection: Track owner mint calls subsequent sells.",
        "tx_pattern",
    ),
    (
        "Wash Trading Circular Transfer. Tokens cycle A->B->C->A same amounts few blocks. Detection: Transfer graph cycle detection.",
        "tx_pattern",
    ),
    (
        "CEX Funded Sniper. Wallet funded from Binance/OKX hot wallet buys token at launch sells within minutes. Detection: Check funding source against CEX addresses.",
        "tx_pattern",
    ),
    (
        "Governance Proposal Execution. Malicious proposal queued and executed. Decode proposal calldata check target contracts. Detection: Verify execution impact.",
        "tx_pattern",
    ),
    (
        "Proxy Implementation Upgrade Tx. Storage slot 0x360894a changes value. upgradeTo() called. Detection: Monitor implementation slot changes verify new bytecode.",
        "tx_pattern",
    ),
    (
        "Phishing Approval Transaction. approve(spender MAX_UINT256) or permit signature followed by transferFrom drain. Detection: Monitors max-value approvals.",
        "tx_pattern",
    ),
    (
        "Pump Dump Volume Pattern. Volume 5-100x average buy from fresh wallets sell from team wallets. Detection: Rolling volume baseline wallet age analysis.",
        "tx_pattern",
    ),
    (
        "Post-Approval Drain. transferFrom by non-owner after recent approval. Detection: Monitor transferFrom patterns where spender was recently approved.",
        "tx_pattern",
    ),
    (
        "Dusting Attack. Hundreds of tiny transfers from single source. Purpose: track wallet activity address poisoning. Detection: Flag mass small-value transfers.",
        "tx_pattern",
    ),
    (
        "Cross-Chain Bridge Exploit. Large withdrawal without matching deposit. Real: Ronin $624M Wormhole $326M Nomad $190M. Detection: Verify deposit-lock-mint cycle integrity.",
        "tx_pattern",
    ),
    (
        "Team Wallet Gradual Dump. Small sells over days/weeks add up to significant supply %. Detection: Track cumulative team sell volume.",
        "tx_pattern",
    ),
    (
        "Flash Loan Arbitrage Cascade. Borrow Aave manipulate Protocol A use A output to attack Protocol B repay. Trace depth >15. Detection: Analyze trace depth protocol sequence.",
        "tx_pattern",
    ),
    (
        "Token Launch Sniper. Multiple bots buy in same block as liquidity add. First block >5 buys from fresh wallets. Detection: First block composition analysis.",
        "tx_pattern",
    ),
    (
        "Liquidity Migration Rug. Removes liquidity from DEX A never adds to DEX B. Funds sent to team wallet. Detection: Monitor liquidity events verify migration.",
        "tx_pattern",
    ),
    (
        "Staking Reward Inflation. APY >500% funded by minting new tokens. Price death spiral. Detection: Check APY vs inflation rate compute real yield.",
        "tx_pattern",
    ),
    (
        "Private Key Compromise. Wallet inactive then sudden large transfers to unknown addresses. All assets liquidated. Detection: Compare against known wallet behavior.",
        "tx_pattern",
    ),
    (
        "Manipulated Oracle Price. Price deviation >5% from market. Preceded by large pool-displacing swap. Detection: Compare against multiple market sources flag >2% deviation.",
        "tx_pattern",
    ),
    (
        "NFT Floor Price Manipulation. Related wallets trade same NFT at escalating prices. Use inflated floor as loan collateral. Detection: Cross-reference wallet funding compute demand.",
        "tx_pattern",
    ),
]

print("Populating RAG collections...")
total = 0
for content, cat in sp:
    r = ingest("scam_patterns", content, {"category": cat})
    if r:
        total += 1
print(f"scam_patterns: {len(sp)} sent, {total} ok so far")

ca_ok = 0
for content, cat in ca:
    r = ingest("contract_audits", content, {"category": cat})
    if r:
        ca_ok += 1
print(f"contract_audits: {len(ca)} sent, {ca_ok} ok")

tp_ok = 0
for content, cat in tp:
    r = ingest("transaction_patterns", content, {"category": cat})
    if r:
        tp_ok += 1
print(f"transaction_patterns: {len(tp)} sent, {tp_ok} ok")

print(f"\nTOTAL: {total + ca_ok + tp_ok} documents ingested")
