"""safe-trading-practices module."""
from __future__ import annotations

safe_trading_practices = {
    "title": 'Safe Trading Practices',
    "type": 'interactive',
    "duration": 12,
    "xp": 120,
    "content": {'slides': [{'type': 'title', 'text': 'Trade Smart, Not Reckless', 'sub': 'Settings and strategies that protect your capital'}, {'type': 'interactive', 'title': 'Slippage Simulator', 'description': "Adjust slippage from 0.5% to 50%. See how different settings affect your trade. Learn why 0.5-2% is standard and why high slippage = you're getting front-run."}, {'type': 'quiz', 'question': 'Setting your slippage to 49% is dangerous because:', 'options': ['It costs more gas', 'MEV bots can sandwich your trade, extracting maximum value at your expense', 'The transaction will fail', 'It makes the trade slower'], 'correct': 1, 'explanation': 'High slippage tolerance allows MEV bots to profit at your expense through sandwich attacks. The bot buys before you (raising the price), your trade executes at the worst price, then the bot sells after (lowering it back).'}, {'type': 'interactive', 'title': 'DEX Aggregators vs Direct Swaps', 'description': 'Compare a $1000 swap on Uniswap direct vs 1inch vs Jupiter. Aggregators split your trade across multiple pools for better rates and less slippage. Plus built-in MEV protection.'}, {'type': 'quiz', 'question': 'What is the #1 rule of safe crypto trading?', 'options': ['Always follow crypto influencers', 'Never invest more than you can afford to lose, and never FOMO', 'Always buy at the all-time high', 'Trade with 100x leverage for maximum gains'], 'correct': 1, 'explanation': "FOMO (Fear Of Missing Out) is the crypto investor's worst enemy. Set a budget, do your research, and never invest money you can't afford to lose entirely."}]},
}

__all__ = ["safe_trading_practices"]
