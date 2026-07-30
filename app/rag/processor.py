"""DocumentProcessor — chunk raw text into overlapping segments for embedding."""

from __future__ import annotations

import re

from app.core.logging import get_logger
from app.rag.chunking import content_hash as _content_hash
from app.rag.config import RAG_CHUNK_OVERLAP, RAG_CHUNK_SIZE, RAG_MIN_CHUNK_CHARS, RAG_MIN_DOC_LENGTH
from app.rag.ingestion_models import Chunk, Document
from app.rag.quality import quality_score

log = get_logger(__name__)


class DocumentProcessor:
    """Chunk raw text into overlapping segments for embedding.

    Strategy (matches ``app.rag.chunking.chunk_text``):
      1. Split on paragraph breaks -> sentences -> words.
      2. Greedy-pack into chunks of <= max_chunk chars.
      3. Apply overlap window to preserve cross-chunk context.
      4. Compute quality score per chunk.
    """

    def __init__(
        self,
        max_chunk: int = RAG_CHUNK_SIZE,
        overlap: int = RAG_CHUNK_OVERLAP,
        min_chars: int = RAG_MIN_CHUNK_CHARS,
    ) -> None:
        self.max_chunk = max_chunk
        self.overlap = overlap
        self.min_chars = min_chars

    def chunk_text(self, text: str) -> list[Chunk]:
        """Split text into overlapping chunks.

        Args:
            text: Raw document text.

        Returns:
            List of Chunk objects with quality scores.
        """
        if not text or not text.strip():
            return []

        text = text.strip()
        if len(text) <= self.max_chunk:
            h = _content_hash(text)
            return [Chunk(text=text, content_hash=h, index=0, quality=quality_score(text, self.max_chunk))]

        # Sentence-level split
        sentences = re.split(r"(?<=[.!?\n])\s+", text)
        raw_chunks: list[str] = []
        cur = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if len(s) > self.max_chunk:
                words = s.split()
                sub = ""
                for w in words:
                    if len(sub) + len(w) + 1 > self.max_chunk and sub:
                        raw_chunks.append(sub)
                        sub = w
                    else:
                        sub = (sub + " " + w).strip()
                if sub:
                    raw_chunks.append(sub)
            elif len(cur) + len(s) + 1 > self.max_chunk:
                if cur:
                    raw_chunks.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()

        if cur:
            raw_chunks.append(cur)

        # Apply overlap
        if self.overlap > 0 and len(raw_chunks) > 1:
            overlapped: list[str] = []
            for i, c in enumerate(raw_chunks):
                if i == 0:
                    overlapped.append(c)
                else:
                    tail = raw_chunks[i - 1][-self.overlap :]
                    overlapped.append(tail + " " + c)
            raw_chunks = overlapped

        # Build Chunk objects
        out: list[Chunk] = []
        for i, c in enumerate(raw_chunks):
            if len(c) < self.min_chars:
                continue
            out.append(
                Chunk(
                    text=c,
                    content_hash=_content_hash(c),
                    index=i,
                    quality=quality_score(c, self.max_chunk),
                )
            )
        return out

    def process_document(self, doc: Document) -> list[Chunk]:
        """Convenience: chunk a Document into chunks."""
        if not doc.content or len(doc.content.strip()) < RAG_MIN_DOC_LENGTH:
            log.debug("doc_too_short", doc_id=doc.doc_id, length=len(doc.content))
            return []
        return self.chunk_text(doc.content)
