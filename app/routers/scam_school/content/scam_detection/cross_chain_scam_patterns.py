"""cross-chain-scam-patterns module."""
from __future__ import annotations

cross_chain_scam_patterns = {
    "title": 'Cross-Chain Scam Patterns',
    "type": 'interactive',
    "duration": 15,
    "xp": 200,
    "content": {'slides': [{'type': 'title', 'text': 'Bridging to Oblivion', 'sub': 'How cross-chain complexity enables new types of scams'}, {'type': 'interactive', 'title': 'Bridge Exploit Timeline', 'description': 'Interactive timeline of major bridge exploits: Wormhole ($326M), Ronin ($624M), Nomad ($190M), BNB Bridge ($566M). See the common vulnerability patterns: message verification failures, validator compromises, and replay attacks.'}, {'type': 'quiz', 'question': 'What was the root cause of the Wormhole bridge exploit?', 'options': ['A phishing attack on a developer', 'A signature verification bug that allowed the attacker to mint 120K wrapped ETH without depositing real ETH', 'A DDoS on the Solana network', 'A rug pull by the Wormhole team'], 'correct': 1, 'explanation': 'The attacker exploited a missing signature verification check, allowing them to forge a guardian signature and mint 120,000 wrapped ETH on Solana without depositing anything on Ethereum.'}, {'type': 'interactive', 'title': 'Wrapped Token Risks', 'description': 'When you bridge USDC from Ethereum to Solana, you receive a wrapped version. If the bridge is compromised, that wrapped token can become unbacked and worthless. Learn to verify bridge security and assess wrapped asset risk.'}, {'type': 'quiz', 'question': "What is a 'replay attack' in cross-chain contexts?", 'options': ['Playing a video of a transaction', "Reusing a valid transaction from one chain on another chain where it wasn't intended", 'Repeating a trade multiple times', 'Recording a transaction for later analysis'], 'correct': 1, 'explanation': 'Without proper replay protection (chain ID, nonce), a transaction signed for one chain can be replayed on another. This was a critical vulnerability in early cross-chain designs and the Ethereum Classic fork.'}]},
}

__all__ = ["cross_chain_scam_patterns"]
