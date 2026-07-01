#!/usr/bin/env python3
"""Check titles of downloaded PDFs."""

import os

import fitz

papers_dir = "/root/backend/data/papers"
for pid in ["2208.00613", "2106.09489", "2204.09632"]:
    path = os.path.join(papers_dir, f"{pid}.pdf")
    try:
        doc = fitz.open(path)
        text = doc[0].get_text()[:500]
        print(f"\n=== {pid} ===")
        for line in text.split("\n")[:10]:
            if line.strip():
                print(f"  {line.strip()}")
        doc.close()
    except Exception as e:
        print(f"  ERROR: {e}")
