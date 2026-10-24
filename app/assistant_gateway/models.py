"""Pydantic contracts for the page assistant gateway (OpenAI-compatible)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGES = 40
MAX_CONTENT_CHARS = 8000
MAX_TOOL_HINTS = 10

_SAFE_ID = r"^[A-Za-z0-9:_\-]+$"


class ChatMessage(BaseModel):
    """One chat turn. Content is text-only by design (no multimodal surface)."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str = Field(default="", max_length=MAX_CONTENT_CHARS)
    name: str | None = Field(default=None, max_length=64)


class PageContext(BaseModel):
    """Compact page state from the frontend. Strict: unknown fields are rejected.

    Every value is display state and is treated as untrusted DATA by the prompt.
    """

    model_config = ConfigDict(extra="forbid")

    page: str | None = Field(default=None, max_length=32, pattern=r"^[a-z]+$")
    token: str | None = Field(default=None, max_length=128, pattern=_SAFE_ID)
    chain: str | None = Field(default=None, max_length=32, pattern=r"^[a-z0-9\-]+$")
    timeframe: str | None = Field(default=None, max_length=16, pattern=r"^[0-9a-zA-Z]+$")
    cluster: str | None = Field(default=None, max_length=128, pattern=_SAFE_ID)
    scan_ref: str | None = Field(default=None, max_length=128, pattern=_SAFE_ID)


class ChatCompletionRequest(BaseModel):
    """OpenAI-shaped request plus the page-assistant extensions."""

    model_config = ConfigDict(extra="ignore")

    model: str | None = Field(default=None, max_length=64)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    stream: bool = False
    alias: str | None = Field(default=None, max_length=32)
    page_context: PageContext | None = None
    tools: list[str] = Field(default_factory=list, max_length=MAX_TOOL_HINTS)
    max_steps: int = Field(default=3, ge=1, le=5)
    temperature: float = Field(default=0.2, ge=0.0, le=1.5)
    max_tokens: int = Field(default=700, ge=32, le=2000)

    @field_validator("tools", mode="before")
    @classmethod
    def _normalize_tool_hints(cls, value: Any) -> Any:
        """Accept OpenAI tool objects, bare names, or CSV — keep names only."""
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        names: list[str] = []
        for item in value:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                fn = item.get("function") if isinstance(item.get("function"), dict) else item
                name = fn.get("name") if isinstance(fn, dict) else None
                if isinstance(name, str):
                    names.append(name)
        return names


class Citation(BaseModel):
    """A retrieved source backing an answer."""

    title: str = ""
    url: str = ""
    snippet: str = ""
