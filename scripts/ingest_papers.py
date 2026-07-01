#!/usr/bin/env python3
"""Ingest research papers from /root/backend/data/papers/ into RAG forensic_reports."""

import hashlib
import json
import os

import httpx

PAPERS_DIR = "/root/backend/data/papers"
METADATA_FILE = f"{PAPERS_DIR}/papers_metadata.json"
RAG_API = "http://localhost:8000/api/v1/rag/ingest"
CHUNK_SIZE = 2500
CHUNK_OVERLAP = 300


def main():
    with open(METADATA_FILE) as f:
        metadata = json.load(f)

    pdfs = sorted([f for f in os.listdir(PAPERS_DIR) if f.endswith(".pdf")])
    print(f"Found {len(pdfs)} PDFs, {len(metadata)} metadata entries")

    ingested = 0
    chunks_total = 0
    errors = 0

    for pdf_file in pdfs:
        arxiv_id = pdf_file.replace(".pdf", "")
        meta = metadata.get(arxiv_id, {})

        # Extract text with pymupdf
        try:
            import fitz

            doc = fitz.open(os.path.join(PAPERS_DIR, pdf_file))
            text = ""
            for page in doc:
                text += page.get_text() + "\n"
            doc.close()
        except Exception as e:
            print(f"  SKIP {arxiv_id}: extract failed: {e}")
            errors += 1
            continue

        if len(text.strip()) < 200:
            print(f"  SKIP {arxiv_id}: too short ({len(text)} chars)")
            errors += 1
            continue

        # Chunk it
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            chunk_text = text[start:end].strip()
            if len(chunk_text) > 100:
                chunks.append(chunk_text)
            start += CHUNK_SIZE - CHUNK_OVERLAP

        # Ingest each chunk
        title = meta.get("title", arxiv_id)
        attack_types = meta.get("attack_types", [])
        authors_list = meta.get("authors", [])
        collection = meta.get("collection", "forensic_reports")

        success = 0
        for i, chunk in enumerate(chunks):
            doc_id = hashlib.sha256(f"{arxiv_id}:chunk:{i}".encode()).hexdigest()[:16]
            payload = {
                "collection": collection,
                "content": chunk,
                "metadata": {
                    "arxiv_id": arxiv_id,
                    "title": title,
                    "authors": ", ".join(authors_list[:3]) if isinstance(authors_list, list) else str(authors_list),
                    "attack_types": ", ".join(attack_types) if isinstance(attack_types, list) else str(attack_types),
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "source": "research_paper",
                    "doc_id": doc_id,
                },
            }
            try:
                resp = httpx.post(RAG_API, json=payload, timeout=30)
                if resp.status_code == 200:
                    success += 1
                else:
                    if errors < 5:
                        print(f"  ERR {arxiv_id} chunk {i}: {resp.status_code} {resp.text[:100]}")
                    errors += 1
            except Exception as e:
                if errors < 5:
                    print(f"  ERR {arxiv_id} chunk {i}: {e}")
                errors += 1

        chunks_total += success
        ingested += 1
        if arxiv_id not in metadata:
            metadata[arxiv_id] = {}
        metadata[arxiv_id]["ingested"] = True
        metadata[arxiv_id]["chunk_count"] = success
        print(f"  {arxiv_id}: {len(chunks)} chunks, {success} ingested — {title[:50]}")

    # Save updated metadata
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nDONE: {ingested} papers, {chunks_total} total chunks, {errors} errors")


if __name__ == "__main__":
    main()
