#!/usr/bin/env python3
"""Convert and ingest additional arxiv papers into RAG."""

import json
import os
import sys

import pymupdf4llm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
PAPERS_DIR = os.path.dirname(os.path.abspath(__file__)).rstrip("/scripts") + "/data/papers"  # noqa: B005

with open(os.path.join(PAPERS_DIR, "papers_metadata.json")) as f:
    existing = json.load(f)

new_papers = {
    "2109.05940": {
        "title": "Smart Contract Vulnerability Detection: Graph Feature + Expert Pattern Fusion",
        "authors": [],
        "year": 2021,
        "arxiv_id": "2109.05940",
        "attack_types": ["vulnerability", "static-analysis"],
        "collection": "forensic_reports",
        "description": "Improving smart contract vulnerability detection using graph features and expert patterns.",
    },
    "2006.06116": {
        "title": "Temporal-Amount Snapshot MultiGraph for Ethereum Transaction Tracking",
        "authors": [],
        "year": 2020,
        "arxiv_id": "2006.06116",
        "attack_types": ["transaction-graph", "tracking"],
        "collection": "forensic_reports",
        "description": "Multi-graph snapshot approach for tracking Ethereum transactions.",
    },
    "1905.00346": {
        "title": "Reentrancy Vulnerability Identification in Ethereum Smart Contracts",
        "authors": [],
        "year": 2019,
        "arxiv_id": "1905.00346",
        "attack_types": ["reentrancy", "vulnerability"],
        "collection": "forensic_reports",
        "description": "Detection of reentrancy vulnerabilities in Ethereum smart contracts.",
    },
    "2207.01247": {
        "title": "How to Design Tokenomics: Framework from 20+ Projects",
        "authors": [],
        "year": 2022,
        "arxiv_id": "2207.01247",
        "attack_types": ["tokenomics", "framework"],
        "collection": "market_intel",
        "description": "Tokenomics design framework from analyzing 20+ blockchain projects.",
    },
    "2202.05146": {
        "title": "Empirical Evidence from Governance Token Distributions",
        "authors": [],
        "year": 2022,
        "arxiv_id": "2202.05146",
        "attack_types": ["governance", "token-distribution"],
        "collection": "market_intel",
        "description": "Empirical analysis of governance token distributions and DAO implications.",
    },
    "2004.02968": {
        "title": "Ponzi Scheme Detection in Ethereum Transaction Network",
        "authors": [],
        "year": 2020,
        "arxiv_id": "2004.02968",
        "attack_types": ["ponzi", "detection"],
        "collection": "forensic_reports",
        "description": "Graph-based Ponzi scheme detection in Ethereum transactions.",
    },
    "2104.02368": {
        "title": "EtherClue: Digital Investigation of Attacks on Ethereum Smart Contracts",
        "authors": [],
        "year": 2021,
        "arxiv_id": "2104.02368",
        "attack_types": ["forensics", "attack-detection"],
        "collection": "forensic_reports",
        "description": "EtherClue digital forensic investigation of smart contract attacks.",
    },
}

converted = 0
for arxiv_id, info in new_papers.items():
    pdf_path = os.path.join(PAPERS_DIR, f"{arxiv_id}.pdf")
    txt_path = os.path.join(PAPERS_DIR, f"{arxiv_id}.txt")
    if not os.path.exists(pdf_path):
        print(f"SKIP {arxiv_id}: PDF not found")
        continue
    if arxiv_id in existing:
        print(f"SKIP {arxiv_id}: already in metadata")
        continue
    sz = os.path.getsize(pdf_path)
    if sz < 5000:
        print(f"INVALID {arxiv_id}: PDF too small ({sz} bytes)")
        continue
    try:
        text = pymupdf4llm.to_markdown(pdf_path)
        with open(txt_path, "w") as f:
            f.write(text)
        words = len(text.split())
        print(f"OK {arxiv_id}: {words} words")
        existing[arxiv_id] = info
        converted += 1
    except Exception as e:
        print(f"ERROR {arxiv_id}: {e}")

with open(os.path.join(PAPERS_DIR, "papers_metadata.json"), "w") as f:
    json.dump(existing, f, indent=2)
print(f"Converted: {converted}, Total papers: {len(existing)}")
