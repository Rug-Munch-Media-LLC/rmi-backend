"""mixer-tumbler-detection module."""
from __future__ import annotations

mixer_tumbler_detection = {
    "title": 'Mixer & Tumbler Detection',
    "type": 'interactive',
    "duration": 20,
    "xp": 300,
    "image": 'https://commons.wikimedia.org/wiki/Special:FilePath/Tor-logo-2011-flat.svg?width=800',
    "image_alt": 'Tor onion logo representing anonymity tools used by mixers and tumblers',
    "content": {'slides': [{'type': 'title', 'text': 'Following Through the Fog', 'sub': 'How mixers work and how to detect their use'}, {'type': 'interactive', 'title': 'Tornado Cash Mechanics', 'description': 'Visual breakdown of how Tornado Cash works: users deposit fixed amounts (0.1, 1, 10, 100 ETH) into a pool. They later withdraw with a zero-knowledge proof, breaking the on-chain link. Learn to identify deposit and withdrawal patterns.'}, {'type': 'quiz', 'question': 'What is the key on-chain signature of Tornado Cash usage?', 'options': ['Random transaction amounts', 'Deposits and withdrawals in fixed denominations (0.1, 1, 10, 100 ETH) to/from known contract addresses', 'Very high gas fees', 'Only during business hours'], 'correct': 1, 'explanation': "Tornado Cash pools accept fixed denominations. So if you see deposits of exactly 1 ETH to a known Tornado address, followed by withdrawals of 1 ETH from different addresses, you're seeing mixer activity."}, {'type': 'interactive', 'title': 'Post-Sanctions Mixer Patterns', 'description': 'Since Tornado Cash sanctions, scammers have shifted to: Wasabi/JoinMarket (CoinJoin for BTC), cross-chain bridges as pseudo-mixers, and privacy chains like Monero. Learn to identify these newer obfuscation techniques.'}, {'type': 'quiz', 'question': 'Why is using a cross-chain bridge an effective pseudo-mixing technique?', 'options': ['Bridges are anonymous', 'Bridging breaks the direct on-chain link — the funds emerge on a different chain with a new address', 'Bridges change your IP address', 'Bridges delete transaction history'], 'correct': 1, 'explanation': 'When you bridge from chain A to chain B, the on-chain trail breaks — the source chain shows funds sent to the bridge contract, and the destination chain shows funds received from the bridge. Correlating the two requires cross-chain analysis.'}]},
}

__all__ = ["mixer_tumbler_detection"]
