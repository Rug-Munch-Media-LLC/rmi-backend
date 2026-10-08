"""understanding-gas-and-fees module."""

from __future__ import annotations

understanding_gas_and_fees = {
    "title": "Understanding Gas & Transaction Fees",
    "type": "interactive",
    "duration": 12,
    "xp": 120,
    "content": {
        "slides": [
            {
                "type": "title",
                "text": "Why Transactions Cost Money",
                "sub": "Gas: the fuel that powers every blockchain action",
            },
            {
                "type": "gas-simulator",
                "title": "Gas Fee Simulator",
                "description": "Adjust network congestion and transaction complexity. Watch how gas fees change in real-time. Compare Ethereum ($5-50), Base ($0.01-0.10), and Solana ($0.0002) costs.",
            },
            {
                "type": "quiz",
                "question": "What happens if you set gas too low on a pending transaction?",
                "options": [
                    "The transaction is free",
                    "It gets stuck pending indefinitely — miners/validators ignore it",
                    "You get a refund",
                    "It processes faster on the cheaper network",
                ],
                "correct": 1,
                "explanation": "If gas is too low, validators won't include your transaction in any block. It can sit pending for hours or days. You may need to 'cancel' it by sending a 0 ETH transaction with the same nonce and higher gas.",
            },
            {
                "type": "interactive",
                "title": "Failed Transactions: Why They Still Cost Gas",
                "description": "A swap reverts because of insufficient output. You still paid gas. Learn why: the validators still did the computational work of processing (and rejecting) your transaction.",
            },
            {
                "type": "quiz",
                "question": "What is front-running in the context of gas?",
                "options": [
                    "Running nodes at the front of the network",
                    "A bot seeing your pending transaction and submitting the same trade with higher gas",
                    "Buying gas tokens in advance",
                    "The first transaction in a block",
                ],
                "correct": 1,
                "explanation": "Front-runners monitor the mempool for profitable trades. When they see yours, they submit an identical transaction with higher gas to get executed first, profiting at your expense.",
            },
        ]
    },
}

__all__ = ["understanding_gas_and_fees"]
