"""
Scam School — Intermediate Course: "Scam Detection & Analysis"
12 modules covering honeypots, holder analysis, LP forensics, smart contract vulnerabilities,
cross-chain threats, NFT scams, social sentiment, and building investigations.
"""

from __future__ import annotations

scam_detection = {
    "id": "scam-detection",
    "title": "Scam Detection & Analysis",
    "subtitle": "Master the art of spotting rugs before they happen",
    "description": (
        "Deep-dive into honeypots, tokenomics red flags, holder distribution analysis, LP forensics, "
        "smart contract vulnerabilities, cross-chain threats, NFT scams, social sentiment manipulation, "
        "and building professional scam investigation reports."
    ),
    "difficulty": "intermediate",
    "duration_minutes": 180,
    "lessons_count": 12,
    "xp_reward": 2400,
    "badge_reward": "scam-hunter",
    "color": "#ef4444",
    "icon": "siren",
    "modules": [
        # ── Module 1 ──
        {
            "id": "honeypot-contracts",
            "title": "Deep Dive: Honeypot Contracts",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Honeypot Trap",
                        "sub": "You can buy, but you can never sell",
                    },
                    {
                        "type": "honeypot-sim",
                        "title": "Honeypot Simulator",
                        "description": (
                            "A live trading simulation. You 'buy' a token, the price goes up 10x. Try to sell — "
                            "the button is disabled. Watch how the contract code silently blocks all sell attempts "
                            "while still accepting buys."
                        ),
                    },
                    {
                        "type": "code-inspection",
                        "title": "Spot the Honeypot Code",
                        "description": (
                            "Two Solidity snippets side-by-side. One is a legitimate transfer function, "
                            "the other contains a hidden check that reverts on sells. Find the malicious logic "
                            "in 60 seconds."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What mechanism do most honeypots use to block sells?",
                        "options": [
                            "They delete the token from your wallet",
                            "The transfer function checks if the recipient is the DEX pair and reverts silently",
                            "They raise the gas fee to 100%",
                            "They change the token name",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Most honeypots add a check in the transfer function: if the recipient is the "
                            "Uniswap/PancakeSwap pair address (meaning you're trying to sell), the transaction "
                            "reverts. Meanwhile, buying works fine — trapping your funds."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Testing with Small Amounts",
                        "description": (
                            "Before committing significant capital to any new token, send a tiny test buy "
                            "(e.g., $5) and immediately attempt to sell. If you can't sell, the token is "
                            "a honeypot and you've only lost $5 instead of $5,000."
                        ),
                    },
                ]
            },
        },
        # ── Module 2 ──
        {
            "id": "holder-distribution-analysis",
            "title": "Analyzing Token Holder Distribution",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Who Really Owns This Token?",
                        "sub": "Holder analysis reveals the truth behind any token",
                    },
                    {
                        "type": "interactive",
                        "title": "Holder Distribution Visualizer",
                        "description": (
                            "Analyze three tokens' holder distributions side-by-side. Token A: natural distribution "
                            "with no single holder above 5%. Token B: Top 10 wallets hold 92%. Token C: 40 wallets "
                            "all funded by the same address. Which would you invest in?"
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "Why is a high concentration of tokens in the top 10 wallets dangerous?",
                        "options": [
                            "They might stake the tokens",
                            "A single coordinated sell-off could crash the price instantly",
                            "They pay more gas",
                            "It looks bad on Etherscan",
                        ],
                        "correct": 1,
                        "explanation": (
                            "If the top 10 holders control 80%+ of the supply, any one of them can trigger "
                            "a cascade sell-off. Combined with bundler patterns, this signals a coordinated "
                            "insider group ready to dump."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Wallet Clustering: Find Hidden Connections",
                        "description": (
                            "Given 20 wallet addresses, trace their funding sources. 15 were funded by the "
                            "same master wallet 2 hours before launch. These are connected insiders, not "
                            "organic buyers. Learn to spot this pattern."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is a 'bundler' pattern in token launches?",
                        "options": [
                            "A group of friends buying together publicly",
                            "Multiple wallets funded from one source, buying simultaneously to fake demand",
                            "People using the same exchange",
                            "Wallets with similar names",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Bundlers create dozens of wallets, fund them from a single source, and program "
                            "them to buy at launch simultaneously. This creates artificial demand that tricks "
                            "real buyers into FOMO, then the bundlers dump."
                        ),
                    },
                ]
            },
        },
        # ── Module 3 ──
        {
            "id": "liquidity-pool-forensics",
            "title": "Liquidity Pool Forensics",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Liquidity: Locked vs Burned vs Exposed",
                        "sub": "Understanding the three LP states and what they mean",
                    },
                    {
                        "type": "interactive",
                        "title": "LP Lock Simulator",
                        "description": (
                            "Visual comparison: Locked LP (dev cannot access for X time), Burned LP "
                            "(permanently removed, no one can touch), and Unlocked LP (dev can drain "
                            "at ANY moment). See the risk difference in real-time."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the difference between 'locked' and 'burned' LP?",
                        "options": [
                            "They are the same thing",
                            "Locked LP is temporarily inaccessible; burned LP is permanently destroyed",
                            "Locked is safer than burned",
                            "Burned LP can be recovered",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Locked LP: tokens are held in a time-lock contract and will unlock at a future date. "
                            "Burned LP: tokens are sent to a dead address (0x000...), permanently removed. "
                            "Burned is safer. Locked still requires trust that the dev won't drain after unlock."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "LP Removal Detection",
                        "description": (
                            "Monitor a token where the dev withdraws liquidity over 3 small transactions "
                            "instead of one big one — trying to avoid detection. Learn how to spot incremental "
                            "LP removal patterns that scanners often miss."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is 'migration risk' in liquidity pools?",
                        "options": [
                            "Moving to a new website",
                            "The dev can migrate liquidity to a new pool they control, making the old token worthless",
                            "Updating the token logo",
                            "Changing the token name",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Some contracts allow the owner to migrate LP to a new pool. This effectively creates "
                            "a new token while leaving holders of the old one with worthless assets. Always check "
                            "for migration functions."
                        ),
                    },
                ]
            },
        },
        # ── Module 4 ──
        {
            "id": "smart-contract-vulnerability-patterns",
            "title": "Smart Contract Vulnerability Patterns",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Code Vulnerabilities That Drain Funds",
                        "sub": "Understanding the most common contract exploits",
                    },
                    {
                        "type": "interactive",
                        "title": "Vulnerability Lab",
                        "description": (
                            "Interactive exploration of 5 vulnerability types: (1) Reentrancy — how recursive calls "
                            "drain contracts, (2) Flash loan attacks — borrowing massive capital with no collateral, "
                            "(3) Oracle manipulation — feeding false prices, (4) Unlimited mint — infinite token creation, "
                            "(5) Unchecked external calls — silent failures that enable theft."
                        ),
                    },
                    {
                        "type": "code-inspection",
                        "title": "Find the Reentrancy Bug",
                        "description": (
                            "A contract has a classic reentrancy vulnerability in its withdraw function. "
                            "The balance is updated AFTER sending ETH, allowing an attacker to call withdraw "
                            "recursively. Find and fix the bug."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What enables a flash loan attack?",
                        "options": [
                            "A vulnerability in the wallet",
                            "Borrowing unlimited funds with zero collateral, manipulating a protocol, and repaying in one transaction",
                            "A bug in Etherscan",
                            "Using a different blockchain",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Flash loans let you borrow any amount with 0 collateral as long as it's repaid "
                            "in the same transaction. Attackers use this to manipulate oracle prices, drain "
                            "lending pools, or exploit arbitrage at massive scale."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the most dangerous single function a token contract can have?",
                        "options": [
                            "transfer()",
                            "mint() — especially without a cap or when ownership is not renounced",
                            "approve()",
                            "balanceOf()",
                        ],
                        "correct": 1,
                        "explanation": (
                            "An unlimited mint function lets the owner create infinite tokens, diluting holders "
                            "to zero. Always check: is there a mint function? Is there a supply cap? Has ownership "
                            "been renounced (so nobody can call it)?"
                        ),
                    },
                ]
            },
        },
        # ── Module 5 ──
        {
            "id": "proxy-contract-risks",
            "title": "Proxy Contract Risks",
            "type": "interactive",
            "duration": 14,
            "xp": 180,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Upgradeable Trap",
                        "sub": "Why proxy contracts can be more dangerous than immutable ones",
                    },
                    {
                        "type": "interactive",
                        "title": "Proxy Contract Visualizer",
                        "description": (
                            "See how a proxy contract works: user interacts with proxy → proxy delegates to "
                            "implementation contract. The admin can swap the implementation at any time, "
                            "changing all logic without warning. What was a legitimate token yesterday "
                            "can become a honeypot today."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the biggest risk of an upgradeable proxy where a single EOA holds the admin key?",
                        "options": [
                            "Higher gas costs",
                            "That single person can change the contract logic to steal all funds instantly",
                            "The contract can't be verified",
                            "Trading takes longer",
                        ],
                        "correct": 1,
                        "explanation": (
                            "If one person controls the proxy admin key, they can upgrade the implementation "
                            "to a malicious contract at any moment. This is why multisig wallets and timelocks "
                            "are essential for proxy admin control."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Multisig & Timelock Analysis",
                        "description": (
                            "Compare three proxy setups: (A) Single EOA admin — extremely dangerous. "
                            "(B) 3-of-5 multisig — better. (C) 3-of-5 multisig + 48-hour timelock — "
                            "best practice. The timelock gives users time to exit before changes take effect."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "How can you check if a contract is a proxy and who controls it?",
                        "options": [
                            "You can't — proxies are invisible",
                            "Check for implementation slot, look at admin functions, and verify on Etherscan's proxy tab",
                            "Ask in the Discord",
                            "Check the token price",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Etherscan shows a 'Proxy' tab for known proxy patterns (EIP-1967, OpenZeppelin, etc.). "
                            "You can also call admin() or owner() on the contract to see who controls upgrades."
                        ),
                    },
                ]
            },
        },
        # ── Module 6 ──
        {
            "id": "tokenomics-red-flags",
            "title": "Tokenomics Red Flags",
            "type": "interactive",
            "duration": 14,
            "xp": 180,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Math That Steals Your Money",
                        "sub": "Bad tokenomics are a preview of bad outcomes",
                    },
                    {
                        "type": "allocation-sim",
                        "title": "Token Allocation Designer",
                        "description": (
                            "Design a token allocation. See what happens when a team holds 5% vs 50% vs 80%. "
                            "Simulate the price impact when the team's tokens unlock. Notice how 80% insider "
                            "allocation = guaranteed dump."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A token has a 10% buy tax and 10% sell tax. After buying and immediately selling, you've lost:",
                        "options": [
                            "0% — the taxes cancel out",
                            "~19% of your capital (you lose 10% on buy, then 10% on the remaining 90%)",
                            "10% — only the sell tax matters",
                            "5% — the average of both taxes",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Buy with $100 → $10 tax → $90 worth of tokens. Sell $90 → $9 tax → $81 back. "
                            "You've lost $19 total. High buy/sell taxes are a common mechanism for devs to "
                            "extract value from every transaction."
                        ),
                    },
                    {
                        "type": "red-flag-checklist",
                        "title": "Tokenomics Red Flag Checklist",
                        "items": [
                            {"flag": "Total supply not fixed (can mint more)", "risk": "Infinite dilution possible"},
                            {"flag": "Team allocation > 20%", "risk": "Concentrated dump risk"},
                            {"flag": "Buy/sell tax > 5%", "risk": "Excessive extraction per trade"},
                            {"flag": "Unfair vesting (team unlocks before community)", "risk": "Insiders dump first"},
                            {
                                "flag": "Reflection/redistribution mechanics",
                                "risk": "Often used to hide tax extraction",
                            },
                            {"flag": "No burn mechanism or deflationary model", "risk": "Supply only increases"},
                        ],
                    },
                    {
                        "type": "quiz",
                        "question": "What is the danger of 'reflection' or 'redistribution' tokenomics?",
                        "options": [
                            "They make tokens faster",
                            "They can hide high effective tax rates while marketing it as 'earning passive rewards'",
                            "They increase security",
                            "They are always legitimate",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Reflection tokens often take a 10-15% tax on every transaction and redistribute "
                            "a portion to holders. The effective cost to traders is high, but it's marketed as "
                            "'earning rewards.' Always calculate the real cost before buying."
                        ),
                    },
                ]
            },
        },
        # ── Module 7 ──
        {
            "id": "dex-manipulation-techniques",
            "title": "DEX Manipulation Techniques",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Artificial Market",
                        "sub": "How scammers fake volume, price, and demand",
                    },
                    {
                        "type": "chart-sim",
                        "title": "Wash Trading Visualization",
                        "description": (
                            "Watch live: Wallet A buys from Wallet B, Wallet B buys from Wallet A, repeat. "
                            "Volume spikes, chart looks active, but it's the same money going in circles. "
                            "Learn to distinguish real vs fake volume."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is wash trading in crypto?",
                        "options": [
                            "Washing your tokens after a hack",
                            "Simultaneously buying and selling from wallets you control to fake trading volume",
                            "Using a wash sale for tax benefits",
                            "Cleaning up old transactions",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Wash trading is buying and selling the same asset between wallets you control "
                            "to create the illusion of market activity. It's illegal in traditional markets "
                            "but rampant in unregulated crypto."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Sandwich Attack Simulator",
                        "description": (
                            "You submit a buy order for 1 ETH of a token. A MEV bot sees your transaction in the "
                            "mempool, buys before you (raising the price), lets you buy at the inflated price, "
                            "then sells immediately after (dropping the price). See the profit extraction in real-time."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "How can you protect yourself from sandwich attacks?",
                        "options": [
                            "You can't — they're unavoidable",
                            "Use low slippage (0.5-1%), DEX aggregators with MEV protection, or private mempool transactions",
                            "Trade only at night",
                            "Use a different wallet for each trade",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Low slippage limits how much bots can profit. DEX aggregators like 1inch and Jupiter "
                            "have built-in MEV protection. Services like Flashbots allow submitting transactions "
                            "directly to miners, bypassing the public mempool."
                        ),
                    },
                ]
            },
        },
        # ── Module 8 ──
        {
            "id": "cross-chain-scam-patterns",
            "title": "Cross-Chain Scam Patterns",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Bridging to Oblivion",
                        "sub": "How cross-chain complexity enables new types of scams",
                    },
                    {
                        "type": "interactive",
                        "title": "Bridge Exploit Timeline",
                        "description": (
                            "Interactive timeline of major bridge exploits: Wormhole ($326M), Ronin ($624M), "
                            "Nomad ($190M), BNB Bridge ($566M). See the common vulnerability patterns: "
                            "message verification failures, validator compromises, and replay attacks."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What was the root cause of the Wormhole bridge exploit?",
                        "options": [
                            "A phishing attack on a developer",
                            "A signature verification bug that allowed the attacker to mint 120K wrapped ETH without depositing real ETH",
                            "A DDoS on the Solana network",
                            "A rug pull by the Wormhole team",
                        ],
                        "correct": 1,
                        "explanation": (
                            "The attacker exploited a missing signature verification check, allowing them "
                            "to forge a guardian signature and mint 120,000 wrapped ETH on Solana without "
                            "depositing anything on Ethereum."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Wrapped Token Risks",
                        "description": (
                            "When you bridge USDC from Ethereum to Solana, you receive a wrapped version. "
                            "If the bridge is compromised, that wrapped token can become unbacked and worthless. "
                            "Learn to verify bridge security and assess wrapped asset risk."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is a 'replay attack' in cross-chain contexts?",
                        "options": [
                            "Playing a video of a transaction",
                            "Reusing a valid transaction from one chain on another chain where it wasn't intended",
                            "Repeating a trade multiple times",
                            "Recording a transaction for later analysis",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Without proper replay protection (chain ID, nonce), a transaction signed for "
                            "one chain can be replayed on another. This was a critical vulnerability in "
                            "early cross-chain designs and the Ethereum Classic fork."
                        ),
                    },
                ]
            },
        },
        # ── Module 9 ──
        {
            "id": "nft-scam-detection",
            "title": "NFT Scam Detection",
            "type": "interactive",
            "duration": 14,
            "xp": 180,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The NFT Minefield",
                        "sub": "Fake collections, wash trading, and hidden pitfalls",
                    },
                    {
                        "type": "interactive",
                        "title": "Fake Collection Detector",
                        "description": (
                            "Compare a real BAYC listing vs a fake clone. Learn to spot: incorrect contract "
                            "address, slightly different artwork, fake verification badges, copied metadata, "
                            "and bogus social proof."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is a common NFT scam where attackers create a collection that looks identical to a famous one?",
                        "options": [
                            "A phishing scam",
                            "A copycat/clone collection — using the same artwork with a different contract address",
                            "A rug pull",
                            "A smart contract exploit",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Clone collections copy the artwork of popular collections exactly, then list them "
                            "slightly below floor price. Buyers think they're getting a deal but receive a "
                            "worthless knockoff. Always verify the contract address."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "NFT Wash Trading Analysis",
                        "description": (
                            "Analyze an NFT collection's trading history: the same 5 wallets have bought and "
                            "sold items between each other 200 times. Learn to spot coordinated wash trading "
                            "designed to inflate volume and floor price."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is 'royalty theft' in the NFT space?",
                        "options": [
                            "Not paying artists",
                            "Using marketplaces that bypass royalty payments to creators by routing through custom contracts",
                            "Hacking the blockchain",
                            "Copying the artwork",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Some marketplaces and aggregators use custom contracts that bypass the royalty "
                            "mechanism, meaning creators get nothing when their work is resold. Always check "
                            "if a marketplace respects creator royalties."
                        ),
                    },
                ]
            },
        },
        # ── Module 10 ──
        {
            "id": "social-sentiment-analysis",
            "title": "Social Sentiment Analysis",
            "type": "interactive",
            "duration": 15,
            "xp": 200,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Bot Army",
                        "sub": "Detecting fake engagement and coordinated shilling",
                    },
                    {
                        "type": "interactive",
                        "title": "Bot Detection Lab",
                        "description": (
                            "Analyze a Twitter thread about a new token: 80% of replies are from accounts "
                            "created in the last 30 days, all using similar phrases, and all following the "
                            "same 3 accounts. Learn the telltale signs of bot networks."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is 'astroturfing' in crypto social media?",
                        "options": [
                            "Growing actual grass on a token logo",
                            "Creating fake grassroots support using bots and paid accounts to simulate organic enthusiasm",
                            "Launching a token on Astroport",
                            "A type of blockchain consensus",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Astroturfing is the practice of manufacturing fake organic support. Scammers "
                            "create hundreds of accounts to hype a token, making it look like a genuine "
                            "community movement when it's actually a coordinated marketing operation."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Coordinated Shilling Patterns",
                        "description": (
                            "Track a token's mentions across Twitter, Telegram, and Discord over 72 hours. "
                            "Notice the identical 'spikes' — hundreds of posts within the same 10-minute window, "
                            "all using similar language. This is a paid shill campaign, not organic interest."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A Telegram group for a new token has 50K members but only 20 people are actually chatting. What does this suggest?",
                        "options": [
                            "Most people prefer to just read",
                            "The member count is likely inflated with bots — real engagement is minimal",
                            "The group is very well organized",
                            "Everyone is in a different time zone",
                        ],
                        "correct": 1,
                        "explanation": (
                            "High member count with low engagement is a classic sign of bot-inflated numbers. "
                            "A genuine 50K community would have hundreds of active chatters. Always check the "
                            "ratio of members to active participants."
                        ),
                    },
                ]
            },
        },
        # ── Module 11 ──
        {
            "id": "memecoin-launch-patterns",
            "title": "Memecoin Launch Patterns",
            "type": "interactive",
            "duration": 14,
            "xp": 180,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Memecoin Casino",
                        "sub": "Understanding pump.fun mechanics and graduated token risks",
                    },
                    {
                        "type": "interactive",
                        "title": "Pump.fun Launch Simulator",
                        "description": (
                            "Simulate the lifecycle of a pump.fun token: creation → bonding curve → "
                            "graduation to Raydium. See how 99% of tokens die on the bonding curve and "
                            "why 'graduated' doesn't mean 'safe'."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "On pump.fun, what does a token 'graduating' mean?",
                        "options": [
                            "The token is now safe",
                            "It reached sufficient market cap to automatically deploy to Raydium with burned LP",
                            "The developers graduated from the platform",
                            "The token received an audit",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Graduation means the bonding curve completed and the token was deployed to "
                            "Raydium. While LP is burned in this process, graduation does NOT mean the "
                            "token is safe — graduated tokens can still be bundled, manipulated, or rugged."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Sniper Bot Detection on New Launches",
                        "description": (
                            "Watch a new pump.fun token get created. Within milliseconds, 20+ wallets buy "
                            "in. These are sniper bots. By the time it appears on the front page for humans, "
                            "the bots already own 30%+ of the supply. Learn to read the trade history."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What percentage of pump.fun tokens ever reach a $100K+ market cap?",
                        "options": [
                            "About 50%",
                            "About 25%",
                            "Less than 1% — the vast majority go to zero within hours",
                            "About 90%",
                        ],
                        "correct": 2,
                        "explanation": (
                            "The vast majority of memecoins go to zero quickly. The few that succeed often "
                            "have coordinated marketing and insider groups. Treat every memecoin as a lottery "
                            "ticket at best — and a scam at worst."
                        ),
                    },
                ]
            },
        },
        # ── Module 12: Final Exam ──
        {
            "id": "building-scam-investigation",
            "title": "Building a Scam Investigation",
            "type": "exam",
            "duration": 18,
            "xp": 280,
            "passing_score": 85,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Final Exam: Scam Detection & Analysis",
                        "sub": "20 questions · 85% to pass · Scam Hunter badge on completion",
                    },
                    {
                        "type": "interactive",
                        "title": "Full Investigation Workflow",
                        "description": (
                            "Step-by-step guide: (1) Gather on-chain evidence (transactions, holder data), "
                            "(2) Analyze contract code for vulnerabilities, (3) Trace fund flows through "
                            "wallets and mixers, (4) Collect off-chain evidence (social media, archives), "
                            "(5) Write investigation report, (6) Share findings with community."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A token has 92% of its supply in the top 10 wallets, unverified contract, and unlocked LP. Your assessment:",
                        "options": [
                            "Low risk",
                            "Extreme risk — multiple critical red flags present",
                            "Medium risk, worth a small investment",
                            "Safe because it has a website",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What does 'LP not locked or burned' mean for investors?",
                        "options": [
                            "Liquidity is guaranteed long-term",
                            "The developer can withdraw all liquidity at any moment, leaving tokens worthless",
                            "The token price will increase",
                            "Trading is disabled",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "You identify 30 wallets funded by the same address, all buying a new token in the first block. This is:",
                        "options": ["Organic demand", "A bundler attack", "Market making", "Staking rewards"],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What is the key indicator of wash trading?",
                        "options": [
                            "High volume from unique wallets",
                            "The same wallets repeatedly buying and selling to each other in circular patterns",
                            "Transactions during business hours",
                            "Trading on multiple exchanges",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "A proxy contract with a single EOA admin can:",
                        "options": [
                            "Nothing — it's locked",
                            "Be upgraded to malicious code at any moment by that one person",
                            "Only change minor parameters",
                            "Automatically audit itself",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What is the safest way to verify an NFT collection's authenticity?",
                        "options": [
                            "Check the artwork looks similar",
                            "Verify the contract address matches the official one from the project's verified channels",
                            "Ask in Discord",
                            "Check the collection name",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "A Telegram group with 100K members has 30 active chatters. This suggests:",
                        "options": [
                            "Most members are busy",
                            "The member count is heavily inflated with bots — treat all claims with skepticism",
                            "The community is very organized",
                            "It's a holiday",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What is the #1 rule of crypto safety?",
                        "options": [
                            "Always follow crypto influencers on Twitter",
                            "Never invest more than you can afford to lose, and always do your own research",
                            "Always buy the dip",
                            "Hold all tokens forever",
                        ],
                        "correct": 1,
                    },
                ]
            },
        },
    ],
}
