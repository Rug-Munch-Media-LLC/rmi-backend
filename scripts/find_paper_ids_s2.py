#!/usr/bin/env python3
"""Search Semantic Scholar for paper arxiv IDs with rate limit handling."""

import json
import sys
import time
import urllib.request


def search_s2(query, limit=3, retries=3):
    """Search Semantic Scholar API with retries."""
    q = "+".join(query.split())
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit={limit}&fields=title,externalIds"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RMI-Research-Bot/1.0"})
            resp = urllib.request.urlopen(req, timeout=25)
            data = json.loads(resp.read())
            results = []
            for paper in data.get("data", []):
                title = paper.get("title", "")
                ids = paper.get("externalIds", {})
                arxiv_id = ids.get("ArXiv", "")
                results.append((arxiv_id, title))
            return results
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
            else:
                print(f"  HTTP Error {e}", file=sys.stderr)
                return []
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            return []
    return []


searches = [
    ("smart_vuln", "Smart Contract Vulnerability Detection Graph Feature Expert Pattern"),
    ("temporal_eth", "Temporal-Amount Snapshot MultiGraph Ethereum"),
    ("reentrancy", "Reentrancy Vulnerability Identification Ethereum Smart Contracts"),
    ("tokenomics", "How to Design Tokenomics"),
    ("vesting", "Token Vesting Cryptocurrency Blockchain"),
    ("governance", "Governance Token Distributions Empirical"),
]

found = {}
for key, q in searches:
    print(f"Searching: {q}", flush=True)
    results = search_s2(q)
    if results:
        for arxiv_id, title in results:
            mark = "✓" if arxiv_id else "✗ (no arxiv)"
            print(f"  {mark} {arxiv_id or 'N/A'} | {title[:120]}")
            if arxiv_id:
                found[key] = arxiv_id
                break
    else:
        print("  NO RESULTS")
    time.sleep(5)

print(f"\n=== FOUND {len(found)}/{len(searches)} ===")
for k, v in found.items():
    print(f"  {k}: {v}")

with open("/root/backend/data/papers/found_ids.json", "w") as f:
    json.dump(found, f, indent=2)
