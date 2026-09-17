"""proxy-contract-risks module."""
from __future__ import annotations

proxy_contract_risks = {
    "title": 'Proxy Contract Risks',
    "type": 'interactive',
    "duration": 14,
    "xp": 180,
    "content": {'slides': [{'type': 'title', 'text': 'The Upgradeable Trap', 'sub': 'Why proxy contracts can be more dangerous than immutable ones'}, {'type': 'interactive', 'title': 'Proxy Contract Visualizer', 'description': 'See how a proxy contract works: user interacts with proxy → proxy delegates to implementation contract. The admin can swap the implementation at any time, changing all logic without warning. What was a legitimate token yesterday can become a honeypot today.'}, {'type': 'quiz', 'question': 'What is the biggest risk of an upgradeable proxy where a single EOA holds the admin key?', 'options': ['Higher gas costs', 'That single person can change the contract logic to steal all funds instantly', "The contract can't be verified", 'Trading takes longer'], 'correct': 1, 'explanation': 'If one person controls the proxy admin key, they can upgrade the implementation to a malicious contract at any moment. This is why multisig wallets and timelocks are essential for proxy admin control.'}, {'type': 'interactive', 'title': 'Multisig & Timelock Analysis', 'description': 'Compare three proxy setups: (A) Single EOA admin — extremely dangerous. (B) 3-of-5 multisig — better. (C) 3-of-5 multisig + 48-hour timelock — best practice. The timelock gives users time to exit before changes take effect.'}, {'type': 'quiz', 'question': 'How can you check if a contract is a proxy and who controls it?', 'options': ["You can't — proxies are invisible", "Check for implementation slot, look at admin functions, and verify on Etherscan's proxy tab", 'Ask in the Discord', 'Check the token price'], 'correct': 1, 'explanation': "Etherscan shows a 'Proxy' tab for known proxy patterns (EIP-1967, OpenZeppelin, etc.). You can also call admin() or owner() on the contract to see who controls upgrades."}]},
}

__all__ = ["proxy_contract_risks"]
