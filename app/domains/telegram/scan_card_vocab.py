"""Severity words and check-code reasons for the scan card.

The vocabulary lives apart from both the extraction layer and the renderer so
neither has to import the other (and so a new check code is added in one place).
"""

from __future__ import annotations

# Emoji-free severity word: the word carries the meaning, the layout carries the
# emphasis. Single source of truth for the header and the action line.
SEVERITY = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}

ACTION = {
    "CRITICAL": "Do not trade this token.",
    "HIGH": "Treat as hostile until a human reads the full report.",
    "MEDIUM": "Unproven, not disproven — size accordingly.",
    "LOW": "No disqualifying signal in the data we hold.",
}

# Check code -> reason a reader can act on. A code missing here falls back to the
# scanner's own ``details``, so a new check renders without a code change.
REASONS = {
    "CR01": "Contract source is not verified",
    "CR02": "Contract is upgradeable (proxy)",
    "CR03": "Mint function is callable",
    "HP01": "Honeypot simulation",
    "HP04": "Transfers can be paused",
    "TE04": "Addresses can be blacklisted",
    "HD03": "Holder count",
    "LR03": "Anti-whale limits",
    "RP05": "Token age under 1 hour",
}
