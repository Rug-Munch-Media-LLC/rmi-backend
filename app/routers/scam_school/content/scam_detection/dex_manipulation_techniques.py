"""dex-manipulation-techniques module."""

from __future__ import annotations

dex_manipulation_techniques = {
    "title": "DEX Manipulation Techniques",
    "type": "interactive",
    "duration": 15,
    "xp": 200,
    "content": {
        "slides": [
            {"type": "title", "text": "The Artificial Market", "sub": "How scammers fake volume, price, and demand"},
            {
                "type": "chart-sim",
                "title": "Wash Trading Visualization",
                "description": "Watch live: Wallet A buys from Wallet B, Wallet B buys from Wallet A, repeat. Volume spikes, chart looks active, but it's the same money going in circles. Learn to distinguish real vs fake volume.",
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
                "explanation": "Wash trading is buying and selling the same asset between wallets you control to create the illusion of market activity. It's illegal in traditional markets but rampant in unregulated crypto.",
            },
            {
                "type": "interactive",
                "title": "Sandwich Attack Simulator",
                "description": "You submit a buy order for 1 ETH of a token. A MEV bot sees your transaction in the mempool, buys before you (raising the price), lets you buy at the inflated price, then sells immediately after (dropping the price). See the profit extraction in real-time.",
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
                "explanation": "Low slippage limits how much bots can profit. DEX aggregators like 1inch and Jupiter have built-in MEV protection. Services like Flashbots allow submitting transactions directly to miners, bypassing the public mempool.",
            },
        ]
    },
}

__all__ = ["dex_manipulation_techniques"]
