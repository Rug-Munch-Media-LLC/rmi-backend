"""Structured RiskReport — deployer / holders / first-buyer / top-25 / fresh /
CEX-funded / bundle / cluster / honeypot / liquidity, per-factor explanation +
composite score + high-level rug/scam determination.

The unified scanner produces a rich ``ScanResult``: every phase (core security,
holder data, clever-wallet funding analysis, bundle detection, SENTINEL
deep-forensic modules) leaves its payload on the result. This package turns all
of that into ONE structured, human-readable risk report:

  * ``RiskFactor``  — one explainable factor (key, label, 0-100 risk score,
                      status ok|risk|critical|unknown, why-it's-risky text).
  * ``ClusterInfo`` — the beyond-market CLUSTER payload (funder tree,
                      shared-deployer network, cross-token cluster graph,
                      cross-token funder graph, temporal lockstep, sniper/bot
                      patterns, MEV bundles, wash-trading clusters).
  * ``RiskReport``  — the full report: deployer, holders, first buyer,
                      top-25 holders, fresh %, CEX-funded %, bundle, cluster,
                      honeypot, liquidity + ``factors`` (~25) + composite
                      score + determination (scam/rug/risky/ok) + deeper
                      analysis + data-quality metadata.
  * ``build_risk_report`` — pure aggregation from a ``ScanResult`` (plus the
                      optional injectable engine payloads). Never raises;
                      every unavailable factor is marked ``unknown``.
  * ``format_risk_report`` — the formatter: structured text output with
                      per-factor explanations and (on ``detailed=True``) the
                      expanded deeper-level analysis.
  * ``run_risk_report_phase`` — async scanner-phase wiring: resolves the
                      deployer, runs the deployer fingerprint / holder-cluster
                      / wallet-cluster engines (best-effort) and attaches
                      ``ScanResult.risk_report``.

Design rules
------------
- Pure builder: ``build_risk_report`` is synchronous and only reads already
  fetched data, so it is trivially testable with a synthetic ``ScanResult``
  and never touches the network / ClickHouse.
- Fail-open: every field extraction is defensive — a missing dict key leaves
  the factor at ``unknown`` instead of raising.
- The composite score is a weighted average over KNOWN factors only; the
  data-quality block reports how many factors were known so callers can
  discount a score built from thin data.

NOTE (degodding 2026-09-03): this package is the public API + a re-export shim.
The implementation is split by concern:

  - ``_model.py`` — the ``RiskFactor`` / ``ClusterInfo`` / ``RiskReport``
    dataclasses, shared thresholds, and factor weights / labels.
  - ``_extract.py`` — coercion helpers + per-section extraction.
  - ``_factors.py`` — the ~25 risk-factor builders.
  - ``_composite.py`` — composite score + determination.
  - ``_deeper.py`` — deeper-level expanded analysis.
  - ``_builder.py`` — ``build_risk_report`` (public).
  - ``_formatter.py`` — ``format_risk_report`` (public).
  - ``_phase.py`` — ``run_risk_report_phase`` (public).
"""

from __future__ import annotations

from app.domains.scanner.unified.risk_report._builder import build_risk_report
from app.domains.scanner.unified.risk_report._composite import _composite_and_determination
from app.domains.scanner.unified.risk_report._formatter import format_risk_report
from app.domains.scanner.unified.risk_report._model import (
    FACTOR_LABELS,
    FACTOR_WEIGHTS,
    ClusterInfo,
    RiskFactor,
    RiskReport,
)
from app.domains.scanner.unified.risk_report._phase import run_risk_report_phase

__all__ = [
    "FACTOR_LABELS",
    "FACTOR_WEIGHTS",
    "ClusterInfo",
    "RiskFactor",
    "RiskReport",
    "_composite_and_determination",
    "build_risk_report",
    "format_risk_report",
    "run_risk_report_phase",
]
