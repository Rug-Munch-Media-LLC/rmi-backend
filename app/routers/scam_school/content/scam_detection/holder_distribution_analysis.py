"""holder-distribution-analysis module."""
from __future__ import annotations

holder_distribution_analysis = {
    "title": 'Analyzing Token Holder Distribution',
    "type": 'interactive',
    "duration": 15,
    "xp": 200,
    "content": {'slides': [{'type': 'title', 'text': 'Who Really Owns This Token?', 'sub': 'Holder analysis reveals the truth behind any token'}, {'type': 'interactive', 'title': 'Holder Distribution Visualizer', 'description': "Analyze three tokens' holder distributions side-by-side. Token A: natural distribution with no single holder above 5%. Token B: Top 10 wallets hold 92%. Token C: 40 wallets all funded by the same address. Which would you invest in?"}, {'type': 'quiz', 'question': 'Why is a high concentration of tokens in the top 10 wallets dangerous?', 'options': ['They might stake the tokens', 'A single coordinated sell-off could crash the price instantly', 'They pay more gas', 'It looks bad on Etherscan'], 'correct': 1, 'explanation': 'If the top 10 holders control 80%+ of the supply, any one of them can trigger a cascade sell-off. Combined with bundler patterns, this signals a coordinated insider group ready to dump.'}, {'type': 'interactive', 'title': 'Wallet Clustering: Find Hidden Connections', 'description': 'Given 20 wallet addresses, trace their funding sources. 15 were funded by the same master wallet 2 hours before launch. These are connected insiders, not organic buyers. Learn to spot this pattern.'}, {'type': 'quiz', 'question': "What is a 'bundler' pattern in token launches?", 'options': ['A group of friends buying together publicly', 'Multiple wallets funded from one source, buying simultaneously to fake demand', 'People using the same exchange', 'Wallets with similar names'], 'correct': 1, 'explanation': 'Bundlers create dozens of wallets, fund them from a single source, and program them to buy at launch simultaneously. This creates artificial demand that tricks real buyers into FOMO, then the bundlers dump.'}]},
}

__all__ = ["holder_distribution_analysis"]
