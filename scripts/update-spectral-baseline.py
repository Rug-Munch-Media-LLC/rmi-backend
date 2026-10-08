#!/usr/bin/env python3
"""Update Spectral baseline from current run."""
import json
import sys

with open('spectral.json') as f:
    data = json.load(f)

errors = sum(1 for r in data if r.get('severity') == 0)
with open('spectral-baseline.txt', 'w') as f:
    f.write(str(errors))
print(f"Spectral error baseline updated to: {errors}")
