"""Continuous RAG ingestion pipeline — digest scanned material into vector storage.

Data sources (what gets ingested):
  - Scanner results (token security reports)
  - Wallet labels and entities
  - News articles and market intelligence
  - Research documents and analysis
  - Agent conversation summaries
  - DeFi protocol documentation
  - Scam pattern documentation

Pipeline stages (in order):
  1. ``DocumentProcessor``  — chunk text into configurable segments with overlap
  2. ``QualityFilter``      — reject low-quality / spam / duplicate content
  3. ``RateLimiter``        — prevent system overload (max N docs per minute)
  4. ``BatchIngestor``      — accumulate and flush in configurable batches
  5. ``IngestionScheduler`` — schedule periodic ingestion sweeps

Integration points:
  - Scanner results: ``ingest_scan_result()`` — call after each scan completes
  - News webhook:    ``ingest_news_article()`` — exposed via pryscraper_bridge
  - Bulletin board:  ``ingest_research_post()`` — hook for bulletin board publishes

Usage:
    from app.rag.ingestion import get_ingestor

    ingestor = get_ingestor()
    result = await ingestor.ingest_document(
        content="Token 0x... flagged as honeypot...",
        collection="scanner",
        doc_id="scan:abc123",
        metadata={"source": "unified_scanner", "token": "0x..."},
    )
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.rag.config import (
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_INGESTION_BATCH_SIZE,
    RAG_INGESTION_MAX_PER_MINUTE,
    RAG_MIN_CHUNK_CHARS,
    RAG_MIN_DOC_LENGTH,
    RAG_SCHEDULER_INTERVAL,
    RAG_SPAM_REPETITION_THRESHOLD,
)

log = get_logger(__name__)

# ── Data types ───────────────────────────────────────────────────────

class IngestionStatus(StrEnum):
    """Status of an ingestion operation."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    PARTIAL = "partial"  # Some chunks succeeded, some failed

@dataclass
class Document:
    """A document ready for ingestion."""

    content: str
    collection: str = "scanner"
    doc_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_type: str = ""  # scanner, wallet, news, research, scam, agent, defi
    ingested_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

@dataclass
class Chunk:
    """A single chunk produced by DocumentProcessor."""

    text: str
    content_hash: str
    index: int
    quality: float = 1.0

@dataclass
class IngestReport:
    """Summary report for a batch ingestion."""

    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicate: int = 0
    rate_limited: int = 0
    failed: int = 0
    chunks_total: int = 0
    took_ms: int = 0
    errors: list[str] = field(default_factory=list)
    status: IngestionStatus = IngestionStatus.PENDING

# ── DocumentProcessor ───────────────────────────────────────────────

import contextlib

from app.rag.chunking import content_hash as _content_hash

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _quality_score(text: str, max_chunk: int) -> float:
    """Quick quality score 0-1. Penalise noise (high special-char ratio)."""
    if not text:
        return 0.0
    n = len(text)
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    length_score = min(1.0, n / max_chunk)
    special_score = max(0.0, 1.0 - (special / n))
    return round(0.7 * length_score + 0.3 * special_score, 3)

class DocumentProcessor:
    """Chunk raw text into overlapping segments for embedding.

    Strategy (matches ``app.rag.chunking.chunk_text``):
      1. Split on paragraph breaks → sentences → words.
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
            return [Chunk(text=text, content_hash=h, index=0, quality=_quality_score(text, self.max_chunk))]

        # Sentence-level split
        sentences = re.split(r"(?<=[.!?\n])\s+", text)
        raw_chunks: list[str] = []
        cur = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            # Hard-split long sentences on word boundaries
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
                    quality=_quality_score(c, self.max_chunk),
                )
            )
        return out

    def process_document(self, doc: Document) -> list[Chunk]:
        """Convenience: chunk a Document into chunks."""
        if not doc.content or len(doc.content.strip()) < RAG_MIN_DOC_LENGTH:
            log.debug("doc_too_short", doc_id=doc.doc_id, length=len(doc.content))
            return []
        return self.chunk_text(doc.content)

# ── QualityFilter ────────────────────────────────────────────────────

class QualityFilter:
    """Reject low-quality content before it reaches the embedding pipeline.

    Checks:
      - Minimum document length (too-short documents are noise)
      - Spam / repetitive patterns (high n-gram repetition ratio)
      - Exact duplicate detection (via content_hash against Redis)
    """

    def __init__(
        self,
        min_length: int = RAG_MIN_DOC_LENGTH,
        spam_threshold: float = RAG_SPAM_REPETITION_THRESHOLD,
    ) -> None:
        self.min_length = min_length
        self.spam_threshold = spam_threshold

    def check(self, doc: Document) -> tuple[bool, str]:
        """Return (is_accepted, reason) for a document."""
        content = doc.content.strip()

        # Length check
        if len(content) < self.min_length:
            return False, f"too_short:{len(content)}<{self.min_length}"

        # Spam check: detect repetitive n-grams
        if self._is_spam(content):
            return False, "spam_repetitive"

        # Duplicate check via Redis
        h = _content_hash(content)
        if self._is_duplicate(h, doc.collection):
            return False, "duplicate"

        return True, ""

    async def check_async(self, doc: Document) -> tuple[bool, str]:
        """Async variant (still sync checks, but follows the same interface pattern)."""
        return self.check(doc)

    def filter_batch(self, docs: list[Document]) -> tuple[list[Document], list[tuple[Document, str]]]:
        """Filter a batch of documents. Returns (accepted, rejected_with_reason)."""
        accepted: list[Document] = []
        rejected: list[tuple[Document, str]] = []
        for doc in docs:
            ok, reason = self.check(doc)
            if ok:
                accepted.append(doc)
            else:
                rejected.append((doc, reason))
        return accepted, rejected

    # ── Internal helpers ─────────────────────────────────────────

    @staticmethod
    def _is_spam(text: str, threshold: float = RAG_SPAM_REPETITION_THRESHOLD) -> bool:
        """Detect spam by checking trigram repetition ratio."""
        if len(text) < 100:
            return False
        words = text.lower().split()
        if len(words) < 10:
            return False
        # Count unique trigrams vs total trigrams
        trigrams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
        if not trigrams:
            return False
        unique = len(set(trigrams))
        ratio = 1.0 - (unique / len(trigrams))
        return ratio > threshold

    @staticmethod
    def _is_duplicate(content_hash: str, collection: str) -> bool:
        """Has this exact content already been ingested?"""
        try:
            from app.core.redis import get_redis

            r = get_redis()
            return bool(r.sismember(f"rag:hashes:{collection}", content_hash))
        except Exception as e:
            log.warning("dedup_check_failed: %s", e)
            return False

# ── RateLimiter ──────────────────────────────────────────────────────

_INGESTION_COUNTER_KEY = "rag:rate:ingestions"

class RateLimiter:
    """Token-bucket rate limiter using Redis.

    Prevents the embedding backend (Ollama / OpenRouter) from being
    overwhelmed during bulk backfills.  Tracks ingestions per minute.
    """

    def __init__(self, max_per_minute: int = RAG_INGESTION_MAX_PER_MINUTE) -> None:
        self.max_per_minute = max_per_minute

    async def check(self, count: int = 1) -> bool:
        """Can ``count`` ingestions proceed without exceeding the rate limit?"""
        try:
            from app.core.redis import get_redis

            r = get_redis()
            now = int(time.time())
            window_start = now - 60

            # Remove expired entries
            r.zremrangebyscore(_INGESTION_COUNTER_KEY, 0, window_start)

            # Count current window
            current = r.zcard(_INGESTION_COUNTER_KEY) or 0
            remaining = self.max_per_minute - current

            if remaining < count:
                log.warning("rate_limiter_blocked", current=current, max=self.max_per_minute)
                return False

            # Record these ingestions
            pipe = r.pipeline()
            for _ in range(count):
                pipe.zadd(_INGESTION_COUNTER_KEY, {f"{now}:{id(self)}:{_}:{hash(time.time())}": now})
            pipe.expire(_INGESTION_COUNTER_KEY, 120)  # TTL safety
            pipe.execute()

            return True

        except Exception as e:
            log.warning("rate_limiter_check_failed: %s", e)
            return True  # Allow on Redis failure (fail-open)

    async def remaining(self) -> int:
        """How many more ingestions are allowed this minute?"""
        try:
            from app.core.redis import get_redis

            r = get_redis()
            now = int(time.time())
            r.zremrangebyscore(_INGESTION_COUNTER_KEY, 0, now - 60)
            current = r.zcard(_INGESTION_COUNTER_KEY) or 0
            return max(0, self.max_per_minute - current)
        except Exception as e:
            log.debug("rate_limiter_remaining_failed: %s", e)
            return self.max_per_minute

    async def reset(self) -> None:
        """Reset the rate limit counter (for testing)."""
        try:
            from app.core.redis import get_redis

            r = get_redis()
            r.delete(_INGESTION_COUNTER_KEY)
        except Exception as e:
            log.debug("rate_limiter_reset_failed: %s", e)

# ── BatchIngestor ────────────────────────────────────────────────────

class BatchIngestor:
    """Accumulate documents and flush in batches of N.

    The core ingestion pipeline:
      1. Accept documents via ``ingest()`` or ``ingest_batch()``.
      2. Process through DocumentProcessor → QualityFilter → RateLimiter.
      3. Delegate to ``app.rag.engine.ingest_document`` for embedding + storage.
      4. Return an ``IngestReport`` with per-document status.
    """

    def __init__(
        self,
        batch_size: int = RAG_INGESTION_BATCH_SIZE,
        processor: DocumentProcessor | None = None,
        quality_filter: QualityFilter | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.batch_size = batch_size
        self._processor = processor or DocumentProcessor()
        self._filter = quality_filter or QualityFilter()
        self._rate_limiter = rate_limiter or RateLimiter()
        self._pending: list[Document] = []

    @property
    def pending_count(self) -> int:
        """Number of documents queued but not yet ingested."""
        return len(self._pending)

    async def ingest(
        self,
        content: str,
        collection: str = "scanner",
        doc_id: str = "",
        metadata: dict[str, Any] | None = None,
        source_type: str = "",
    ) -> IngestReport:
        """Ingest a single document immediately (bypasses batching).

        This runs the full pipeline synchronously for one document.
        """
        doc = Document(
            content=content,
            collection=collection,
            doc_id=doc_id or f"ingest:{int(time.time() * 1000)}:{hash(content) % 10**8}",
            metadata=metadata or {},
            source_type=source_type,
        )
        return await self.ingest_batch([doc])

    async def ingest_batch(self, docs: list[Document]) -> IngestReport:
        """Ingest a batch of documents through the full pipeline."""
        start = time.monotonic()
        report = IngestReport(total=len(docs))

        if not docs:
            return report

        # 1. Quality filter
        accepted, rejected = self._filter.filter_batch(docs)
        report.rejected = len(rejected)
        for doc, reason in rejected:
            log.info("doc_rejected", doc_id=doc.doc_id, reason=reason)

        if not accepted:
            report.status = IngestionStatus.REJECTED
            report.took_ms = int((time.monotonic() - start) * 1000)
            return report

        # 2. Rate limit check (estimate chunks per doc)
        estimated_ops = sum(max(1, len(d.content) // RAG_CHUNK_SIZE) for d in accepted)
        if not await self._rate_limiter.check(estimated_ops):
            report.rate_limited = len(accepted)
            report.status = IngestionStatus.RATE_LIMITED
            report.took_ms = int((time.monotonic() - start) * 1000)
            log.warning("batch_rate_limited", docs=len(accepted), estimated_ops=estimated_ops)
            return report

        # 3. Process and ingest each document
        from app.rag.engine import ingest_document as engine_ingest

        errors: list[str] = []
        accepted_count = 0
        total_chunks = 0

        for doc in accepted:
            try:
                result = await engine_ingest(
                    collection=doc.collection,
                    doc_id=doc.doc_id,
                    content=doc.content,
                    metadata=doc.metadata,
                    chunk=True,
                )
                if result["status"] == "ok":
                    accepted_count += 1
                    total_chunks += result.get("chunks", 0)
                elif result["status"] == "duplicate":
                    report.duplicate += 1
                else:
                    errors.append(f"{doc.doc_id}:{result['status']}")
            except Exception as e:
                log.warning("ingest_doc_failed", doc_id=doc.doc_id, error=str(e))
                errors.append(f"{doc.doc_id}:{e!s}")
                report.failed += 1

        report.accepted = accepted_count
        report.chunks_total = total_chunks
        report.errors = errors[:20]  # Cap error list

        if accepted_count == len(accepted):
            report.status = IngestionStatus.COMPLETED
        elif accepted_count > 0:
            report.status = IngestionStatus.PARTIAL
        else:
            report.status = IngestionStatus.FAILED

        report.took_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "batch_ingest_done",
            total=report.total,
            accepted=report.accepted,
            rejected=report.rejected,
            duplicate=report.duplicate,
            failed=report.failed,
            chunks=report.chunks_total,
            took_ms=report.took_ms,
        )
        return report

    async def queue(self, doc: Document) -> int:
        """Queue a document for batch ingestion. Returns queue size."""
        self._pending.append(doc)
        if len(self._pending) >= self.batch_size:
            await self.flush()
        return len(self._pending)

    async def queue_many(self, docs: list[Document]) -> int:
        """Queue multiple documents. Returns queue size after addition."""
        self._pending.extend(docs)
        if len(self._pending) >= self.batch_size:
            await self.flush()
        return len(self._pending)

    async def flush(self) -> IngestReport | None:
        """Flush queued documents through the pipeline."""
        if not self._pending:
            return None
        batch = self._pending[: self.batch_size]
        self._pending = self._pending[self.batch_size :]
        return await self.ingest_batch(batch)

    async def flush_all(self) -> list[IngestReport]:
        """Flush all remaining queued documents."""
        reports: list[IngestReport] = []
        while self._pending:
            report = await self.flush()
            if report:
                reports.append(report)
        return reports

# ── IngestionScheduler ──────────────────────────────────────────────

class IngestionScheduler:
    """Schedule periodic ingestion jobs.

    Runs at a configurable interval, flushing the batch ingestor's queue
    and triggering any collection-specific sweep handlers.
    """

    def __init__(
        self,
        ingestor: BatchIngestor | None = None,
        interval: int = RAG_SCHEDULER_INTERVAL,
    ) -> None:
        self._ingestor = ingestor or BatchIngestor()
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._handlers: list[Callable[[], Awaitable[None]]] = []

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def interval(self) -> int:
        return self._interval

    def register_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        """Register a sweep handler called on each tick."""
        self._handlers.append(handler)

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            log.warning("scheduler_already_running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        log.info("scheduler_started", interval=self._interval)

    async def stop(self) -> None:
        """Stop the scheduler loop gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        log.info("scheduler_stopped")

    async def tick(self) -> list[IngestReport]:
        """Run one scheduler cycle: flush queue + call handlers."""
        reports: list[IngestReport] = []

        # Flush queued documents
        batch_report = await self._ingestor.flush_all()
        if batch_report:
            reports.extend(batch_report)

        # Run sweep handlers
        for handler in self._handlers:
            try:
                await handler()
            except Exception as e:
                log.warning("scheduler_handler_failed", handler=handler.__name__, error=str(e))

        return reports

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                log.error("scheduler_tick_failed", error=str(e))
            await asyncio.sleep(self._interval)

# ── Source‑specific ingestion helpers ────────────────────────────────

async def ingest_scan_result(
    token_address: str,
    chain: str,
    safety_score: float,
    risk_flags: list[str],
    symbol: str = "",
    name: str = "",
    extra: dict[str, Any] | None = None,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    """Ingest a token scan result into the ``scanner`` collection.

    Called after each scan completes (see ``app.domains.scanner``).
    """
    metadata = {
        "token_address": token_address,
        "chain": chain,
        "safety_score": safety_score,
        "risk_flags": risk_flags,
        "symbol": symbol,
        "name": name,
        "source": "scanner",
        "scanned_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        metadata.update(extra)

    content = (
        f"Token: {token_address}\n"
        f"Chain: {chain}\n"
        f"Symbol: {symbol or '—'}\n"
        f"Name: {name or '—'}\n"
        f"Safety score: {safety_score}/100\n"
        f"Risk flags: {', '.join(risk_flags)}\n"
    )

    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="scanner",
        doc_id=f"scan:{chain}:{token_address}",
        metadata=metadata,
        source_type="scanner",
    )

async def ingest_wallet_profile(
    wallet_address: str,
    chain: str,
    labels: list[str],
    risk_score: float = 0.0,
    entities: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    """Ingest a wallet profile / label set into the ``wallet`` collection."""
    metadata = {
        "wallet_address": wallet_address,
        "chain": chain,
        "labels": labels,
        "risk_score": risk_score,
        "entities": entities or [],
        "source": "wallet_intel",
    }
    if extra:
        metadata.update(extra)

    content = (
        f"Wallet: {wallet_address}\n"
        f"Chain: {chain}\n"
        f"Labels: {', '.join(labels)}\n"
        f"Risk score: {risk_score}\n"
        f"Entities: {', '.join(entities or [])}\n"
    )

    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="wallet",
        doc_id=f"wallet:{chain}:{wallet_address}",
        metadata=metadata,
        source_type="wallet",
    )

async def ingest_news_article(
    title: str,
    body: str,
    source_url: str,
    source_name: str = "",
    published_at: str = "",
    categories: list[str] | None = None,
    sentiment: float = 0.0,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    """Ingest a news article into the ``news`` collection.

    Called from the pryscraper news webhook.
    """
    metadata = {
        "title": title,
        "source_url": source_url,
        "source_name": source_name or "",
        "published_at": published_at or datetime.now(UTC).isoformat(),
        "categories": categories or [],
        "sentiment": sentiment,
        "source": "news",
    }

    content = f"Title: {title}\n\n{body}" if title else body

    doc_id = f"news:{_content_hash(source_url + title)[:12]}"

    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="news",
        doc_id=doc_id,
        metadata=metadata,
        source_type="news",
    )

async def ingest_research_post(
    post_id: str,
    title: str,
    content: str,
    category: str = "",
    tags: list[str] | None = None,
    author: str = "",
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    """Ingest a bulletin board / research post into the ``research`` collection.

    Called when a research post is published (see ``app.bulletin_board``).
    """
    metadata = {
        "post_id": post_id,
        "title": title,
        "category": category,
        "tags": tags or [],
        "author": author,
        "source": "bulletin_board",
        "published_at": datetime.now(UTC).isoformat(),
    }

    full_content = f"Title: {title}\n\n{content}" if title else content

    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=full_content,
        collection="research",
        doc_id=f"research:{post_id}",
        metadata=metadata,
        source_type="research",
    )

async def ingest_scam_pattern(
    pattern_name: str,
    description: str,
    severity: str = "medium",
    indicators: list[str] | None = None,
    references: list[str] | None = None,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    """Ingest a scam pattern document into the ``scams`` collection."""
    metadata = {
        "pattern_name": pattern_name,
        "severity": severity,
        "indicators": indicators or [],
        "references": references or [],
        "source": "scam_patterns",
    }

    indicators_text = "\n".join(f"  - {i}" for i in (indicators or []))
    refs_text = "\n".join(f"  - {r}" for r in (references or []))
    content = (
        f"Pattern: {pattern_name}\n"
        f"Severity: {severity}\n"
        f"Description: {description}\n"
        f"Indicators:\n{indicators_text}\n"
        f"References:\n{refs_text}\n"
    )

    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="scams",
        doc_id=f"scam:{_content_hash(pattern_name)[:12]}",
        metadata=metadata,
        source_type="scam",
    )

# ── Convenience singleton ────────────────────────────────────────────

_default_processor = DocumentProcessor()
_default_filter = QualityFilter()
_default_limiter = RateLimiter()
_default_ingestor = BatchIngestor(
    processor=_default_processor,
    quality_filter=_default_filter,
    rate_limiter=_default_limiter,
)
_default_scheduler = IngestionScheduler(ingestor=_default_ingestor)

def get_processor() -> DocumentProcessor:
    """Get the default DocumentProcessor singleton."""
    return _default_processor

def get_filter() -> QualityFilter:
    """Get the default QualityFilter singleton."""
    return _default_filter

def get_rate_limiter() -> RateLimiter:
    """Get the default RateLimiter singleton."""
    return _default_limiter

def get_ingestor() -> BatchIngestor:
    """Get the default BatchIngestor singleton."""
    return _default_ingestor

def get_scheduler() -> IngestionScheduler:
    """Get the default IngestionScheduler singleton."""
    return _default_scheduler
