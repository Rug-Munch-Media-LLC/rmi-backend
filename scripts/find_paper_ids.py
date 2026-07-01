#!/usr/bin/env python3
"""Search arxiv for papers with retry logic."""

import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET


def search_arxiv(query, max_results=5):
    """Search arxiv and return list of (arxiv_id, title) tuples."""
    q = "+AND+".join([f"ti:{w}" for w in query.split()[:4]])
    url = f"https://export.arxiv.org/api/query?search_query={q}&max_results={max_results}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RMI-Research-Bot/1.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        root = ET.fromstring(data)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("a:entry", ns):
            title = entry.find("a:title", ns).text.strip().replace("\n", " ")
            aid = entry.find("a:id", ns).text.strip()
            aid = aid.split("/abs/")[-1]
            results.append((aid, title))
        return results
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return []


# Search for each paper
searches = {
    "smart_contract_vuln": "smart contract vulnerability detection graph feature",
    "temporal_amount": "temporal amount snapshot multigraph ethereum",
    "reentrancy": "reentrancy vulnerability identification ethereum smart contract",
    "tokenomics_design": "tokenomics design token economy",
    "token_vesting": "token vesting cryptocurrency",
    "governance_tokens": "governance token distribution empirical",
}

results = {}
for key, q in searches.items():
    print(f"Searching: {q}", flush=True)
    r = search_arxiv(q)
    results[key] = r
    if r:
        for aid, title in r:
            print(f"  {aid} | {title[:100]}")
    else:
        print("  NO RESULTS")
    time.sleep(5)  # Rate limit

# Save results
with open("/root/backend/data/papers/search_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("\nDone. Results saved.")
