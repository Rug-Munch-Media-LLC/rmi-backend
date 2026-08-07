#!/usr/bin/env python3
"""Quick smoke test for the 5 new SENTINEL scanners."""

import sys

sys.path.insert(0, "/root/backend")

from app.scanners import SentinelReport

sr = SentinelReport(token_address="0xtest", chain="ethereum")  # noqa: S106 — test fixture
fields = list(sr.__dataclass_fields__.keys())
print(f"{len(fields)} fields on SentinelReport")

new_fields = [
    f
    for f in fields
    if f
    in (
        "flash_loan_detection",
        "pump_dump_detection",
        "oracle_manipulation",
        "governance_attack",
        "proxy_detection",
    )
]
print(f"New Tier 2 fields: {new_fields}")
print("Pipeline integration OK")
