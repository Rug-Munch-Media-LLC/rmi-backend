#!/usr/bin/env python3
"""Search arxiv for papers by title keywords and return arxiv IDs."""

import sys
import urllib.request
import xml.etree.ElementTree as ET


def search_arxiv(query, max_results=3):
    """Search arxiv and return list of (arxiv_id, title) tuples."""
    q = "+".join(query.split())
    url = f"https://export.arxiv.org/api/query?search_query=ti:{q}&max_results={max_results}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RMI-Research-Bot/1.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = resp.read()
        root = ET.fromstring(data)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("a:entry", ns):
            title = entry.find("a:title", ns).text.strip().replace("\n", " ")
            aid = entry.find("a:id", ns).text.strip()
            # Extract just the arxiv ID from the full URL
            aid = aid.split("/abs/")[-1]
            results.append((aid, title))
        return results
    except Exception as e:
        print(f"ERROR searching for '{query}': {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    queries = [
        "Smart Contract Vulnerability Detection Interpretable Graph Feature Expert Pattern Fusion",
        "Temporal-Amount Snapshot MultiGraph Ethereum Transaction Tracking",
        "Reentrancy Vulnerability Identification Ethereum Smart Contracts",
        "How to Design Tokenomics",
        "Token Vesting Schedules",
        "Empirical Evidence Governance Token Distributions",
    ]
    for q in queries:
        results = search_arxiv(q)
        print(f"\n=== {q} ===")
        for aid, title in results:
            print(f"  {aid} | {title[:120]}")
        if not results:
            print("  NO RESULTS")
