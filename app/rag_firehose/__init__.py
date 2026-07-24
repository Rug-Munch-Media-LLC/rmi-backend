"""
RAG Firehose - Continuous Intelligence Ingestion Engine
========================================================
Self-feeding RAG pipeline that continuously pulls, filters, and ingests
crypto intelligence from multiple sources at different cadences.

Usage:
    from app.rag_firehose import get_firehose, FirehoseEngine
    from app.rag_firehose import IngestionPipeline, FeedSources
"""

from app.rag_firehose.engine import (
    FirehoseEngine,
    RagFirehose,
    get_firehose,
)
from app.rag_firehose.models import (
    COLLECTION_CAPS,
    FeedSource,
    FirehoseStats,
    HASH_TTL,
    MIN_QUALITY_SCORE,
    RAG_API,
    RATE_LIMITS,
)
from app.rag_firehose.pipeline import IngestionPipeline
from app.rag_firehose.sources import FeedSources

__all__ = [
    "COLLECTION_CAPS",
    "FeedSource",
    "FeedSources",
    "FirehoseEngine",
    "FirehoseStats",
    "HASH_TTL",
    "IngestionPipeline",
    "MIN_QUALITY_SCORE",
    "RAG_API",
    "RATE_LIMITS",
    "RagFirehose",
    "get_firehose",
]
