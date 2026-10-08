"""wallet-safety-fundamentals module."""

from __future__ import annotations

wallet_safety_fundamentals = {
    "title": "Wallet Safety Fundamentals",
    "type": "interactive",
    "duration": 14,
    "xp": 140,
    "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Padlock.svg?width=800",
    "image_alt": "Padlock icon representing secure wallet and private key storage",
    "content": {
        "slides": [
            {"type": "title", "text": "Your Crypto Wallet", "sub": "Not actually a wallet — it's a keychain"},
            {
                "type": "interactive-keygen",
                "title": "Generate a Key Pair",
                "description": "Watch as a random private key is generated and its corresponding public address is derived. Click to generate a new one. Your private key IS your money.",
            },
            {
                "type": "quiz",
                "question": "What should you NEVER do with your seed phrase?",
                "options": [
                    "Store it in a hardware wallet",
                    "Share it with anyone, screenshot it, or store it online",
                    "Write it on paper and keep in a safe",
                    "Use it to restore your wallet",
                ],
                "correct": 1,
                "explanation": "Your seed phrase is the master key to ALL your funds. Anyone with it controls everything. NEVER share it, screenshot it, store it in cloud storage, or type it into websites.",
            },
            {
                "type": "comparison",
                "left": {
                    "title": "Hot Wallet",
                    "examples": ["MetaMask", "Phantom", "Trust Wallet"],
                    "traits": [
                        "Connected to the internet",
                        "Convenient for daily use",
                        "Vulnerable to malware and phishing",
                        "Keep small amounts only",
                    ],
                },
                "right": {
                    "title": "Cold Wallet",
                    "examples": ["Ledger", "Trezor", "Keystone"],
                    "traits": [
                        "Offline, never connected to internet",
                        "Sign transactions on the device",
                        "Much harder to hack",
                        "Store large amounts here",
                    ],
                },
            },
            {
                "type": "drag-drop",
                "title": "Secure vs Insecure Storage",
                "items": [
                    {"text": "Hardware wallet (Ledger/Trezor)", "zone": "secure"},
                    {"text": "Screenshot saved on phone", "zone": "insecure"},
                    {"text": "Encrypted USB in a safe", "zone": "secure"},
                    {"text": "Email to yourself", "zone": "insecure"},
                    {"text": "Paper backup in fireproof safe", "zone": "secure"},
                    {"text": "Google Docs / cloud storage", "zone": "insecure"},
                ],
            },
        ]
    },
}

__all__ = ["wallet_safety_fundamentals"]
