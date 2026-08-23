# ADR-0001: Canonical Token Safety Scoring

- Status: PROPOSED (migration queued)
- Date: 2026-08-23
- Context: SYSTEM_AUDIT_2026-08-23.md finding F3 / win #3

## Problem

Two scoring methodologies coexist and BOTH serve live traffic:

| Implementation | Pipeline | Method | Entry |
|---|---|---|---|
| `app/domains/scanner/unified/scoring.py::compute_score` | unified scanner (`unified_scanner_router`) | dimension-delta model (security/liquidity dims, max/delta/flags, blue-chip guard, explainable breakdown) | newer |
| `app/domains/scanner/service/scoring.py::_calculate_safety_score` | service scan (`service/scan.py`) | module-weight model (per-module reliability weights, baseline 35, compounding severe risks) | legacy |

**Product risk:** the same token can receive different scores depending on
which endpoint a consumer hits. For a trust product this is unacceptable at
scale and blocks publishing a single "our score" definition.

## Decision

1. **`unified/scoring.compute_score` is the target canonical.** Rationale:
   explainable per-dimension breakdown (score/max/delta/flags) matches the
   scanner-repo public contract (`TokenRatingResult.to_dict`), supports the
   confidence discipline, and is where active development happens.
2. `_calculate_safety_score` is **frozen**: bug-fixes only, no new signals.
   Its module docstring now carries a deprecation pointer to this ADR.
3. **Migration plan** (three gates, each gated by tests):
   - Gate A: characterization tests — record current outputs of BOTH scorers
     for a fixture corpus (50 tokens spanning clean/risky/degenerate).
   - Gate B: parity report — publish score deltas between methodologies on
     the corpus; product owner signs off on expected movements.
   - Gate C: switch `service/scan.py` to `compute_score`; keep
     `_calculate_safety_score` one release for rollback; then delete.
4. **Proliferation guard:** no third scoring implementation may be added.
   New signals extend `unified/scoring.py` dimensions.

## Consequences

- Short-term: two scorers remain until Gate C (tracked here, not silently).
- Consumers see one consistent score after migration.
- Score-history consumers must be notified of methodology change at Gate B
  sign-off (breaking-change notice in changelog).
