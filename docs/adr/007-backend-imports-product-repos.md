# ═══════════════════════════════════════════════════════════════
# ADR-007: Product Code Lives in Its Own Repo; Backend Imports It
# ═══════════════════════════════════════════════════════════════
# Date: 2026-08-30
# Status: Accepted

## Context
RugMunch ships several products (rugbrain RAG service, rugcharts, rugmaps,
degenfeed, etc.) alongside the `rmi-backend` FastAPI monolith. Historically,
product logic drifted into the backend (flat modules, duplicated shims) — the
`app/` root accumulated 191 flat modules while `app/integrations/` held a single
client. The result was import sprawl, no single ownership boundary, and fragile
cross-repo coupling (e.g. the `app.rag` re-export shims layered over the
extracted `rugbrain` package).

## Decision
**Product code lives in its own repository.** The backend imports it as a
dependency — it never vendors, forks, or mirrors product logic into `app/`.
Concretely:

- `rugbrain`, `rugcharts`, `rugmaps`, `degenfeed`, etc. are separate repos,
  installed as packages (or consumed via a thin client) and imported by the
  backend.
- The backend surfaces product capabilities through a narrow adapter boundary
  at `app/integrations/` (e.g. `app/integrations/rugbrain_client.py`) and typed
  dependencies in `app/core/dependencies.py`.
- During extraction, the backend may keep one-release **re-export shims** that
  alias `from app.<x> import *` to the product package. Shims are removed once
  downstream callers migrate; they are never a permanent home for product code.
- External data/AI provider connectors are treated the same way: they are
  leaf adapters consolidated under `app/integrations/`, not flat modules at
  `app/` root. The dynamic provider registry (`app/routers/v1/providers.py`)
  is the single source of truth for the module path of each provider.

## Alternatives Considered
1. **Vendor product code into `app/`** — rejected: duplicate ownership, no
   single canonical source, drift between the product repo and the backend.
2. **Mirror product repos under a RugMunch org** — rejected (see root
   AGENTS.md hard rule: never host third-party/product repositories as
   mirrors); consume as a dependency instead.
3. **Keep everything in one monolith forever** — rejected: products must
   release and scale independently.

## Consequences
- Backend imports are strict: `app/integrations/*` and product packages are
  bounded by the import-linter (`.importlinter`) + ruff archfit gates.
- Connectors and clients consolidate under `app/integrations/`; no new flat
  connector modules at `app/` root.
- Extraction of a product requires a shim → migrate callers → remove shim
  lifecycle, tracked in the product's extraction plan.
- Risk: a product package that fails to import breaks the backend; mitigated by
  optional imports + circuit-breaker registries (see ADR-001 DataBus).

---
*Owner: Rug Munch Media LLC Engineering · For questions: admin@rugmunch.io*
