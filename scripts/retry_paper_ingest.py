#!/usr/bin/env python3
"""Re-ingest papers that failed (chunk_count == 0)."""

import hashlib
import json
import os

import fitz
import httpx

PAPERS_DIR = "/root/backend/data/papers"
METADATA_FILE = f"{PAPERS_DIR}/papers_metadata.json"
RAG_API = "http://localhost:8000/api/v1/rag/ingest"
CHUNK_SIZE = 2500
CHUNK_OVERLAP = 300


def main():
    with open(METADATA_FILE) as f:
        metadata = json.load(f)

    to_redo = [k for k, v in metadata.items() if not v.get("chunk_count", 0)]
    print(f"Re-ingesting {len(to_redo)} failed papers")

    total_chunks = 0
    errors = 0

    for arxiv_id in to_redo:
        pdf_path = os.path.join(PAPERS_DIR, f"{arxiv_id}.pdf")
        if not os.path.exists(pdf_path):
            print(f"  SKIP {arxiv_id}: no PDF")
            continue
        meta = metadata.get(arxiv_id, {})

        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()

        if len(text.strip()) < 200:
            print(f"  SKIP {arxiv_id}: too short")
            continue

        chunks = []
        start = 0
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            chunk = text[start:end].strip()
            if len(chunk) > 100:
                chunks.append(chunk)
            start += CHUNK_SIZE - CHUNK_OVERLAP

        title = meta.get("title", arxiv_id)
        success = 0
        for i, chunk in enumerate(chunks):
            payload = {
                "collection": meta.get("collection", "forensic_reports"),
                "content": chunk,
                "metadata": {
                    "arxiv_id": arxiv_id,
                    "title": title,
                    "authors": ", ".join(meta.get("authors", [])[:3])
                    if isinstance(meta.get("authors"), list)
                    else str(meta.get("authors", "")),
                    "attack_types": ", ".join(meta.get("attack_types", []))
                    if isinstance(meta.get("attack_types"), list)
                    else str(meta.get("attack_types", "")),
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "source": "research_paper",
                    "doc_id": hashlib.sha256(f"{arxiv_id}:chunk:{i}".encode()).hexdigest()[:16],
                },
            }
            try:
                resp = httpx.post(RAG_API, json=payload, timeout=30)
                if resp.status_code == 200:
                    success += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

        total_chunks += success
        metadata[arxiv_id]["ingested"] = True
        metadata[arxiv_id]["chunk_count"] = success
        print(f"  {arxiv_id}: {success}/{len(chunks)} chunks - {title[:50]}")

    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nDONE: {total_chunks} chunks, {errors} errors")


if __name__ == "__main__":
    main()
