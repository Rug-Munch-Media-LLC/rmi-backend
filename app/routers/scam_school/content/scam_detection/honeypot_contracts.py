"""honeypot-contracts module."""
from __future__ import annotations

honeypot_contracts = {
    "title": 'Deep Dive: Honeypot Contracts',
    "type": 'interactive',
    "duration": 15,
    "xp": 200,
    "image": 'https://commons.wikimedia.org/wiki/Special:FilePath/Warning_icon.svg?width=800',
    "image_alt": 'Warning triangle icon representing the honeypot trap',
    "content": {'slides': [{'type': 'title', 'text': 'The Honeypot Trap', 'sub': 'You can buy, but you can never sell'}, {'type': 'honeypot-sim', 'title': 'Honeypot Simulator', 'description': "A live trading simulation. You 'buy' a token, the price goes up 10x. Try to sell — the button is disabled. Watch how the contract code silently blocks all sell attempts while still accepting buys."}, {'type': 'code-inspection', 'title': 'Spot the Honeypot Code', 'description': 'Two Solidity snippets side-by-side. One is a legitimate transfer function, the other contains a hidden check that reverts on sells. Find the malicious logic in 60 seconds.'}, {'type': 'quiz', 'question': 'What mechanism do most honeypots use to block sells?', 'options': ['They delete the token from your wallet', 'The transfer function checks if the recipient is the DEX pair and reverts silently', 'They raise the gas fee to 100%', 'They change the token name'], 'correct': 1, 'explanation': "Most honeypots add a check in the transfer function: if the recipient is the Uniswap/PancakeSwap pair address (meaning you're trying to sell), the transaction reverts. Meanwhile, buying works fine — trapping your funds."}, {'type': 'interactive', 'title': 'Testing with Small Amounts', 'description': "Before committing significant capital to any new token, send a tiny test buy (e.g., $5) and immediately attempt to sell. If you can't sell, the token is a honeypot and you've only lost $5 instead of $5,000."}]},
}

__all__ = ["honeypot_contracts"]
