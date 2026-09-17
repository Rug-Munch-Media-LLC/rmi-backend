"""nft-scam-detection module."""
from __future__ import annotations

nft_scam_detection = {
    "title": 'NFT Scam Detection',
    "type": 'interactive',
    "duration": 14,
    "xp": 180,
    "content": {'slides': [{'type': 'title', 'text': 'The NFT Minefield', 'sub': 'Fake collections, wash trading, and hidden pitfalls'}, {'type': 'interactive', 'title': 'Fake Collection Detector', 'description': 'Compare a real BAYC listing vs a fake clone. Learn to spot: incorrect contract address, slightly different artwork, fake verification badges, copied metadata, and bogus social proof.'}, {'type': 'quiz', 'question': 'What is a common NFT scam where attackers create a collection that looks identical to a famous one?', 'options': ['A phishing scam', 'A copycat/clone collection — using the same artwork with a different contract address', 'A rug pull', 'A smart contract exploit'], 'correct': 1, 'explanation': "Clone collections copy the artwork of popular collections exactly, then list them slightly below floor price. Buyers think they're getting a deal but receive a worthless knockoff. Always verify the contract address."}, {'type': 'interactive', 'title': 'NFT Wash Trading Analysis', 'description': "Analyze an NFT collection's trading history: the same 5 wallets have bought and sold items between each other 200 times. Learn to spot coordinated wash trading designed to inflate volume and floor price."}, {'type': 'quiz', 'question': "What is 'royalty theft' in the NFT space?", 'options': ['Not paying artists', 'Using marketplaces that bypass royalty payments to creators by routing through custom contracts', 'Hacking the blockchain', 'Copying the artwork'], 'correct': 1, 'explanation': 'Some marketplaces and aggregators use custom contracts that bypass the royalty mechanism, meaning creators get nothing when their work is resold. Always check if a marketplace respects creator royalties.'}]},
}

__all__ = ["nft_scam_detection"]
