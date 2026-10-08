"""mev-sandwich-attack-forensics module."""

from __future__ import annotations

mev_sandwich_attack_forensics = {
    "title": "MEV & Sandwich Attack Forensics",
    "type": "interactive",
    "duration": 20,
    "xp": 300,
    "content": {
        "slides": [
            {
                "type": "title",
                "text": "The Invisible Tax",
                "sub": "Analyzing MEV extraction and identifying the extractors",
            },
            {
                "type": "interactive",
                "title": "Mempool Analysis Lab",
                "description": "Watch the mempool in real-time (simulated). A user submits a swap. Within milliseconds, a bot front-runs it, the user's trade executes at a worse price, and the bot back-runs for profit. See the exact P&L of each party.",
            },
            {
                "type": "quiz",
                "question": "How can you identify a sandwich attack in transaction history?",
                "options": [
                    "The transactions are in different blocks",
                    "Three transactions in the same block: buy (bot), buy (victim), sell (bot) — with the bot profiting",
                    "The gas is higher than normal",
                    "The token price goes up",
                ],
                "correct": 1,
                "explanation": "A sandwich attack appears as three transactions in quick succession: bot buys (driving price up), victim buys (at inflated price), bot sells (at profit). The victim effectively paid the bot's profit in slippage.",
            },
            {
                "type": "interactive",
                "title": "Builder & Searcher Identification",
                "description": "Analyze MEV-boost relay data to identify known searcher addresses, builder patterns, and the relays they use. Learn to associate MEV extraction with specific entities.",
            },
            {
                "type": "quiz",
                "question": "What is the role of a 'searcher' in the MEV supply chain?",
                "options": [
                    "A blockchain validator",
                    "An entity that scans the mempool for profitable opportunities and submits bundles to builders",
                    "A security auditor",
                    "A token creator",
                ],
                "correct": 1,
                "explanation": "Searchers run algorithms to find MEV opportunities (arbitrage, liquidations, sandwiching). They create transaction bundles and submit them to block builders who include them in blocks for a share of the profit.",
            },
        ]
    },
}

__all__ = ["mev_sandwich_attack_forensics"]
