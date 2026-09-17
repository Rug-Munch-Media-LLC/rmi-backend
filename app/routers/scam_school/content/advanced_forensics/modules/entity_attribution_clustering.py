"""entity-attribution-clustering module."""
from __future__ import annotations

entity_attribution_clustering = {
    "title": 'Entity Attribution & Clustering',
    "type": 'interactive',
    "duration": 20,
    "xp": 300,
    "image": 'https://commons.wikimedia.org/wiki/Special:FilePath/Fingerprint.svg?width=800',
    "image_alt": 'Fingerprint icon representing entity attribution and wallet clustering',
    "content": {'slides': [{'type': 'title', 'text': 'Who Is Behind the Wallet?', 'sub': 'Linking addresses to known entities and scammer groups'}, {'type': 'interactive', 'title': 'Entity Resolution Lab', 'description': 'Given 100 addresses, use clustering heuristics to identify: (1) exchange deposit addresses (high volume, reuse pattern), (2) mixer addresses (fixed denominations), (3) personal wallets (long-term holding), and (4) scammer hot wallets (rapid turnover).'}, {'type': 'quiz', 'question': 'What heuristic helps identify an exchange deposit address?', 'options': ['It holds tokens for years', 'It receives funds from thousands of unique addresses and quickly moves them to a single consolidation wallet', 'It only interacts with one dApp', 'It has a low transaction count'], 'correct': 1, 'explanation': 'Exchange deposit addresses receive from many unique senders and forward funds to exchange hot wallets or consolidation addresses. The many-to-one pattern with high address reuse is a strong exchange indicator.'}, {'type': 'interactive', 'title': 'Known Scammer Clusters', 'description': 'Explore a database of known scammer clusters: wallets linked to the same entity through shared funding sources, coordinated transaction timing, and behavioral patterns. Learn how to cross-reference clusters across investigations.'}, {'type': 'quiz', 'question': "What is the 'common spend' heuristic in wallet clustering?", 'options': ['Wallets that hold the same token', 'Multiple input addresses used in the same transaction indicate those addresses are controlled by the same entity', 'Wallets with similar balances', 'Wallets created on the same day'], 'correct': 1, 'explanation': 'When a transaction has multiple input addresses, all those addresses must be controlled by the same entity (since signing requires each private key). This is one of the strongest clustering heuristics available.'}]},
}

__all__ = ["entity_attribution_clustering"]
