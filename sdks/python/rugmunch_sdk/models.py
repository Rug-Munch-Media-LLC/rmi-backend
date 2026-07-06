"""RMI SDK - Response models (ergonomic, not raw API shapes)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TokenRisk(BaseModel):
    score: int = 0
    tier: str = "low"
    factors: list[str] = Field(default_factory=list)
    token: dict | None = None
    deployer_reputation: int | None = None


class Report(BaseModel):
    report_id: str
    subject_type: str
    subject_id: str
    risk_score: int
    risk_tier: str
    markdown: str
    sections: dict[str, str] = Field(default_factory=dict)


class RagHit(BaseModel):
    """A RAG search hit. Maps from the API's `content` field to `text`."""

    doc_id: str
    score: float
    text: str = ""
    content: str = ""  # alias
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data):
        # Accept both `text` and `content` from the API
        if "content" in data and "text" not in data:
            data["text"] = data["content"]
        super().__init__(**data)


class NewsItem(BaseModel):
    news_id: str
    title: str
    source: str
    published_at: str
    summary: str = ""
    sentiment_score: float | None = None
    score: float | None = None


class McpTool(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
