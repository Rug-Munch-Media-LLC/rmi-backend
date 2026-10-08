"""on-chain-fund-tracing module."""

from __future__ import annotations

on_chain_fund_tracing = {
    "title": "On-Chain Fund Tracing",
    "type": "interactive",
    "duration": 20,
    "xp": 300,
    "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Internet_map_1024.jpg?width=800",
    "image_alt": "Map of internet network links, illustrating fund flows across connected addresses",
    "content": {
        "slides": [
            {"type": "title", "text": "Follow the Money", "sub": "Every transaction leaves a permanent trail"},
            {
                "type": "trace-sim",
                "title": "Interactive Transaction Tracer",
                "description": "Start with a known scammer wallet. Trace the stolen funds through 8 hops: wallet A → DEX swap → wallet B → bridge to another chain → wallet C → split into 50 wallets → consolidate into wallet D → deposit to exchange. Every hop revealed step by step.",
            },
            {
                "type": "quiz",
                "question": "What is the most reliable tool for tracing ETH transactions across multiple hops?",
                "options": [
                    "CoinMarketCap",
                    "Etherscan combined with manual transaction graph analysis",
                    "Twitter",
                    "Discord",
                ],
                "correct": 1,
                "explanation": "Etherscan shows every transaction, internal transaction, and token transfer. By following the 'To' address of each transaction, you can trace funds through arbitrary numbers of hops. Tools like Breadcrumbs and Chainalysis automate this.",
            },
            {
                "type": "interactive",
                "title": "Multi-Chain Trace Exercise",
                "description": "A scammer bridges stolen ETH from Ethereum to Base, swaps to USDC, bridges to Solana, and swaps to SOL. Follow the trail across three chains using bridge transaction hashes and cross-chain explorers.",
            },
            {
                "type": "quiz",
                "question": "What is a 'peel chain' in crypto money laundering?",
                "options": [
                    "A type of blockchain",
                    "Sending funds through many wallets, peeling off small amounts at each step to obscure the trail",
                    "A DeFi protocol",
                    "A mining technique",
                ],
                "correct": 1,
                "explanation": "A peel chain sends large amounts through chains of wallets, 'peeling' off smaller amounts at each step. The small amounts are harder to trace and the large transfers create a complex graph that's expensive to analyze.",
            },
        ]
    },
}

__all__ = ["on_chain_fund_tracing"]
