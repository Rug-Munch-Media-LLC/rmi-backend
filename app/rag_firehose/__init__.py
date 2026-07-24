"""
RAG Firehose - Continuous Intelligence Ingestion Engine
========================================================
Self-feeding RAG pipeline that continuously pulls, filters, and ingests
crypto intelligence from multiple sources at different cadences.

Usage:
    from app.rag_firehose import get_firehose, FirehoseEngine
    from app.rag_firehose import IngestionPipeline, FeedSources
"""

from __future__ import annotations

from app.rag_firehose.engine import (
    FirehoseEngine,
    RagFirehose,
    get_firehose,
)
from app.rag_firehose.models import (
    COLLECTION_CAPS,
    HASH_TTL,
    MIN_QUALITY_SCORE,
    RAG_API,
    RATE_LIMITS,
    FeedSource,
    FirehoseStats,
)
from app.rag_firehose.pipeline import IngestionPipeline
from app.rag_firehose.sources import FeedSources

__all__ = [
    "COLLECTION_CAPS",
    "HASH_TTL",
    "MIN_QUALITY_SCORE",
    "RAG_API",
    "RATE_LIMITS",
    "FeedSource",
    "FeedSources",
    "FirehoseEngine",
    "FirehoseStats",
    "IngestionPipeline",
    "RagFirehose",
    "get_firehose",
]
