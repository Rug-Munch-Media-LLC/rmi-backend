"""advanced-smart-contract-auditing module."""
from __future__ import annotations

advanced_smart_contract_auditing = {
    "title": 'Advanced Smart Contract Auditing',
    "type": 'interactive',
    "duration": 22,
    "xp": 350,
    "content": {'slides': [{'type': 'title', 'text': 'Reading Code Like a Pro', 'sub': 'Manual review techniques, fuzzing, and formal verification'}, {'type': 'interactive', 'title': 'Manual Audit Workflow', 'description': 'Walk through a professional audit: (1) Read the spec and documentation, (2) Create a threat model, (3) Line-by-line code review with a checklist, (4) Write Proof of Concept exploits for each finding, (5) Categorize by severity.'}, {'type': 'code-inspection', 'title': 'Find All 5 Vulnerabilities', 'description': 'A deliberately buggy ERC-20 contract contains: reentrancy, unchecked return value, integer overflow (Solidity < 0.8), incorrect access control, and front-running vulnerability. You have 10 minutes to find all 5.'}, {'type': 'quiz', 'question': 'What is fuzz testing in the context of smart contract security?', 'options': ['Testing on random blockchains', 'Automatically generating random/malformed inputs to find edge cases that crash or exploit the contract', 'Testing only the frontend', 'Testing with very slow transactions'], 'correct': 1, 'explanation': "Fuzzing tools like Foundry's invariant tests or Echidna generate millions of random inputs and call sequences, searching for state violations. This catches edge cases that manual review often misses."}, {'type': 'quiz', 'question': "What does 'formal verification' prove about a smart contract?", 'options': ['That the contract has no bugs', 'That specific mathematical properties hold for all possible inputs (not that it has zero bugs)', 'That the contract is profitable', 'That the contract will never be hacked'], 'correct': 1, 'explanation': "Formal verification proves that specified invariants (e.g., 'total supply never exceeds MAX_SUPPLY') hold for all possible states. It does NOT prove the contract does what it's supposed to do or that there are no bugs in unspecified properties."}]},
}

__all__ = ["advanced_smart_contract_auditing"]
