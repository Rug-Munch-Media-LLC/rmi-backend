"""Response contract for icon resolution."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IconResolution(BaseModel):
    """Resolved icon for one token; ``icon_url`` is always usable."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(..., min_length=1, max_length=32)
    token: str = Field(..., min_length=1, max_length=160)
    icon_url: str = Field(..., min_length=1, max_length=2048)
    source: str = Field(..., min_length=1, max_length=40)
    cached: bool = False
    fallback: bool = False
    content_type: str | None = Field(None, max_length=64)
    expires_in: int = Field(0, ge=0)
