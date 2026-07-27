"""Scraped intelligence storage — data ingested from Pryscraper into the RMI ecosystem.

Stores blockchain intelligence, scam reports, threat reports, and other scraped
data so it can be consumed by the DataBus, scanner pipeline, RAG, and analytics.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.models import Base


class ScrapedIntel(Base):
    """Persistent storage for scraped intelligence from Pryscraper.

    Every scrape job publishes here. Downstream consumers (DataBus, RAG, scanners)
    read from this table to enrich token security, entity intel, and threat analysis.
    """

    __tablename__ = "scraped_intel"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Source of the data, e.g. "pryscraper"
    source = Column(String(64), nullable=False, index=True)
    # Data type classification: "scraped_intel", "scam_report", "threat_intel",
    # "blockchain_data", "entity_profile"
    data_type = Column(String(64), nullable=False, index=True)
    # The scraped content as JSONB for schema flexibility
    content = Column(JSONB, nullable=False, default=dict)
    # Optional URL the data was scraped from
    url = Column(Text, nullable=True)
    # Token address or entity address the data relates to (for DataBus lookup)
    token_address = Column(String(128), nullable=True, index=True)
    # Blockchain chain identifier (solana, ethereum, bsc, etc.)
    chain = Column(String(32), nullable=True, index=True)
    # When the data was scraped (sender's timestamp)
    scraped_at = Column(DateTime(timezone=True), nullable=True)
    # When we ingested it
    ingested_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    # Error message if ingestion partially failed
    error = Column(Text, nullable=True)

    # Composite index for efficient DataBus queries
    __table_args__ = (
        Index("ix_scraped_intel_lookup", "data_type", "token_address", "chain"),
        Index("ix_scraped_intel_source_type", "source", "data_type"),
    )
