"""
Scam School API - Interactive Crypto Education Platform
=========================================================
Backend for the RMI Scam School: courses, lessons, progress tracking,
gamification (XP, levels, badges, streaks), certificates, and quizzes.

All data stored in Redis + Supabase backup.
"""

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.redis import get_redis
from typing import Any

logger = get_logger("scam_school")
router = APIRouter(tags=["scam-school"])

# ── Config ──
API_BASE = os.getenv("API_BASE", "https://rugmunch.io")


# ── Redis helper ──
async def get_current_user(request: Request) -> dict | None:
    """Extract user from JWT."""
    from app.auth import get_current_user as _get_user

    return await _get_user(request)


# ── Course Data (embedded - no external dependencies) ──

COURSES = [
    {
        "id": "crypto-basics",
        "title": "Crypto Basics",
        "subtitle": "The foundation every investor needs",
        "description": "Learn how blockchains work, what wallets are, how to read explorers, and why decentralization matters. No prior knowledge required.",
        "difficulty": "beginner",
        "duration_minutes": 45,
        "lessons_count": 6,
        "xp_reward": 500,
        "badge_reward": "crypto-cadet",
        "color": "#6366f1",
        "icon": "shield",
        "modules": [
            {
                "id": "what-is-blockchain",
                "title": "What is a Blockchain?",
                "type": "interactive",
                "duration": 8,
                "xp": 80,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Digital Ledger",
                            "sub": "Understanding blockchain in 3 minutes",
                        },
                        {
                            "type": "interactive-block",
                            "title": "Build a Chain",
                            "description": "Drag blocks to form a chain. Each block contains transactions. Change one block and watch the chain break.",
                        },
                        {
                            "type": "quiz",
                            "question": "What makes a blockchain immutable?",
                            "options": [
                                "It uses a database",
                                "Each block hashes the previous block",
                                "It is stored on one computer",
                                "It uses encryption",
                            ],
                            "correct": 1,
                            "explanation": "Each block contains the hash of the previous block. Change one transaction and every subsequent hash changes, making tampering obvious.",
                        },
                        {
                            "type": "simulation",
                            "title": "Hash Visualization",
                            "description": "Type any text and see its SHA-256 hash change in real-time. Even one letter difference creates a completely different hash.",
                        },
                    ]
                },
            },
            {
                "id": "wallets-explained",
                "title": "Wallets & Keys",
                "type": "interactive",
                "duration": 7,
                "xp": 70,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Your Crypto Wallet",
                            "sub": "Not actually a wallet",
                        },
                        {
                            "type": "interactive-keygen",
                            "title": "Generate a Key Pair",
                            "description": "Watch as a random private key is generated, and its corresponding public address is derived. Click to generate a new one.",
                        },
                        {
                            "type": "quiz",
                            "question": "What should you NEVER do with your private key?",
                            "options": [
                                "Store it in a hardware wallet",
                                "Share it with anyone",
                                "Write it on paper",
                                "Use it to sign transactions",
                            ],
                            "correct": 1,
                            "explanation": "Your private key is like the password to your entire account. Anyone with it controls your funds. NEVER share it, screenshot it, or paste it into websites.",
                        },
                        {
                            "type": "drag-drop",
                            "title": "Secure vs Insecure Storage",
                            "items": [
                                {"text": "Hardware wallet", "zone": "secure"},
                                {"text": "Screenshot on phone", "zone": "insecure"},
                                {"text": "Encrypted USB drive", "zone": "secure"},
                                {"text": "Email to yourself", "zone": "insecure"},
                                {"text": "Paper in safe", "zone": "secure"},
                                {"text": "Cloud storage", "zone": "insecure"},
                            ],
                        },
                    ]
                },
            },
            {
                "id": "reading-explorers",
                "title": "Reading Block Explorers",
                "type": "interactive",
                "duration": 10,
                "xp": 100,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Become a Blockchain Detective",
                            "sub": "How to read what really happened",
                        },
                        {
                            "type": "explorer-sim",
                            "title": "Live Explorer Simulation",
                            "description": "A simulated blockchain explorer showing real-looking transactions. Click through to trace a token from creation to first buyers.",
                        },
                        {
                            "type": "quiz",
                            "question": "What does a 'contract creation' transaction mean?",
                            "options": [
                                "Someone bought a token",
                                "A new smart contract was deployed",
                                "The blockchain was upgraded",
                                "A wallet was created",
                            ],
                            "correct": 1,
                            "explanation": "Contract creation means a developer deployed a new smart contract to the blockchain. This is how tokens, DEXs, and DeFi protocols are born.",
                        },
                        {
                            "type": "spot-the-difference",
                            "title": "Real vs Fake Explorer",
                            "description": "Two explorer screenshots side by side. One is real Etherscan, the other is a phishing clone. Spot 5 differences.",
                        },
                    ]
                },
            },
            {
                "id": "tokens-vs-coins",
                "title": "Coins vs Tokens",
                "type": "lesson",
                "duration": 5,
                "xp": 50,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Not All Crypto is Equal",
                            "sub": "The difference between layer-1 coins and ERC-20 tokens",
                        },
                        {
                            "type": "comparison",
                            "left": {
                                "title": "Coin (Layer 1)",
                                "examples": ["Bitcoin", "Ethereum", "Solana"],
                                "traits": [
                                    "Has its own blockchain",
                                    "Used for network fees",
                                    "Native to the protocol",
                                ],
                            },
                            "right": {
                                "title": "Token",
                                "examples": ["USDC", "SHIB", "PEPE"],
                                "traits": [
                                    "Built ON another blockchain",
                                    "Uses smart contracts",
                                    "Can be created by anyone",
                                ],
                            },
                        },
                        {
                            "type": "quiz",
                            "question": "Which can be created by anyone in 5 minutes?",
                            "options": [
                                "A new Bitcoin",
                                "A new Ethereum",
                                "A new token on Ethereum",
                                "A new Solana validator",
                            ],
                            "correct": 2,
                            "explanation": "Anyone can deploy an ERC-20 token on Ethereum for under $5. This is why there are 500,000+ tokens and most are worthless or scams.",
                        },
                    ]
                },
            },
            {
                "id": "gas-fees",
                "title": "Gas Fees & Transactions",
                "type": "interactive",
                "duration": 8,
                "xp": 80,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Why Transactions Cost Money",
                            "sub": "Gas: the fuel of the blockchain",
                        },
                        {
                            "type": "gas-simulator",
                            "title": "Gas Fee Simulator",
                            "description": "Adjust network congestion and transaction complexity. Watch how gas fees change in real-time. Compare Ethereum, Base, and Solana costs.",
                        },
                        {
                            "type": "quiz",
                            "question": "What happens if you set gas too low?",
                            "options": [
                                "Transaction is free",
                                "Transaction fails or gets stuck",
                                "You get a refund",
                                "It processes faster",
                            ],
                            "correct": 1,
                            "explanation": "If gas is too low, miners/validators won't include your transaction. It can get stuck 'pending' for hours or days until you cancel or replace it.",
                        },
                        {
                            "type": "interactive",
                            "title": "Estimate a Swap",
                            "description": "Simulate a token swap on Uniswap. Input amount, see price impact, slippage, and total gas cost before confirming.",
                        },
                    ]
                },
            },
            {
                "id": "crypto-basics-exam",
                "title": "Module Exam: Crypto Basics",
                "type": "exam",
                "duration": 7,
                "xp": 120,
                "passing_score": 80,
                "content": {
                    "slides": [
                        {
                            "type": "exam-intro",
                            "text": "Prove your knowledge",
                            "sub": "12 questions · 80% to pass · 3 attempts allowed",
                        },
                        {
                            "type": "quiz",
                            "question": "What is the primary purpose of a blockchain?",
                            "options": [
                                "To mine Bitcoin",
                                "To store decentralized, tamper-proof records",
                                "To replace banks",
                                "To create NFTs",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "Which of these is a Layer-1 blockchain?",
                            "options": ["USDC", "Uniswap", "Ethereum", "Chainlink"],
                            "correct": 2,
                        },
                        {
                            "type": "quiz",
                            "question": "What does 'immutable' mean in blockchain?",
                            "options": [
                                "Fast transactions",
                                "Cannot be changed after creation",
                                "Free to use",
                                "Anonymous",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "Where is the SAFEST place to store large amounts of crypto?",
                            "options": [
                                "Exchange wallet",
                                "Mobile app",
                                "Hardware wallet",
                                "Browser extension",
                            ],
                            "correct": 2,
                        },
                        {
                            "type": "quiz",
                            "question": "What is a 'private key'?",
                            "options": [
                                "Your wallet address",
                                "Your password to the blockchain",
                                "A type of cryptocurrency",
                                "A mining tool",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What happens to gas fees when the network is congested?",
                            "options": [
                                "They decrease",
                                "They increase",
                                "They stay the same",
                                "They become free",
                            ],
                            "correct": 1,
                        },
                    ]
                },
            },
        ],
    },
    {
        "id": "scam-detection",
        "title": "Scam Detection Masterclass",
        "subtitle": "Learn to spot rugs before they happen",
        "description": "The core RMI curriculum. Learn every red flag, from honeypots to coordinated bundling. Interactive simulations of real scam patterns.",
        "difficulty": "intermediate",
        "duration_minutes": 90,
        "lessons_count": 10,
        "xp_reward": 1200,
        "badge_reward": "scam-hunter",
        "color": "#ef4444",
        "icon": "siren",
        "modules": [
            {
                "id": "honeypots",
                "title": "Honeypot Traps",
                "type": "interactive",
                "duration": 12,
                "xp": 150,
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
                            "description": "A live trading simulation. You 'buy' a token, the price goes up. Try to sell... the button is disabled. Watch how the contract code prevents sells.",
                        },
                        {
                            "type": "code-inspection",
                            "title": "Spot the Honeypot Code",
                            "description": "Two smart contract code snippets. One is legitimate, one contains a honeypot. Find the malicious line.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is a honeypot token?",
                            "options": [
                                "A token with high rewards",
                                "A token you can buy but not sell",
                                "A token with locked liquidity",
                                "A token with a cute mascot",
                            ],
                            "correct": 1,
                            "explanation": "Honeypots allow buys (attracting victims) but block sells through smart contract code. Your money goes in, never comes out.",
                        },
                        {
                            "type": "interactive",
                            "title": "RMI Scanner Demo",
                            "description": "Paste a contract address (simulated) and watch the RMI scanner detect honeypot code, unverified contracts, and malicious functions in real-time.",
                        },
                    ]
                },
            },
            {
                "id": "rug-pull-mechanics",
                "title": "Rug Pull Mechanics",
                "type": "interactive",
                "duration": 15,
                "xp": 200,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Anatomy of a Rug Pull",
                            "sub": "From launch to drain in 48 hours",
                        },
                        {
                            "type": "timeline",
                            "title": "Rug Pull Timeline",
                            "events": [
                                {
                                    "time": "T-7 days",
                                    "event": "Dev creates token, socials, and website",
                                    "flag": False,
                                },
                                {
                                    "time": "T-1 day",
                                    "event": "Influencers paid to shill",
                                    "flag": True,
                                },
                                {
                                    "time": "T-0",
                                    "event": "Token launches with 80% insider allocation",
                                    "flag": True,
                                },
                                {
                                    "time": "T+2 hours",
                                    "event": "Price pumps 10x as insiders coordinate buys",
                                    "flag": True,
                                },
                                {
                                    "time": "T+6 hours",
                                    "event": "Insiders dump 60% of supply",
                                    "flag": True,
                                },
                                {
                                    "time": "T+12 hours",
                                    "event": "LP tokens withdrawn, liquidity drained",
                                    "flag": True,
                                },
                                {
                                    "time": "T+48 hours",
                                    "event": "Socials deleted, website down, dev gone",
                                    "flag": True,
                                },
                            ],
                        },
                        {
                            "type": "allocation-sim",
                            "title": "Token Allocation Simulator",
                            "description": "Design a token allocation. If the team holds more than 20%, the community is at risk. Watch what happens when insiders control 80%.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is the FIRST red flag in a rug pull timeline?",
                            "options": [
                                "Price drops",
                                "High insider allocation at launch",
                                "Social media deleted",
                                "Website goes down",
                            ],
                            "correct": 1,
                            "explanation": "The allocation is visible BEFORE you invest. If insiders hold 60-80%, they have the power to dump on you. Check this first.",
                        },
                        {
                            "type": "interactive",
                            "title": "Liquidity Lock Check",
                            "description": "Simulate checking LP tokens on a lock platform. Unlocked LP = dev can drain the pool anytime. Locked LP = safer (but not guaranteed).",
                        },
                    ]
                },
            },
            {
                "id": "bundler-detection",
                "title": "Bundler & Sniper Detection",
                "type": "interactive",
                "duration": 15,
                "xp": 200,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Coordinated Attack",
                            "sub": "How bundlers manipulate launches",
                        },
                        {
                            "type": "bundle-visualizer",
                            "title": "Bundle Visualizer",
                            "description": "Watch 20 connected wallets buy simultaneously at launch. The visualization shows wallet connections, buy timing, and total allocation. This is a coordinated bundler attack.",
                        },
                        {
                            "type": "sniper-sim",
                            "title": "Sniper Bot Simulation",
                            "description": "Simulate a token launch. Bots buy in the same block as creation. Normal users buy 10 blocks later at 5x the price. See the unfair advantage in real-time.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is a 'bundler' in crypto?",
                            "options": [
                                "A wallet that holds many tokens",
                                "A group of coordinated wallets buying together",
                                "A type of smart contract",
                                "A blockchain validator",
                            ],
                            "correct": 1,
                            "explanation": "Bundlers are networks of wallets controlled by the same entity. They buy simultaneously to hide their total allocation and create fake demand.",
                        },
                        {
                            "type": "interactive",
                            "title": "Wallet Cluster Analysis",
                            "description": "Given 10 wallet addresses, trace their funding sources. 8 were funded by the same wallet 1 hour before launch. This is the bundler's master wallet.",
                        },
                    ]
                },
            },
            {
                "id": "contract-red-flags",
                "title": "Smart Contract Red Flags",
                "type": "interactive",
                "duration": 12,
                "xp": 150,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Reading the Code",
                            "sub": "Red flags in smart contracts",
                        },
                        {
                            "type": "code-hunt",
                            "title": "Find the Kill Switch",
                            "description": "A smart contract has a hidden 'kill' function that lets the owner destroy all tokens. Find it in the code.",
                        },
                        {
                            "type": "red-flag-checklist",
                            "title": "Contract Red Flag Checklist",
                            "items": [
                                {
                                    "flag": "Mint function not renounced",
                                    "risk": "Dev can create infinite tokens",
                                },
                                {
                                    "flag": "Ownership not renounced",
                                    "risk": "Dev can change contract rules",
                                },
                                {
                                    "flag": "Hidden transfer fees > 10%",
                                    "risk": "Every buy/sell loses money to dev",
                                },
                                {
                                    "flag": "Blacklist function exists",
                                    "risk": "Dev can block you from selling",
                                },
                                {
                                    "flag": "Contract not verified",
                                    "risk": "You can't see what the code does",
                                },
                                {
                                    "flag": "Proxy contract with admin",
                                    "risk": "Dev can change code after launch",
                                },
                            ],
                        },
                        {
                            "type": "quiz",
                            "question": "Why is an unverified contract dangerous?",
                            "options": [
                                "It costs more gas",
                                "You can't read the source code",
                                "It has no logo",
                                "It can't be traded",
                            ],
                            "correct": 1,
                            "explanation": "Unverified contracts hide their source code. The deployer could have put anything in there - honeypots, backdoors, infinite mint functions. Always verify.",
                        },
                    ]
                },
            },
            {
                "id": "social-engineering",
                "title": "Social Engineering Attacks",
                "type": "interactive",
                "duration": 10,
                "xp": 120,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Human Exploit",
                            "sub": "When scammers hack your mind, not your wallet",
                        },
                        {
                            "type": "phishing-sim",
                            "title": "Phishing Simulation",
                            "description": "You receive a DM: 'Your wallet has been compromised. Click here to secure it immediately.' The link looks like MetaMask. What do you do? Interactive choose-your-own-adventure.",
                        },
                        {
                            "type": "fake-support",
                            "title": "Fake Support Scam",
                            "description": "A 'support agent' in Discord asks you to 'verify your wallet' by sharing your seed phrase. Walk through the conversation and spot the red flags.",
                        },
                        {
                            "type": "quiz",
                            "question": "A 'dev' DMs you with a 'pre-sale' link. What do you do?",
                            "options": [
                                "Click it immediately",
                                "Ask for proof they're the real dev",
                                "Check official channels and ignore DMs",
                                "Send a small amount to test",
                            ],
                            "correct": 2,
                            "explanation": "Real devs never DM first. Always verify through official channels (verified Twitter, official Discord announcements). Pre-sale links in DMs are 99% scams.",
                        },
                        {
                            "type": "drag-drop",
                            "title": "Real vs Fake Announcement",
                            "items": [
                                {
                                    "text": "Posted by verified account with checkmark",
                                    "zone": "real",
                                },
                                {"text": "Urgency: 'Limited time, act NOW!'", "zone": "fake"},
                                {"text": "Links to official domain", "zone": "real"},
                                {"text": "Asks for private key or seed phrase", "zone": "fake"},
                                {"text": "Announced on multiple official channels", "zone": "real"},
                                {"text": "Only shared in DM", "zone": "fake"},
                            ],
                        },
                    ]
                },
            },
            {
                "id": "pump-dump-patterns",
                "title": "Pump & Dump Patterns",
                "type": "interactive",
                "duration": 10,
                "xp": 120,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Artificial Hype",
                            "sub": "How coordinated pumps create bagholders",
                        },
                        {
                            "type": "chart-sim",
                            "title": "Live Chart Simulation",
                            "description": "Watch a token chart in real-time. See the telltale signs: volume spikes with no news, identical buy sizes, wallet clustering, and the inevitable dump.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is the hallmark of a coordinated pump?",
                            "options": [
                                "Steady organic growth",
                                "Sudden volume spike with identical wallet sizes",
                                "Slow price decline",
                                "High liquidity",
                            ],
                            "correct": 1,
                            "explanation": "Coordinated pumps show sudden volume spikes from many wallets of similar size buying at the same time. This is bots or a group acting together.",
                        },
                        {
                            "type": "interactive",
                            "title": "Volume Analysis Tool",
                            "description": "Analyze a token's trading history. Spot the wash trading: buyers and sellers are the same wallets. Real volume vs fake volume visualization.",
                        },
                    ]
                },
            },
            {
                "id": "scam-detection-exam",
                "title": "Final Exam: Scam Detection",
                "type": "exam",
                "duration": 16,
                "xp": 260,
                "passing_score": 85,
                "content": {
                    "slides": [
                        {
                            "type": "exam-intro",
                            "text": "The Scam Hunter Certification",
                            "sub": "20 questions · 85% to pass · Certificate awarded",
                        },
                        {
                            "type": "quiz",
                            "question": "A token has 70% insider allocation. What is the risk?",
                            "options": [
                                "Low risk, insiders are trustworthy",
                                "High risk, insiders can dump on retail",
                                "No risk, allocation doesn't matter",
                                "Medium risk, only if price drops",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What does 'LP not burned' mean?",
                            "options": [
                                "Liquidity is locked forever",
                                "Dev can withdraw liquidity anytime",
                                "Tokens can't be traded",
                                "The contract is verified",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "You see 15 wallets funded by the same address buying simultaneously. This is:",
                            "options": [
                                "Organic demand",
                                "A bundler attack",
                                "Market making",
                                "Staking rewards",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "A contract has a 'blacklist' function. What can the dev do?",
                            "options": [
                                "Block specific wallets from selling",
                                "Increase token supply",
                                "Change the token name",
                                "Burn tokens",
                            ],
                            "correct": 0,
                        },
                        {
                            "type": "quiz",
                            "question": "Which is the SAFEST action when receiving an unsolicited airdrop?",
                            "options": [
                                "Sell it immediately",
                                "Interact with the token's website",
                                "Ignore it and don't interact",
                                "Stake it for rewards",
                            ],
                            "correct": 2,
                        },
                        {
                            "type": "quiz",
                            "question": "What is 'slippage' in a DEX trade?",
                            "options": [
                                "The trading fee",
                                "The difference between expected and actual price",
                                "The gas cost",
                                "The time to confirm",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "A token's website claims 'audited by CertiK' but CertiK has no record. This is:",
                            "options": [
                                "A minor mistake",
                                "A fake audit claim",
                                "An old audit",
                                "A private audit",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What is the #1 rule of crypto safety?",
                            "options": [
                                "Always follow influencers",
                                "Never invest more than you can afford to lose",
                                "Buy the dip",
                                "Hold forever",
                            ],
                            "correct": 1,
                        },
                    ]
                },
            },
        ],
    },
    {
        "id": "advanced-forensics",
        "title": "Advanced On-Chain Forensics",
        "subtitle": "Think like a blockchain detective",
        "description": "Deep technical analysis. Trace transactions across chains, decode contract interactions, and build cases with real evidence.",
        "difficulty": "advanced",
        "duration_minutes": 120,
        "lessons_count": 8,
        "xp_reward": 2000,
        "badge_reward": "forensic-analyst",
        "color": "#f59e0b",
        "icon": "microscope",
        "modules": [
            {
                "id": "tracing-transactions",
                "title": "Tracing Transactions",
                "type": "interactive",
                "duration": 15,
                "xp": 250,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Follow the Money",
                            "sub": "Every transaction leaves a trail",
                        },
                        {
                            "type": "trace-sim",
                            "title": "Transaction Tracer",
                            "description": "Start with a suspicious wallet. Trace its funding source through 5 hops. Discover it was funded by a known scammer wallet from a previous rug pull.",
                        },
                        {
                            "type": "quiz",
                            "question": "What tool shows all transactions for a specific wallet?",
                            "options": [
                                "DEX Screener",
                                "Etherscan / Solscan",
                                "CoinMarketCap",
                                "Twitter",
                            ],
                            "correct": 1,
                            "explanation": "Block explorers like Etherscan (Ethereum) and Solscan (Solana) show every transaction, token transfer, and contract interaction for any wallet.",
                        },
                        {
                            "type": "interactive",
                            "title": "Multi-Chain Trace",
                            "description": "A scammer bridges stolen funds from Ethereum to Base to Solana. Follow the trail across chains using bridge transaction hashes.",
                        },
                    ]
                },
            },
            {
                "id": "contract-forensics",
                "title": "Contract Forensics",
                "type": "interactive",
                "duration": 20,
                "xp": 300,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Decode the Contract",
                            "sub": "Reading bytecode like a detective reads fingerprints",
                        },
                        {
                            "type": "bytecode-sim",
                            "title": "Bytecode Analysis",
                            "description": "Even unverified contracts can be analyzed. Use decompilers to find function signatures, storage patterns, and suspicious logic in raw bytecode.",
                        },
                        {
                            "type": "quiz",
                            "question": "What does 'renounced ownership' mean?",
                            "options": [
                                "The dev sold their tokens",
                                "No one can change the contract anymore",
                                "The contract was deleted",
                                "The token is worthless",
                            ],
                            "correct": 1,
                            "explanation": "Renounced ownership means the deployer sent ownership to the zero address (0x000...). They can no longer mint tokens, change fees, or pause trading.",
                        },
                        {
                            "type": "interactive",
                            "title": "Function Signature Decoder",
                            "description": "Given a transaction's input data (0x23b872dd...), decode it to 'transferFrom(address,address,uint256)'. Learn to read raw transaction data.",
                        },
                    ]
                },
            },
            {
                "id": "advanced-exam",
                "title": "Master Forensics Exam",
                "type": "exam",
                "duration": 20,
                "xp": 450,
                "passing_score": 90,
                "content": {
                    "slides": [
                        {
                            "type": "exam-intro",
                            "text": "Forensic Analyst Certification",
                            "sub": "15 questions · 90% to pass · Elite badge awarded",
                        },
                        {
                            "type": "quiz",
                            "question": "How can you detect wash trading?",
                            "options": [
                                "High market cap",
                                "Same wallets buying and selling to each other",
                                "Low gas fees",
                                "Verified contract",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What is a 'dust attack'?",
                            "options": [
                                "Sending tiny amounts to track wallets",
                                "A type of DDoS",
                                "Mining with low power",
                                "A phishing technique",
                            ],
                            "correct": 0,
                        },
                        {
                            "type": "quiz",
                            "question": "Which bridge transaction field is MOST important for tracing?",
                            "options": [
                                "Token symbol",
                                "Transaction hash",
                                "Wallet balance",
                                "Gas price",
                            ],
                            "correct": 1,
                        },
                    ]
                },
            },
        ],
    },
]

# ── Gamification Config ──

LEVELS = [
    {"level": 1, "title": "Novice", "xp_required": 0, "color": "#9ca3af"},
    {"level": 2, "title": "Learner", "xp_required": 300, "color": "#6366f1"},
    {"level": 3, "title": "Analyst", "xp_required": 800, "color": "#8b5cf6"},
    {"level": 4, "title": "Detective", "xp_required": 1500, "color": "#ec4899"},
    {"level": 5, "title": "Hunter", "xp_required": 2500, "color": "#f59e0b"},
    {"level": 6, "title": "Expert", "xp_required": 4000, "color": "#ef4444"},
    {"level": 7, "title": "Master", "xp_required": 6000, "color": "#10b981"},
    {"level": 8, "title": "Legend", "xp_required": 9000, "color": "#3b82f6"},
    {"level": 9, "title": "Oracle", "xp_required": 13000, "color": "#fbbf24"},
    {"level": 10, "title": "Sentinel", "xp_required": 18000, "color": "#dc2626"},
]

BADGES = {
    "crypto-cadet": {
        "name": "Crypto Cadet",
        "description": "Completed Crypto Basics",
        "icon": "shield",
        "color": "#6366f1",
        "rarity": "common",
    },
    "scam-hunter": {
        "name": "Scam Hunter",
        "description": "Completed Scam Detection Masterclass",
        "icon": "crosshair",
        "color": "#ef4444",
        "rarity": "rare",
    },
    "forensic-analyst": {
        "name": "Forensic Analyst",
        "description": "Completed Advanced Forensics",
        "icon": "microscope",
        "color": "#f59e0b",
        "rarity": "epic",
    },
    "perfect-score": {
        "name": "Perfect Score",
        "description": "100% on any exam",
        "icon": "star",
        "color": "#fbbf24",
        "rarity": "legendary",
    },
    "speed-learner": {
        "name": "Speed Learner",
        "description": "Completed 3 lessons in one day",
        "icon": "zap",
        "color": "#10b981",
        "rarity": "rare",
    },
    "streak-master": {
        "name": "Streak Master",
        "description": "7-day learning streak",
        "icon": "flame",
        "color": "#f97316",
        "rarity": "epic",
    },
    "community-guardian": {
        "name": "Community Guardian",
        "description": "Reported 5 verified scams",
        "icon": "users",
        "color": "#3b82f6",
        "rarity": "legendary",
    },
    "first-blood": {
        "name": "First Blood",
        "description": "First lesson completed",
        "icon": "target",
        "color": "#ec4899",
        "rarity": "common",
    },
}

ACHIEVEMENTS = [
    {
        "id": "first-lesson",
        "name": "First Steps",
        "description": "Complete your first lesson",
        "xp": 50,
        "icon": "footprints",
    },
    {
        "id": "first-course",
        "name": "Course Complete",
        "description": "Complete your first course",
        "xp": 200,
        "icon": "graduation-cap",
    },
    {
        "id": "exam-pass",
        "name": "Exam Passed",
        "description": "Pass any exam with 80%+",
        "xp": 150,
        "icon": "check-circle",
    },
    {
        "id": "perfect-exam",
        "name": "Flawless",
        "description": "Get 100% on an exam",
        "xp": 500,
        "icon": "crown",
    },
    {
        "id": "streak-3",
        "name": "On Fire",
        "description": "3-day learning streak",
        "xp": 100,
        "icon": "flame",
    },
    {
        "id": "streak-7",
        "name": "Unstoppable",
        "description": "7-day learning streak",
        "xp": 300,
        "icon": "fire",
    },
    {
        "id": "streak-30",
        "name": "Dedicated",
        "description": "30-day learning streak",
        "xp": 1000,
        "icon": "award",
    },
    {
        "id": "all-courses",
        "name": "Scholar",
        "description": "Complete all courses",
        "xp": 2000,
        "icon": "book-open",
    },
    {
        "id": "help-others",
        "name": "Mentor",
        "description": "Help 3 other learners in community",
        "xp": 200,
        "icon": "heart",
    },
    {
        "id": "report-scam",
        "name": "Whistleblower",
        "description": "Report a verified scam",
        "xp": 300,
        "icon": "megaphone",
    },
]

# ── Models ──


class LessonProgress(BaseModel):
    lesson_id: str
    course_id: str
    completed: bool = False
    score: int | None = None
    xp_earned: int = 0
    time_spent_seconds: int = 0
    completed_at: str | None = None
    attempts: int = 0


class CourseProgress(BaseModel):
    course_id: str
    completed_lessons: list[str] = []
    total_lessons: int
    exam_score: int | None = None
    exam_passed: bool = False
    started_at: str
    completed_at: str | None = None


class UserProgress(BaseModel):
    user_id: str
    total_xp: int = 0
    level: int = 1
    level_title: str = "Novice"
    streak_days: int = 0
    longest_streak: int = 0
    last_activity: str | None = None
    courses_progress: dict[str, CourseProgress] = {}
    lessons_progress: dict[str, LessonProgress] = {}
    badges: list[str] = []
    achievements: list[str] = []
    certificates: list[dict] = []
    time_spent_total_minutes: int = 0


class QuizAnswer(BaseModel):
    question_index: int
    answer: int


class ExamSubmit(BaseModel):
    answers: list[QuizAnswer]
    time_spent_seconds: int = 0


class LessonComplete(BaseModel):
    lesson_id: str
    course_id: str
    score: int | None = None
    time_spent_seconds: int = 0
    answers: list[QuizAnswer] | None = None


# ── Helpers ──


def _get_progress(user_id: str) -> UserProgress:
    """Get or create user progress."""
    r = get_redis()
    data = r.hget("rmi:scam_school:progress", user_id)
    if data:
        d = json.loads(data)
        return UserProgress(**d)
    return UserProgress(user_id=user_id, started_at=datetime.now(UTC).isoformat())


def _save_progress(progress: UserProgress) -> None:
    """Save user progress to Redis."""
    r = get_redis()
    r.hset("rmi:scam_school:progress", progress.user_id, progress.model_dump_json())


def _calculate_level(xp: int) -> dict:
    """Calculate level from XP."""
    current_level = LEVELS[0]
    next_level = LEVELS[1] if len(LEVELS) > 1 else None
    for i, level in enumerate(LEVELS):
        if xp >= level["xp_required"]:
            current_level = level
            next_level = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    progress_pct = 0
    if next_level:
        range_size = next_level["xp_required"] - current_level["xp_required"]
        progress_in_range = xp - current_level["xp_required"]
        progress_pct = (
            min(100, int((progress_in_range / range_size) * 100)) if range_size > 0 else 100
        )
    return {
        "level": current_level["level"],
        "title": current_level["title"],
        "color": current_level["color"],
        "xp": xp,
        "xp_to_next": next_level["xp_required"] - xp if next_level else 0,
        "next_level_title": next_level["title"] if next_level else None,
        "progress_pct": progress_pct,
    }


def _award_xp(progress: UserProgress, amount: int, source: str) -> dict:
    """Award XP and check for level ups."""
    old_level = progress.level
    progress.total_xp += amount
    new_level_data = _calculate_level(progress.total_xp)
    progress.level = new_level_data["level"]
    progress.level_title = new_level_data["title"]

    leveled_up = progress.level > old_level
    return {
        "awarded": amount,
        "source": source,
        "new_total": progress.total_xp,
        "leveled_up": leveled_up,
        "old_level": old_level,
        "new_level": progress.level,
        "level_title": progress.level_title,
    }


def _award_badge(progress: UserProgress, badge_id: str) -> dict | None:
    """Award a badge if not already owned."""
    if badge_id in progress.badges:
        return None
    if badge_id not in BADGES:
        return None
    progress.badges.append(badge_id)
    badge = BADGES[badge_id]
    return {
        "badge_id": badge_id,
        "name": badge["name"],
        "description": badge["description"],
        "icon": badge["icon"],
        "color": badge["color"],
        "rarity": badge["rarity"],
    }


def _check_achievements(progress: UserProgress) -> list[dict]:
    """Check and award new achievements."""
    new_achievements = []

    # First lesson
    if "first-lesson" not in progress.achievements and len(progress.lessons_progress) > 0:
        if any(line.completed for line in progress.lessons_progress.values()):
            progress.achievements.append("first-lesson")
            new_achievements.append({"id": "first-lesson", "name": "First Steps", "xp": 50})

    # First course
    if "first-course" not in progress.achievements:
        completed_courses = sum(1 for c in progress.courses_progress.values() if c.completed_at)
        if completed_courses >= 1:
            progress.achievements.append("first-course")
            new_achievements.append({"id": "first-course", "name": "Course Complete", "xp": 200})

    # All courses
    if "all-courses" not in progress.achievements:
        if len(progress.courses_progress) >= len(COURSES):
            all_done = all(c.completed_at for c in progress.courses_progress.values())
            if all_done:
                progress.achievements.append("all-courses")
                new_achievements.append({"id": "all-courses", "name": "Scholar", "xp": 2000})

    # Streak achievements
    if "streak-3" not in progress.achievements and progress.streak_days >= 3:
        progress.achievements.append("streak-3")
        new_achievements.append({"id": "streak-3", "name": "On Fire", "xp": 100})
    if "streak-7" not in progress.achievements and progress.streak_days >= 7:
        progress.achievements.append("streak-7")
        new_achievements.append({"id": "streak-7", "name": "Unstoppable", "xp": 300})
        _award_badge(progress, "streak-master")
    if "streak-30" not in progress.achievements and progress.streak_days >= 30:
        progress.achievements.append("streak-30")
        new_achievements.append({"id": "streak-30", "name": "Dedicated", "xp": 1000})

    return new_achievements


def _update_streak(progress: UserProgress) -> None:
    """Update learning streak."""
    now = datetime.now(UTC)
    if progress.last_activity:
        last = datetime.fromisoformat(progress.last_activity)
        days_diff = (now - last).days
        if days_diff == 0:
            return  # Already active today
        elif days_diff == 1:
            progress.streak_days += 1
        else:
            progress.streak_days = 1  # Reset
        progress.longest_streak = max(progress.longest_streak, progress.streak_days)
    else:
        progress.streak_days = 1
    progress.last_activity = now.isoformat()


def _generate_certificate(user_id: str, course_id: str, score: int) -> dict:
    """Generate a certificate."""
    cert_id = f"cert-{course_id}-{user_id[:8]}-{secrets.token_hex(4)}"
    course = next((c for c in COURSES if c["id"] == course_id), None)
    return {
        "id": cert_id,
        "course_id": course_id,
        "course_title": course["title"] if course else "Unknown Course",
        "user_id": user_id,
        "score": score,
        "issued_at": datetime.now(UTC).isoformat(),
        "verify_url": f"{API_BASE}/verify-cert/{cert_id}",
        "hash": hashlib.sha256(f"{cert_id}:{user_id}:{course_id}".encode()).hexdigest()[:16],
    }


# ── Endpoints ──


@router.get("/scam-school/courses")
async def list_courses() -> dict[str, list[dict]]:
    """List all available courses with metadata."""
    return {
        "courses": [
            {
                "id": c["id"],
                "title": c["title"],
                "subtitle": c["subtitle"],
                "description": c["description"],
                "difficulty": c["difficulty"],
                "duration_minutes": c["duration_minutes"],
                "lessons_count": c["lessons_count"],
                "xp_reward": c["xp_reward"],
                "badge_reward": c["badge_reward"],
                "color": c["color"],
                "icon": c["icon"],
                "modules": [
                    {
                        "id": m["id"],
                        "title": m["title"],
                        "type": m["type"],
                        "duration": m["duration"],
                        "xp": m["xp"],
                    }
                    for m in c["modules"]
                ],
            }
            for c in COURSES
        ]
    }


@router.get("/scam-school/courses/{course_id}")
async def get_course(course_id: str) -> Any:
    """Get full course content including all lesson slides."""
    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@router.get("/scam-school/courses/{course_id}/modules/{module_id}")
async def get_module(course_id: str, module_id: str) -> Any:
    """Get a specific module's content."""
    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    module = next((m for m in course["modules"] if m["id"] == module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module


@router.get("/scam-school/progress")
async def get_progress(request: Request) -> dict:
    """Get current user's full progress."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    progress = _get_progress(user["id"])
    level_data = _calculate_level(progress.total_xp)

    return {
        "user_id": progress.user_id,
        "xp": progress.total_xp,
        "level": level_data,
        "streak": {
            "current": progress.streak_days,
            "longest": progress.longest_streak,
        },
        "badges": [BADGES[b] for b in progress.badges if b in BADGES],
        "achievements": [a for a in ACHIEVEMENTS if a["id"] in progress.achievements],
        "certificates": progress.certificates,
        "courses_progress": progress.courses_progress,
        "time_spent_total_minutes": progress.time_spent_total_minutes,
    }


@router.post("/scam-school/lessons/{course_id}/{lesson_id}/complete")
async def complete_lesson(course_id: str, lesson_id: str, req: LessonComplete, request: Request) -> dict[str, str]:
    """Mark a lesson as complete and award XP."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    module = next((m for m in course["modules"] if m["id"] == lesson_id), None)
    if not module:
        raise HTTPException(status_code=404, detail="Lesson not found")

    progress = _get_progress(user["id"])
    _update_streak(progress)

    # Calculate score from quiz answers if provided
    score = req.score or 0
    if req.answers and "content" in module and "slides" in module["content"]:
        quiz_slides = [s for s in module["content"]["slides"] if s.get("type") == "quiz"]
        correct = 0
        for ans in req.answers:
            if ans.question_index < len(quiz_slides):
                quiz = quiz_slides[ans.question_index]
                if quiz.get("correct") == ans.answer:
                    correct += 1
        if quiz_slides:
            score = int((correct / len(quiz_slides)) * 100)

    # Award XP based on score
    base_xp = module.get("xp", 50)
    xp_earned = int(base_xp * (score / 100)) if score > 0 else base_xp

    # Update lesson progress
    lesson_key = f"{course_id}:{lesson_id}"
    progress.lessons_progress[lesson_key] = LessonProgress(
        lesson_id=lesson_id,
        course_id=course_id,
        completed=True,
        score=score,
        xp_earned=xp_earned,
        time_spent_seconds=req.time_spent_seconds,
        completed_at=datetime.now(UTC).isoformat(),
        attempts=1,
    )

    # Update course progress
    if course_id not in progress.courses_progress:
        progress.courses_progress[course_id] = CourseProgress(
            course_id=course_id,
            total_lessons=len(course["modules"]),
            started_at=datetime.now(UTC).isoformat(),
        )

    course_prog = progress.courses_progress[course_id]
    if lesson_id not in course_prog.completed_lessons:
        course_prog.completed_lessons.append(lesson_id)

    # Check if course complete
    if len(course_prog.completed_lessons) >= course_prog.total_lessons:
        course_prog.completed_at = datetime.now(UTC).isoformat()
        # Award course badge
        badge = _award_badge(progress, course.get("badge_reward", ""))
        # Award course XP bonus
        bonus_xp = course.get("xp_reward", 0)
        xp_result = _award_xp(progress, bonus_xp, f"Course complete: {course['title']}")
    else:
        xp_result = _award_xp(progress, xp_earned, f"Lesson complete: {module['title']}")
        badge = None

    progress.time_spent_total_minutes += req.time_spent_seconds // 60

    # Check achievements
    new_achievements = _check_achievements(progress)
    for ach in new_achievements:
        _award_xp(progress, ach["xp"], f"Achievement: {ach['name']}")

    _save_progress(progress)

    return {
        "status": "ok",
        "xp_earned": xp_earned,
        "xp_result": xp_result,
        "score": score,
        "badge_awarded": badge,
        "achievements": new_achievements,
        "level": _calculate_level(progress.total_xp),
        "streak": progress.streak_days,
    }


@router.post("/scam-school/exams/{course_id}/{exam_id}")
async def submit_exam(course_id: str, exam_id: str, req: ExamSubmit, request: Request) -> dict:
    """Submit an exam and get results."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    exam = next((m for m in course["modules"] if m["id"] == exam_id and m["type"] == "exam"), None)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    progress = _get_progress(user["id"])

    # Grade exam
    slides = exam.get("content", {}).get("slides", [])
    quiz_slides = [s for s in slides if s.get("type") == "quiz"]
    correct = 0
    results = []

    for i, ans in enumerate(req.answers):
        if i < len(quiz_slides):
            quiz = quiz_slides[i]
            is_correct = quiz.get("correct") == ans.answer
            if is_correct:
                correct += 1
            results.append(
                {
                    "question_index": i,
                    "correct": is_correct,
                    "correct_answer": quiz.get("correct"),
                    "your_answer": ans.answer,
                    "explanation": quiz.get("explanation", ""),
                }
            )

    total = len(quiz_slides)
    score = int((correct / total) * 100) if total > 0 else 0
    passing = exam.get("passing_score", 80)
    passed = score >= passing

    # Update exam progress
    exam_key = f"{course_id}:{exam_id}"
    progress.lessons_progress[exam_key] = LessonProgress(
        lesson_id=exam_id,
        course_id=course_id,
        completed=passed,
        score=score,
        xp_earned=0,
        time_spent_seconds=req.time_spent_seconds,
        completed_at=datetime.now(UTC).isoformat() if passed else None,
        attempts=1,
    )

    if course_id not in progress.courses_progress:
        progress.courses_progress[course_id] = CourseProgress(
            course_id=course_id,
            total_lessons=len(course["modules"]),
            started_at=datetime.now(UTC).isoformat(),
        )

    course_prog = progress.courses_progress[course_id]
    course_prog.exam_score = score
    course_prog.exam_passed = passed

    xp_earned = 0
    badge = None
    certificate = None
    new_achievements = []

    if passed:
        # Award exam XP
        base_xp = exam.get("xp", 100)
        xp_earned = base_xp
        if score == 100:
            xp_earned = int(base_xp * 1.5)  # Bonus for perfect score
            badge = _award_badge(progress, "perfect-score")
            new_achievements.append({"id": "perfect-exam", "name": "Flawless", "xp": 500})

        xp_result = _award_xp(progress, xp_earned, f"Exam passed: {exam['title']}")

        # Award course badge on exam pass
        if not course_prog.completed_at:
            course_prog.completed_at = datetime.now(UTC).isoformat()
            badge_course = _award_badge(progress, course.get("badge_reward", ""))
            if badge_course:
                badge = badge_course
            # Generate certificate
            certificate = _generate_certificate(user["id"], course_id, score)
            progress.certificates.append(certificate)
    else:
        xp_result = None

    # Check achievements
    ach = _check_achievements(progress)
    for a in ach:
        _award_xp(progress, a["xp"], f"Achievement: {a['name']}")
    new_achievements.extend(ach)

    _save_progress(progress)

    return {
        "passed": passed,
        "score": score,
        "passing_score": passing,
        "correct": correct,
        "total": total,
        "results": results,
        "xp_earned": xp_earned,
        "xp_result": xp_result,
        "badge_awarded": badge,
        "certificate": certificate,
        "achievements": new_achievements,
        "level": _calculate_level(progress.total_xp),
    }


@router.get("/scam-school/leaderboard")
async def get_leaderboard(limit: int = Query(50, ge=1, le=100)) -> dict:
    """Get global leaderboard by XP."""
    r = get_redis()
    all_progress = r.hgetall("rmi:scam_school:progress")

    entries = []
    for user_id, data in all_progress.items():
        try:
            d = json.loads(data)
            entries.append(
                {
                    "user_id": user_id,
                    "xp": d.get("total_xp", 0),
                    "level": d.get("level", 1),
                    "level_title": d.get("level_title", "Novice"),
                    "badges_count": len(d.get("badges", [])),
                    "courses_completed": sum(
                        1 for c in d.get("courses_progress", {}).values() if c.get("completed_at")
                    ),
                    "streak": d.get("streak_days", 0),
                }
            )
        except Exception:
            continue

    entries.sort(key=lambda x: x["xp"], reverse=True)

    # Add rank
    for i, e in enumerate(entries[:limit]):
        e["rank"] = i + 1

    return {"leaderboard": entries[:limit], "total_learners": len(entries)}


@router.get("/scam-school/badges")
async def list_badges() -> dict:
    """List all available badges."""
    return {"badges": BADGES}


@router.get("/scam-school/achievements")
async def list_achievements() -> dict:
    """List all achievements."""
    return {"achievements": ACHIEVEMENTS}


@router.get("/scam-school/levels")
async def list_levels() -> dict:
    """List level progression."""
    return {"levels": LEVELS}


@router.get("/scam-school/verify-cert/{cert_id}")
async def verify_certificate(cert_id: str) -> dict[str, bool]:
    """Verify a certificate's authenticity."""
    r = get_redis()
    all_progress = r.hgetall("rmi:scam_school:progress")

    for user_id, data in all_progress.items():
        try:
            d = json.loads(data)
            for cert in d.get("certificates", []):
                if cert["id"] == cert_id:
                    return {
                        "valid": True,
                        "certificate": cert,
                        "user_id": user_id,
                        "verified_at": datetime.now(UTC).isoformat(),
                    }
        except Exception:
            continue

    return {"valid": False, "error": "Certificate not found"}
