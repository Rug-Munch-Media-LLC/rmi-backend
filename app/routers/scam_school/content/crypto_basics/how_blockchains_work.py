"""how-blockchains-work module."""

from __future__ import annotations

how_blockchains_work = {
    "title": "How Blockchains Work",
    "type": "interactive",
    "duration": 14,
    "xp": 140,
    "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin_Block_Data.svg?width=800",
    "image_alt": "Diagram of a Bitcoin block: header, transactions, and the link to the previous block hash",
    "content": {
        "slides": [
            {"type": "title", "text": "The Digital Ledger", "sub": "Understanding blockchain technology in 5 minutes"},
            {
                "type": "interactive-block",
                "title": "Build a Chain",
                "description": "Drag blocks to form a blockchain. Each block contains transactions and the hash of the previous block. Change one transaction and watch every subsequent hash break — that's immutability in action.",
            },
            {
                "type": "quiz",
                "question": "What makes a blockchain immutable?",
                "options": [
                    "It uses a database",
                    "Each block hashes the previous block, creating a tamper-evident chain",
                    "It is stored on one computer",
                    "It uses password protection",
                ],
                "correct": 1,
                "explanation": "Each block contains the hash of the previous block. Change one transaction and every subsequent hash changes, making tampering immediately obvious to the network.",
            },
            {
                "type": "interactive",
                "title": "Why 'Code Is Law' Matters for Safety",
                "description": "Smart contracts execute exactly as written — no exceptions, no appeals. A malicious contract will do exactly what its code says. Understanding this is the first step to protecting yourself.",
            },
            {
                "type": "simulation",
                "title": "Hash Visualization",
                "description": "Type any text and see its SHA-256 hash change in real-time. Even one letter difference creates a completely different hash. This is the cryptographic foundation of blockchain security.",
            },
        ]
    },
}

__all__ = ["how_blockchains_work"]
