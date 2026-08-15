"""Pydantic v2 request/response models for the OSINT API.

These mirror the engine's report shapes (:mod:`app.osint` and
:mod:`app.osint.engine`) so the OpenAPI schema types the responses precisely —
the frontend's generated ``schema.d.ts`` can then drop its
``Record<string, unknown>`` cast for the OSINT endpoints.

Shapes (contract with :mod:`app.osint.engine`):

    * per-module report  -> :class:`OsintReportModel`
      ``{target, target_type, findings, errors}``
      (``OsintReport.to_dict()``)
    * engine output      -> :class:`AggregateReportModel`
      ``{target, target_type, results, findings, errors}``
      (both ``run_osint`` and ``run_aggregate`` emit this shape; ``run_osint``
      omits the flat ``findings`` list, which defaults to ``[]``)
    * POST body          -> :class:`AggregateRequest`
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OsintFindingModel(BaseModel):
    """A single resolved fact about the target (mirrors ``OsintFinding``)."""

    source: str
    category: str
    summary: str
    confidence: str = "medium"  # high | medium | low
    data: dict[str, Any] = Field(default_factory=dict)


class OsintReportModel(BaseModel):
    """Aggregated result for one target (mirrors ``OsintReport.to_dict()``)."""

    target: str
    target_type: str
    findings: list[OsintFindingModel] = Field(default_factory=list[OsintFindingModel])
    errors: list[str] = Field(default_factory=list)


class AggregateReportModel(BaseModel):
    """Combined engine output — per-module results plus flat findings/errors.

    ``findings`` defaults to ``[]`` so the ``run_osint`` shape (which omits the
    flat list) validates against the same model as ``run_aggregate``.

    ``cached`` is an API-level flag (not part of the engine report): it is set
    to ``True`` when the response was served from the Redis OSINT cache.
    """

    target: str
    target_type: str
    results: dict[str, OsintReportModel] = Field(default_factory=dict)
    findings: list[OsintFindingModel] = Field(default_factory=list[OsintFindingModel])
    errors: list[str] = Field(default_factory=list)
    cached: bool = False


class AggregateRequest(BaseModel):
    """POST /api/osint/aggregate body."""

    target: str
    target_type: str | None = None
