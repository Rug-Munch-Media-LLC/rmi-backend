"""Attack-pattern knowledge base entries (contract audit patterns)."""

from __future__ import annotations

from typing import Any

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
    {
        "doc_id": "audit:reentrancy_pattern",
        "content": (
            "REENTRANCY AUDIT PATTERN: The classic EVM exploit (2016 DAO hack, ~$60M). The "
            "vulnerable shape makes an external call BEFORE updating state, so a malicious "
            "recipient can re-enter the function while balances are stale:\n"
            "```solidity\n"
            "function withdraw(uint256 amount) external {\n"
            "    require(balances[msg.sender] >= amount);\n"
            '    (bool ok, ) = msg.sender.call{value: amount}(""); // interaction FIRST\n'
            "    balances[msg.sender] -= amount;                     // effect LAST\n"
            "}\n"
            "```\n"
            "Variants: cross-function reentrancy (re-entering a different function with a "
            "shared flag), ERC-777/ERC-1155 callback reentrancy via tokensReceived/on"
            "ERC1155Received, and read-only reentrancy (view functions feeding stale "
            "balances to other protocols). Mitigations: checks-effects-interactions, "
            "nonReentrant guards, pull-over-push payments. Audit: flag every external call "
            "before a state update and every custom fallback/recipient hook. Severity: "
            "CRITICAL."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "critical", "pattern": "reentrancy"},
    },
    {
        "doc_id": "audit:integer_overflow_pattern",
        "content": (
            "INTEGER OVERFLOW/UNDERFLOW AUDIT PATTERN: Pre-0.8 Solidity silently wrapped on "
            "arithmetic overflow, letting balances become astronomically large or "
            "underflow to near-max:\n"
            "```solidity\n"
            "// Solidity < 0.8: wraps silently\n"
            "function transfer(address to, uint256 amount) external {\n"
            "    balances[msg.sender] -= amount; // underflow if amount > balance\n"
            "    balances[to] += amount;         // overflow wraps\n"
            "}\n"
            "```\n"
            "Solidity 0.8+ reverts by default, but unchecked blocks, unsafe casts, and "
            "non-EVM chains still wrap. Audit: check arithmetic on balances, allowances, "
            "and token amounts; look for unchecked{} blocks, casts, and custom VMs. "
            "Severity: HIGH."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "high", "pattern": "integer_overflow"},
    },
    {
        "doc_id": "audit:tx_origin_pattern",
        "content": (
            "TX.ORIGIN AUTHORIZATION AUDIT PATTERN: tx.origin returns the account that "
            "STARTED the transaction, not the immediate caller, so a phishing contract "
            "can pass an owner check:\n"
            "```solidity\n"
            "function withdrawAll() external {\n"
            "    require(tx.origin == owner); // attacker's contract passes this\n"
            "    payable(owner).transfer(address(this).balance);\n"
            "}\n"
            "```\n"
            "Victim calls attacker's contract; attacker's contract calls withdrawAll(); "
            "tx.origin is the victim, so the check passes. Use msg.sender for "
            "authorization. tx.origin is only acceptable for explicit anti-phishing "
            "conditions. Audit: grep for tx.origin in require/if conditions. Severity: "
            "HIGH."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "high", "pattern": "tx_origin"},
    },
    {
        "doc_id": "audit:unchecked_external_call_pattern",
        "content": (
            "UNCHECKED EXTERNAL CALL AUDIT PATTERN: Low-level calls return a success "
            "boolean instead of reverting; ignoring it lets a contract continue as if the "
            "call succeeded:\n"
            "```solidity\n"
            '(bool success, ) = recipient.call{value: amount}("");\n'
            "// success ignored — accounting proceeds as if the payment landed\n"
            "```\n"
            "This corrupts balances and lets attackers complete flows that should have "
            "failed. Also related: transfer()/send() with fixed 2300 gas can brick "
            "payments as gas costs rise. Audit: every external interaction must handle "
            "failure explicitly; prefer high-level interface calls that revert. Severity: "
            "HIGH."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "high", "pattern": "unchecked_call"},
    },
    {
        "doc_id": "audit:delegatecall_pattern",
        "content": (
            "DELEGATECALL AUDIT PATTERN: delegatecall executes the callee's code in the "
            "CALLER's storage context, so pointing it at an untrusted address lets that "
            "address rewrite any storage variable — owner, balances, everything. The 2016 "
            "Parity wallet hack destroyed ~$150M of ETH this way:\n"
            "```solidity\n"
            "function execute(address target, bytes calldata data) external {\n"
            "    target.delegatecall(data); // runs in THIS contract's storage\n"
            "}\n"
            "```\n"
            "Audit: guard the target address, keep proxy storage collision-free, and "
            "never let users supply arbitrary delegatecall targets. Severity: CRITICAL."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "critical", "pattern": "delegatecall"},
    },
    {
        "doc_id": "audit:access_control_pattern",
        "content": (
            "ACCESS CONTROL AUDIT PATTERN: Privileged functions callable by anyone because "
            "the owner/admin modifier is missing. Ranks first in OWASP's smart-contract "
            "top 10 and caused the largest dollar losses of any vulnerability class in "
            "2024-2025 incident data:\n"
            "```solidity\n"
            "function mint(address to, uint256 amount) external { // no onlyOwner\n"
            "    _mint(to, amount); // anyone can mint unlimited supply\n"
            "}\n"
            "```\n"
            "Audit: enumerate every state-changing function, verify a role check exists, "
            "and test that unprivileged callers revert. Also check proxy admins, "
            "upgrade paths, and roles that can bypass a renounced owner. Severity: "
            "CRITICAL."
        ),
        "metadata": {"category": "vulnerability_pattern", "severity": "critical", "pattern": "access_control"},
    },
]
