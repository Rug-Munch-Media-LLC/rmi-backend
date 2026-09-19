"""RAG helper utilities consumed by the backend.

These modules were relocated out of ``app.rag`` (Track 1 RAG cutaway):
rugbrain owns the RAG engine, while the backend keeps only the thin
helpers it needs to talk to it (embeddings, rerank, scan intel, ratings).

Import from here, not ``app.rag``:

    from app.integrations.rag_helpers.embeddings import get_embedding
    from app.integrations.rag_helpers.rerank import rerank_results
    from app.integrations.rag_helpers.scan_intel import embed_scan_result_bg
    from app.integrations.rag_helpers.ratings import rate_article
"""

from __future__ import annotations

__all__: list[str] = []
