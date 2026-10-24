"""Page-context dispatch: pick the builder, attach RAG citations, set status."""

from __future__ import annotations

from typing import Any

from app.api.v1.assistant.models import (
    PageContextResponse,
    PageName,
    PageParams,
    Section,
)
from app.api.v1.assistant.rag import citations_for
from app.api.v1.assistant.sources_rugcharts import build_rugcharts
from app.api.v1.assistant.sources_rugmaps_page import build_rugmaps
from app.api.v1.assistant.sources_scanner import build_scanner
from app.api.v1.assistant.sources_trades import build_trades

_BUILDERS = {
    "rugcharts": build_rugcharts,
    "rugmaps": build_rugmaps,
    "scanner": build_scanner,
    "trades": build_trades,
}


def _status(sections: dict[str, Section]) -> str:
    with_data = sum(1 for section in sections.values() if section.data is not None)
    if with_data == 0:
        return "unavailable"
    return "ok" if with_data == len(sections) else "partial"


def _subject(page: PageName, params: PageParams) -> dict[str, Any]:
    subject: dict[str, Any] = {"token": params.token, "chain": params.chain}
    if page == "rugcharts":
        subject["timeframe"] = params.timeframe
    if params.pair:
        subject["pair"] = params.pair
    if params.cluster:
        subject["cluster"] = params.cluster
    return subject


async def build_page_context(page: PageName, params: PageParams) -> PageContextResponse:
    """Build one page context; every section is grounded or null+reason."""
    sections = await _BUILDERS[page](params)
    citations = await citations_for(f"{page} {params.chain} {params.token} scam risk context", limit=3)
    return PageContextResponse(
        page=page,
        status=_status(sections),  # type: ignore[arg-type]
        subject=_subject(page, params),
        sections=sections,
        citations=citations,
    )
